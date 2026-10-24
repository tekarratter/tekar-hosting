import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..config import settings
from ..core.audit import audit
from ..core.deps import DB, CurrentUser, StaffUser, app_role, check_app, visible_app_ids
from ..core.jobs import enqueue
from ..core.security import decrypt, encrypt, random_password
from ..models import App, AppMember, AppTemplate, Backup, Job, ProxyRoute, ServiceBinding, User
from ..services import compose, devbox, docker_svc, git
from ..services.templates import TEMPLATES, get_template

router = APIRouter(prefix="/api/apps", tags=["apps"])
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def app_out(a: App, role: str | None = None) -> dict[str, Any]:
    return {
        "id": a.id, "slug": a.slug, "name": a.name, "description": a.description, "kind": a.kind,
        "compose_project": a.compose_project, "work_dir": a.work_dir, "compose_files": a.compose_files,
        "status": a.status, "cpu_limit": a.cpu_limit, "mem_limit_mb": a.mem_limit_mb, "disk_quota_mb": a.disk_quota_mb,
        "disk_usage_mb": a.disk_usage_mb, "service_limits": a.service_limits or {}, "edge_services": a.edge_services or [],
        "backup_cron": a.backup_cron, "backup_retention": a.backup_retention, "backup_target_id": a.backup_target_id,
        "backup_stop_app": a.backup_stop_app, "backup_include_binds": a.backup_include_binds,
        "auto_update": a.auto_update, "template": a.template, "owner_id": a.owner_id,
        "has_deploy_token": bool(a.deploy_token), "volumes_dir": a.volumes_dir, "data_dir": a.data_dir,
        "backup_destination_id": a.backup_destination_id, "backup_keep_local": a.backup_keep_local, "converted_from": a.converted_from, "created_at": a.created_at, "updated_at": a.updated_at,
        "last_deployed_at": a.last_deployed_at, "my_role": role, "git": a.git,
    }


async def _containers_by_project() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in await docker_svc.list_containers():
        if c["project"]:
            out.setdefault(c["project"], []).append(c)
    return out


@router.get("")
async def list_apps(user: CurrentUser, db: DB):
    ids = await visible_app_ids(user, db)
    q = select(App).order_by(App.name)
    if ids is not None:
        q = q.where(App.id.in_(ids or [-1]))
    apps = (await db.execute(q)).scalars().all()
    by_proj = await _containers_by_project()
    out = []
    for a in apps:
        cs = by_proj.get(a.compose_project, [])
        running = sum(1 for c in cs if c["state"] == "running")
        status = "stopped" if not cs else "running" if running == len(cs) else "partial" if running else "stopped"
        d = app_out(a, await app_role(user, a, db))
        d.update(status=status, containers=len(cs), running=running)
        out.append(d)
    return out


def _tpl_out(t: AppTemplate, user: User) -> dict[str, Any]:
    return {"id": f"custom:{t.id}", "name": t.name, "description": t.description, "services": t.services or [], "web": t.web,
            "compose": t.compose, "env": t.env, "custom": True, "shared": t.shared,
            "can_delete": user.role == "admin" or t.owner_id == user.id}


def _visible_templates(user: User):
    q = select(AppTemplate).order_by(AppTemplate.name)
    if user.role != "admin":
        q = q.where((AppTemplate.owner_id == user.id) | AppTemplate.shared.is_(True))
    return q


async def find_template(db, user: User, tid: str | None) -> dict[str, Any] | None:
    if not tid:
        return None
    if tid.startswith("custom:"):
        t = (await db.execute(_visible_templates(user).where(AppTemplate.id == int(tid.split(":", 1)[1] or 0)))).scalar_one_or_none()
        return _tpl_out(t, user) if t else None
    return get_template(tid)


@router.get("/templates")
async def templates(user: CurrentUser, db: DB):
    builtin = [{**{k: t[k] for k in ("id", "name", "description", "services", "web", "compose", "env")}, "custom": False} for t in TEMPLATES]
    return builtin + [_tpl_out(t, user) for t in (await db.execute(_visible_templates(user))).scalars()]


class TemplateIn(BaseModel):
    name: str
    description: str = ""
    compose: str
    env: str = ""
    services: list[str] = Field(default_factory=list)
    web: dict[str, Any] | None = None
    shared: bool = False


@router.post("/templates")
async def create_template(body: TemplateIn, user: CurrentUser, db: DB, request: Request):
    if user.role == "member":
        raise HTTPException(403, "Member không tạo được template")
    try:
        compose.validate_yaml(body.compose)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    web = body.web if body.web and body.web.get("service") and body.web.get("port") else None
    t = AppTemplate(name=body.name.strip()[:100] or "Template", description=body.description, compose=body.compose, env=body.env,
                    services=[s for s in body.services if s in ("postgres", "redis", "s3")], web=web,
                    shared=body.shared and user.role in ("admin", "operator"), owner_id=user.id)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    await audit(db, user, "template.create", t.name, {"shared": t.shared}, request)
    return _tpl_out(t, user)


@router.delete("/templates/{tid}")
async def delete_template(tid: int, user: CurrentUser, db: DB, request: Request):
    t = await db.get(AppTemplate, tid)
    if t is None:
        raise HTTPException(404, "Không thấy template")
    if user.role != "admin" and t.owner_id != user.id:
        raise HTTPException(403, "Chỉ người tạo hoặc admin xoá được")
    await db.delete(t)
    await db.commit()
    await audit(db, user, "template.delete", t.name, request=request)
    return {"ok": True}


@router.get("/discover")
async def discover(user: StaffUser, db: DB):
    """Các dự án compose đang chạy trên host chưa được quản lý."""
    managed = set((await db.execute(select(App.compose_project))).scalars().all())
    projects = await docker_svc.compose_projects()
    out = []
    for p in projects.values():
        if p["project"] in managed or p["project"] == "tekar-hosting":
            continue
        host_wd = await docker_svc.translate_path(p["work_dir"] or "")
        p["host_work_dir"] = host_wd
        p["accessible"] = bool(host_wd)
        p["managed_by"] = _manager_hint(p["work_dir"] or "")
        out.append(p)
    standalone = [c for c in await docker_svc.list_containers() if not c["project"] and not c["name"].startswith("th-")]
    return {"projects": sorted(out, key=lambda x: x["project"]), "standalone": standalone}


def _manager_hint(work_dir: str) -> str | None:
    if work_dir.startswith("/app/data/stacks"):
        return "dockhand"
    if work_dir.startswith("/data/compose/"):
        return "portainer"
    return None


class AdoptIn(BaseModel):
    project: str
    name: str | None = None
    slug: str | None = None


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")
    return s[:40] or "app"


@router.post("/adopt")
async def adopt(body: AdoptIn, user: StaffUser, db: DB, request: Request):
    projects = await docker_svc.compose_projects()
    p = projects.get(body.project)
    if not p:
        raise HTTPException(404, "Không thấy project")
    if (await db.execute(select(App).where(App.compose_project == body.project))).scalar_one_or_none():
        raise HTTPException(400, "Project đã được quản lý")
    slug = _slugify(body.slug or body.project)
    if (await db.execute(select(App).where(App.slug == slug))).scalar_one_or_none():
        slug = f"{slug}-{secrets.token_hex(2)}"
    work_dir = await docker_svc.translate_path(p["work_dir"] or "") or (p["work_dir"] or "")
    files = [await docker_svc.translate_path(f) or f for f in p["config_files"]]
    app = App(
        slug=slug, name=body.name or body.project, kind="adopted", compose_project=body.project,
        work_dir=work_dir, compose_files=files, owner_id=user.id, status="running",
        description=f"Đang được quản lý bởi {_manager_hint(p['work_dir'] or '')}; deploy/sửa compose thực hiện ở công cụ đó"
        if _manager_hint(p["work_dir"] or "") and not os.path.exists(work_dir) else "",
    )
    db.add(app)
    await db.commit()
    await audit(db, user, "app.adopt", slug, {"project": body.project}, request)
    return app_out(app, "owner")


class CreateIn(BaseModel):
    name: str
    slug: str
    description: str = ""
    compose_yaml: str = ""
    env: str = ""
    template: str | None = None
    template_vars: dict[str, str] = Field(default_factory=dict)
    services: list[dict[str, Any]] = Field(default_factory=list)  # [{"service_id": 1, "env_prefix": ""}]
    domain: str | None = None
    web_service: str | None = None  # service + port nhận traffic từ domain (template có sẵn thì lấy theo template)
    web_port: int | None = None
    git: dict[str, Any] | None = None  # {repo, branch, credential_id, subdir, compose_path, sync_compose, auto_pull}
    devbox: dict[str, Any] | None = None  # template "devbox": {user, password, keys, sudo}
    deploy: bool = True


def _fill(text: str, vars_: dict[str, str]) -> str:
    def rep(m: re.Match) -> str:
        k = m.group(1)
        if k == "random":
            return random_password(24)
        v = vars_.get(k, "")
        return random_password(24) if v.strip() == "${random}" else v  # ô để "${random}" → sinh ngẫu nhiên; bỏ trống → rỗng

    return re.sub(r"\$\{(\w+)\}", rep, text)


@router.post("")
async def create_app(body: CreateIn, user: CurrentUser, db: DB, request: Request):
    from .platform import bind_service

    if user.role == "member":
        raise HTTPException(403, "Member không được tạo app; liên hệ admin")
    if not SLUG_RE.match(body.slug):
        raise HTTPException(400, "Slug chỉ gồm a-z, 0-9, '-' (2-41 ký tự)")
    if (await db.execute(select(App).where((App.slug == body.slug) | (App.compose_project == f"th-{body.slug}")))).scalar_one_or_none():
        raise HTTPException(400, "Slug đã tồn tại")
    compose_yaml, env = body.compose_yaml, body.env
    dev_keys = None
    if body.template == devbox.TEMPLATE_ID:  # môi trường phát triển: user SSH + thư mục làm việc
        d = body.devbox or {}
        try:
            dev_user = devbox.check_user(d.get("user") or "")
            dev_pw = devbox.check_password(d.get("password") or "")
            dev_keys = devbox.clean_keys(d.get("keys") or "")
            if not dev_pw and not dev_keys:
                raise ValueError("Cần mật khẩu hoặc ít nhất một public key để đăng nhập SSH")
            port = await devbox.alloc_port(db)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        from ..core.store import storage_path as _sp

        owner = compose.storage_owner(Path(await _sp("apps", db)))
        compose_yaml = devbox.COMPOSE
        env = devbox.build_env(dev_user, dev_pw, bool(d.get("sudo")), owner[0] if owner else 1000, port) + (env or "")
        tpl = None
    else:
        tpl = await find_template(db, user, body.template)
        if body.template and not tpl:
            raise HTTPException(404, "Không thấy template")
    git_cfg = None
    if body.git and body.git.get("repo"):
        from .git import get_cred

        cred = await get_cred(db, user, body.git.get("credential_id"))
        try:
            url = git.normalize_repo(body.git["repo"], cred.host if cred else "github.com")
        except git.GitError as e:
            raise HTTPException(400, str(e)) from e
        git_cfg = {"repo": url, "branch": body.git.get("branch") or None, "credential_id": cred.id if cred else None,
                   "subdir": (body.git.get("subdir") or "").strip("/"), "compose_path": body.git.get("compose_path") or None,
                   "sync_compose": bool(body.git.get("compose_path") and body.git.get("sync_compose")),
                   "auto_pull": bool(body.git.get("auto_pull"))}
    if tpl:
        compose_yaml = compose_yaml or tpl["compose"]
        env = (env + "\n" if env else "") + _fill(tpl["env"], body.template_vars)
    if body.domain:
        env += f"\nDOMAIN={body.domain}\n"
    try:
        compose.validate_yaml(compose_yaml)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    from ..core.store import get_setting, storage_path

    apps_root = await storage_path("apps", db)
    work_dir = Path(apps_root) / body.slug
    if work_dir.exists() and any(work_dir.iterdir()):
        raise HTTPException(400, f"Thư mục {work_dir} đã tồn tại và có dữ liệu")
    vol_root, data_root = await storage_path("volumes", db), await storage_path("data", db)
    app = App(
        slug=body.slug, name=body.name, description=body.description, kind="managed",
        compose_project=f"th-{body.slug}", work_dir=str(work_dir),
        compose_yaml=compose_yaml, env_enc=encrypt(env.strip() + "\n"), owner_id=user.id, template=body.template,
        status="created",
        volumes_dir=str(Path(vol_root) / body.slug) if vol_root else None,
        data_dir=str(Path(data_root) / body.slug) if data_root else None,
        backup_destination_id=await get_setting("default_backup_destination_id", db),
        git=git_cfg,
    )
    web = (tpl or {}).get("web") or ({"service": body.web_service, "port": body.web_port} if body.web_service and body.web_port else None)
    if web:
        app.edge_services = [web["service"]]
    db.add(app)
    await db.commit()
    await db.refresh(app)
    compose.write_managed_files(app)  # file có trên đĩa ngay, kể cả khi chưa deploy
    if dev_keys is not None:
        devbox.prepare_dir(app, dev_keys)

    wanted = body.services
    if tpl and not wanted and tpl.get("services"):  # gọi qua API/MCP không chọn dịch vụ → lấy dịch vụ đầu tiên đúng loại
        from ..models import PlatformService

        svcs = (await db.execute(select(PlatformService).order_by(PlatformService.id))).scalars().all()
        first = {}
        for s in svcs:
            first.setdefault(s.kind, s.id)
        wanted = [{"service_id": first[k]} for k in tpl["services"] if k in first]
    for s in wanted:
        await bind_service(db, app, int(s["service_id"]), s.get("env_prefix", ""), user)

    if body.domain and web:
        await _add_route(db, app, body.domain, web)

    await audit(db, user, "app.create", app.slug, {"template": body.template, "git": (git_cfg or {}).get("repo")}, request)
    if git_cfg:  # clone code trước (kể cả khi chưa deploy), compose đã chọn ở wizard giữ nguyên
        job = await enqueue(db, "git_update", {"app_id": app.id, "deploy": body.deploy, "initial": True}, app.id, user.id)
    else:
        job = await enqueue(db, "deploy", {"app_id": app.id}, app.id, user.id) if body.deploy else None
    return {**app_out(app, "owner"), "job_id": job.id if job else None}


def _upstream_host(app: App, service: str) -> str:
    """Tên container của service trên mạng edge: container_name nếu compose đặt, mặc định <project>-<service>-1."""
    import yaml

    try:
        svc = ((yaml.safe_load(app.compose_yaml or "") or {}).get("services") or {}).get(service) or {}
    except yaml.YAMLError:
        svc = {}
    name = svc.get("container_name")
    return name if isinstance(name, str) and name and "$" not in name else f"{app.compose_project}-{service}-1"


async def _add_route(db, app: App, domain: str, web: dict[str, Any]) -> None:
    db.add(ProxyRoute(app_id=app.id, hosts=[domain.strip().lower()],
                      upstreams=[f"{_upstream_host(app, web['service'])}:{web['port']}"],
                      source="template", cf_tunnel=True, tls_mode="tunnel"))
    await db.commit()
    from .proxy import apply_all

    await apply_all(db)


def _web_from_routes(app: App, routes: list[ProxyRoute]) -> dict[str, Any] | None:
    """Upstream "<project>-<service>-1:<port>" của route đầu tiên → {service, port}."""
    for r in routes:
        for u in r.upstreams or []:
            m = re.fullmatch(re.escape(app.compose_project) + r"[-_](.+)[-_]\d+:(\d+)", u)
            if m:
                return {"service": m.group(1), "port": int(m.group(2))}
    return None


def _source_compose(app: App) -> tuple[str, str, str | None]:
    """(compose, env, thư mục gốc để chép file) của app nguồn. Adopted nhiều file compose → gộp bằng `docker compose config`."""
    if app.kind == "managed":
        compose.sync_from_disk(app)
        return app.compose_yaml, decrypt(app.env_enc), app.work_dir
    if not compose.files_accessible(app):
        raise HTTPException(400, "Không đọc được compose của app này (Dockhand/Portainer quản lý) — chuyển stack sang panel trước rồi clone")
    files = [f for f in app.compose_files if f]
    env_path = Path(app.work_dir) / ".env"
    env = env_path.read_text() if env_path.is_file() else ""
    if len(files) == 1:
        return Path(files[0]).read_text(), env, str(Path(files[0]).parent)
    raise HTTPException(400, "App dùng nhiều file compose — clone chưa hỗ trợ, hãy chuyển stack sang panel trước")


class CloneIn(BaseModel):
    name: str
    slug: str
    copy_files: bool = True  # chép thư mục app (file cấu hình, code, thư mục bind tương đối)
    copy_data: bool = False  # chép dữ liệu volume, APP_DATA_DIR, database/redis/s3 của dịch vụ nền tảng
    stop_source: bool = False  # tạm dừng app gốc khi chép dữ liệu (nhất quán hơn)
    services: bool = True  # cấp tài nguyên mới trên cùng dịch vụ nền tảng (db/user/bucket riêng)
    strip_ports: bool = True  # bỏ cổng publish ra host (tránh trùng cổng với app gốc)
    domain: str | None = None
    web_service: str | None = None
    web_port: int | None = None
    deploy: bool = True


@router.get("/{app_id}/clone-info")
async def clone_info(app_id: int, user: CurrentUser, db: DB):
    """Gợi ý cho form clone: service, cổng publish, container_name, bind tuyệt đối dùng chung, dịch vụ nền tảng."""
    import yaml

    app = await check_app(user, app_id, db, "deployer")
    text, _, root = _source_compose(app)
    doc = yaml.safe_load(text) or {}
    ports, names, shared = [], [], []
    for name, svc in (doc.get("services") or {}).items():
        svc = svc or {}
        if svc.get("ports"):
            ports.append({"service": name, "ports": [str(p) if not isinstance(p, dict) else f"{p.get('published')}:{p.get('target')}" for p in svc["ports"]]})
        if svc.get("container_name"):
            names.append(name)
        for v in svc.get("volumes") or []:
            src = v.split(":", 1)[0] if isinstance(v, str) else (v.get("source") if v.get("type") == "bind" else None)
            if src and src.startswith("/") and not (root and src.startswith(root.rstrip("/") + "/")):
                shared.append({"service": name, "path": src})
    routes = (await db.execute(select(ProxyRoute).where(ProxyRoute.app_id == app.id))).scalars().all()
    bindings = (await db.execute(select(ServiceBinding).where(ServiceBinding.app_id == app.id))).scalars().all()
    return {
        "name": app.name, "slug": app.slug, "services": list((doc.get("services") or {}).keys()), "ports": ports, "container_names": names, "shared_binds": shared,
        "web": _web_from_routes(app, routes), "bindings": [{"kind": b.service.kind, "service": b.service.name, "resource": b.resource} for b in bindings],
        "volumes": [k for k, v in (doc.get("volumes") or {}).items() if not (v or {}).get("external")],
        "git": bool((app.git or {}).get("repo")), "data_dir": app.data_dir,
    }


@router.post("/{app_id}/clone")
async def clone_app(app_id: int, body: CloneIn, user: CurrentUser, db: DB, request: Request):
    """Tạo app managed mới từ app đang chạy: cùng compose/env, thư mục, (tuỳ chọn) dữ liệu và tài nguyên nền tảng riêng."""
    import yaml

    from .platform import bind_service

    if user.role == "member":
        raise HTTPException(403, "Member không được tạo app; liên hệ admin")
    src = await check_app(user, app_id, db, "deployer")
    if not SLUG_RE.match(body.slug):
        raise HTTPException(400, "Slug chỉ gồm a-z, 0-9, '-' (2-41 ký tự)")
    if (await db.execute(select(App).where((App.slug == body.slug) | (App.compose_project == f"th-{body.slug}")))).scalar_one_or_none():
        raise HTTPException(400, "Slug đã tồn tại")
    text, env, copy_root = _source_compose(src)
    if body.domain:  # DOMAIN của app gốc nằm trong phần env người dùng → thay bằng domain mới
        dom = body.domain.strip().lower()
        env, n = re.subn(r"(?m)^DOMAIN=.*$", f"DOMAIN={dom}", env)
        if not n:
            env = env.rstrip("\n") + f"\nDOMAIN={dom}\n"
    if devbox.is_devbox(src):  # cổng SSH riêng cho bản sao (ports giữ nguyên vì dùng ${SSH_PORT})
        try:
            env = devbox.env_set(env, "SSH_PORT", str(await devbox.alloc_port(db)))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    doc = yaml.safe_load(text) or {}
    for svc in (doc.get("services") or {}).values():
        if isinstance(svc, dict):
            svc.pop("container_name", None)  # trùng tên với app gốc
            if body.strip_ports and not devbox.is_devbox(src):
                svc.pop("ports", None)
    compose_yaml = f"# Clone từ {src.slug}\n" + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    try:
        compose.validate_yaml(compose_yaml)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    from ..core.store import get_setting, storage_path

    apps_root = await storage_path("apps", db)
    work_dir = Path(apps_root) / body.slug
    if work_dir.exists() and any(work_dir.iterdir()):
        raise HTTPException(400, f"Thư mục {work_dir} đã tồn tại và có dữ liệu")
    vol_root, data_root = await storage_path("volumes", db), await storage_path("data", db)
    g = dict(src.git) if (src.git or {}).get("repo") else None
    if g:
        g.update(auto_pull=False, error=None)
    app = App(
        slug=body.slug, name=body.name, description=f"Clone từ {src.name}", kind="managed",
        compose_project=f"th-{body.slug}", work_dir=str(work_dir), compose_yaml=compose_yaml, env_enc=encrypt(env.strip() + "\n"),
        owner_id=user.id, template=src.template, status="created", git=g,
        service_limits=dict(src.service_limits or {}), cpu_limit=src.cpu_limit, mem_limit_mb=src.mem_limit_mb,
        edge_services=list(src.edge_services or []),
        # app gốc có thư mục volume/data (compose dùng ${APP_DATA_DIR}) → bản sao luôn có thư mục riêng cạnh đó
        volumes_dir=str(Path(vol_root) / body.slug) if vol_root else str(Path(src.volumes_dir).parent / body.slug) if src.volumes_dir else None,
        data_dir=str(Path(data_root) / body.slug) if data_root else str(Path(src.data_dir).parent / body.slug) if src.data_dir else None,
        backup_destination_id=await get_setting("default_backup_destination_id", db),
    )
    web = {"service": body.web_service, "port": body.web_port} if body.web_service and body.web_port else None
    if web and web["service"] not in app.edge_services:
        app.edge_services = [*app.edge_services, web["service"]]
    db.add(app)
    await db.commit()
    await db.refresh(app)
    compose.write_managed_files(app)

    mapping: dict[int, int] = {}
    if body.services:
        for b in (await db.execute(select(ServiceBinding).where(ServiceBinding.app_id == src.id))).scalars().all():
            nb = await bind_service(db, app, b.service_id, b.env_prefix, user)
            mapping[b.id] = nb.id
    if body.domain and web:
        await _add_route(db, app, body.domain, web)
    params = {"source_id": src.id, "app_id": app.id, "copy_files": body.copy_files, "copy_root": copy_root,
              "copy_data": body.copy_data, "stop_source": body.stop_source and body.copy_data,
              "bindings": {str(k): v for k, v in mapping.items()}, "deploy": body.deploy}
    job = await enqueue(db, "app_clone", params, app.id, user.id)
    await audit(db, user, "app.clone", app.slug, {"source": src.slug, **{k: params[k] for k in ("copy_files", "copy_data")}}, request)
    return {**app_out(app, "owner"), "job_id": job.id}


class SaveTemplateIn(BaseModel):
    name: str
    description: str = ""
    blank_env: bool = True  # bỏ giá trị biến: KEY=${KEY}, người tạo app nhập lại
    shared: bool = False


@router.post("/{app_id}/save-template")
async def save_as_template(app_id: int, body: SaveTemplateIn, user: CurrentUser, db: DB, request: Request):
    app = await check_app(user, app_id, db, "deployer")
    text, env, _ = _source_compose(app)
    if body.blank_env:
        lines = []
        for line in env.splitlines():
            k, sep, _v = line.partition("=")
            lines.append(f"{k}=${{{k.strip()}}}" if sep and not line.lstrip().startswith("#") and k.strip().isidentifier() else line)
        env = "\n".join(lines) + "\n" if lines else ""
    routes = (await db.execute(select(ProxyRoute).where(ProxyRoute.app_id == app.id))).scalars().all()
    kinds = sorted({b.service.kind for b in (await db.execute(select(ServiceBinding).where(ServiceBinding.app_id == app.id))).scalars()})
    return await create_template(TemplateIn(name=body.name, description=body.description or f"Từ app {app.name}", compose=text, env=env,
                                            services=kinds, web=_web_from_routes(app, routes), shared=body.shared), user, db, request)


@router.get("/{app_id}")
async def get_app(app_id: int, user: CurrentUser, db: DB):
    app = await check_app(user, app_id, db)
    if compose.sync_from_disk(app):  # người dùng sửa file trực tiếp trên đĩa
        await db.commit()
    role = await app_role(user, app, db)
    d = app_out(app, role)
    cs = await docker_svc.project_containers(app.compose_project)
    d["containers"] = [docker_svc.summarize(c) for c in cs]
    d["available_services"] = sorted({(c.labels or {}).get(docker_svc.COMPOSE_SERVICE) for c in cs} - {None})
    d["compose_accessible"] = compose.files_accessible(app)
    if role in ("owner", "deployer"):
        if app.kind == "managed":
            d["compose_yaml"] = app.compose_yaml
            d["env"] = decrypt(app.env_enc)
        else:
            files = {}
            for f in app.compose_files:
                try:
                    files[f] = open(f).read()
                except OSError as e:
                    files[f] = f"# không đọc được: {e}"
            d["compose_sources"] = files
    return d


class UpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    compose_yaml: str | None = None
    env: str | None = None
    backup_cron: str | None = None
    backup_retention: int | None = None
    backup_target_id: int | None = None
    backup_stop_app: bool | None = None
    backup_include_binds: bool | None = None
    auto_update: bool | None = None
    owner_id: int | None = None
    backup_destination_id: int | None = None
    backup_keep_local: bool | None = None


@router.put("/{app_id}")
async def update_app(app_id: int, body: UpdateIn, user: CurrentUser, db: DB, request: Request):
    app = await check_app(user, app_id, db, "deployer")
    data = body.model_dump(exclude_unset=True)
    if "compose_yaml" in data:
        if app.kind != "managed":
            raise HTTPException(400, "App adopted: sửa compose trực tiếp trong thư mục dự án")
        try:
            compose.validate_yaml(data["compose_yaml"])
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    if "backup_cron" in data and data["backup_cron"]:
        from croniter import croniter

        if not croniter.is_valid(data["backup_cron"]):
            raise HTTPException(400, "Cron không hợp lệ")
    if "owner_id" in data and (await app_role(user, app, db)) != "owner":
        raise HTTPException(403, "Chỉ owner đổi được chủ sở hữu")
    for k, v in data.items():
        if k == "env":
            app.env_enc = encrypt(v)
        else:
            setattr(app, k, v)
    await db.commit()
    await db.refresh(app)  # updated_at (onupdate) hết hạn sau commit
    # ghi ngay ra đĩa (vendor-free: file luôn phản ánh cấu hình hiện tại)
    if app.kind == "managed":
        if "compose_yaml" in data:
            compose.write_compose(app)
        if "env" in data:
            compose.write_env(app, await _service_env(db, app))
        compose.write_readme(app)
        compose.fix_owner(app)
    await audit(db, user, "app.update", app.slug, {"fields": list(data.keys())}, request)
    return app_out(app, await app_role(user, app, db))


@router.post("/{app_id}/deploy")
async def deploy(app_id: int, user: CurrentUser, db: DB, request: Request, pull: bool = True):
    app = await check_app(user, app_id, db, "deployer")
    job = await enqueue(db, "deploy", {"app_id": app.id, "pull": pull}, app.id, user.id)
    await audit(db, user, "app.deploy", app.slug, request=request)
    return {"job_id": job.id}


@router.post("/{app_id}/action/{verb}")
async def app_action(app_id: int, verb: str, user: CurrentUser, db: DB, request: Request):
    if verb not in ("start", "stop", "restart", "pull", "down"):
        raise HTTPException(400, "Hành động không hợp lệ")
    app = await check_app(user, app_id, db, "deployer")
    job = await enqueue(db, "app_action", {"app_id": app.id, "action": verb}, app.id, user.id)
    await audit(db, user, f"app.{verb}", app.slug, request=request)
    return {"job_id": job.id}


async def _service_env(db, app: App) -> dict[str, str]:
    from ..services import platform

    env: dict[str, str] = {}
    for b in (await db.execute(select(ServiceBinding).where(ServiceBinding.app_id == app.id))).scalars():
        env.update(platform.binding_env(b))
    return env


class DeleteIn(BaseModel):
    remove_containers: bool = True
    remove_volumes: bool = False
    remove_files: bool = False  # xoá thư mục compose/.env (mặc định giữ lại — vendor-free)
    drop_service_data: bool = False
    confirm: str


@router.post("/{app_id}/delete")
async def delete_app(app_id: int, body: DeleteIn, user: CurrentUser, db: DB, request: Request):
    app = await check_app(user, app_id, db, "owner")
    if body.confirm != app.slug:
        raise HTTPException(400, "Nhập đúng slug để xác nhận")
    if not body.remove_containers:
        # gỡ khỏi panel, giữ nguyên container + thư mục compose (tự vận hành tiếp bằng docker compose)
        await db.delete(app)
        await db.commit()
        await audit(db, user, "app.unmanage", app.slug, request=request)
        return {"ok": True}
    job = await enqueue(db, "app_delete", {"app_id": app.id, **body.model_dump(exclude={"confirm"})}, app.id, user.id)
    await audit(db, user, "app.delete", app.slug, body.model_dump(exclude={"confirm"}), request)
    return {"job_id": job.id}


class LimitsIn(BaseModel):
    service_limits: dict[str, dict[str, float | int | None]] = Field(default_factory=dict)
    cpu_limit: float | None = None
    mem_limit_mb: int | None = None
    disk_quota_mb: int | None = None


@router.put("/{app_id}/limits")
async def set_limits(app_id: int, body: LimitsIn, user: StaffUser, db: DB, request: Request):
    """Chỉ admin/operator cấp phát tài nguyên."""
    app = await check_app(user, app_id, db, "owner")
    clean = {s: {k: v for k, v in lim.items() if v} for s, lim in body.service_limits.items()}
    if body.cpu_limit:
        total_cpu = sum(float(v.get("cpus") or 0) for v in clean.values())
        if total_cpu > body.cpu_limit:
            raise HTTPException(400, f"Tổng CPU các service ({total_cpu}) vượt hạn mức app ({body.cpu_limit})")
    if body.mem_limit_mb:
        total_mem = sum(int(v.get("memory_mb") or 0) for v in clean.values())
        if total_mem > body.mem_limit_mb:
            raise HTTPException(400, f"Tổng RAM các service ({total_mem}MB) vượt hạn mức app ({body.mem_limit_mb}MB)")
    app.service_limits = clean
    app.cpu_limit, app.mem_limit_mb, app.disk_quota_mb = body.cpu_limit, body.mem_limit_mb, body.disk_quota_mb
    await db.commit()
    job = await enqueue(db, "apply_limits", {"app_id": app.id}, app.id, user.id)
    await audit(db, user, "app.limits", app.slug, body.model_dump(), request)
    return {"job_id": job.id}


class EdgeIn(BaseModel):
    services: list[str]


@router.put("/{app_id}/edge")
async def set_edge(app_id: int, body: EdgeIn, user: CurrentUser, db: DB):
    app = await check_app(user, app_id, db, "deployer")
    app.edge_services = body.services
    await db.commit()
    job = await enqueue(db, "apply_limits", {"app_id": app.id}, app.id, user.id)
    return {"job_id": job.id}


@router.get("/{app_id}/logs")
async def app_logs(app_id: int, user: CurrentUser, db: DB, tail: int = 200, service: str | None = None):
    app = await check_app(user, app_id, db)
    out = []
    for c in await docker_svc.project_containers(app.compose_project):
        svc = (c.labels or {}).get(docker_svc.COMPOSE_SERVICE)
        if service and svc != service:
            continue
        out.append({"container": c.name, "service": svc, "logs": await docker_svc.logs(c.id, tail=tail)})
    return out


@router.get("/{app_id}/jobs")
async def app_jobs(app_id: int, user: CurrentUser, db: DB, limit: int = 30):
    await check_app(user, app_id, db)
    rows = (await db.execute(select(Job).where(Job.app_id == app_id).order_by(Job.id.desc()).limit(limit))).scalars().all()
    return [{"id": j.id, "kind": j.kind, "status": j.status, "created_at": j.created_at, "finished_at": j.finished_at} for j in rows]


# ---- members
@router.get("/{app_id}/members")
async def members(app_id: int, user: CurrentUser, db: DB):
    app = await check_app(user, app_id, db)
    rows = (await db.execute(select(AppMember).where(AppMember.app_id == app_id))).scalars().all()
    owner = await db.get(User, app.owner_id) if app.owner_id else None
    return {
        "owner": {"id": owner.id, "email": owner.email, "name": owner.name} if owner else None,
        "members": [{"id": m.id, "user_id": m.user_id, "email": m.user.email, "name": m.user.name, "role": m.role} for m in rows],
    }


class MemberIn(BaseModel):
    user_id: int
    role: str = "viewer"


@router.post("/{app_id}/members")
async def add_member(app_id: int, body: MemberIn, user: CurrentUser, db: DB, request: Request):
    app = await check_app(user, app_id, db, "owner")
    if body.role not in ("owner", "deployer", "viewer"):
        raise HTTPException(400, "Role không hợp lệ")
    m = (await db.execute(select(AppMember).where(AppMember.app_id == app_id, AppMember.user_id == body.user_id))).scalar_one_or_none()
    if m:
        m.role = body.role
    else:
        db.add(AppMember(app_id=app_id, user_id=body.user_id, role=body.role))
    await db.commit()
    await audit(db, user, "app.member", app.slug, body.model_dump(), request)
    return {"ok": True}


@router.delete("/{app_id}/members/{member_id}")
async def remove_member(app_id: int, member_id: int, user: CurrentUser, db: DB):
    await check_app(user, app_id, db, "owner")
    m = await db.get(AppMember, member_id)
    if m and m.app_id == app_id:
        await db.delete(m)
        await db.commit()
    return {"ok": True}


# ---- môi trường phát triển (devbox): thông tin SSH
async def _devbox_app(user: User, app_id: int, db, role: str = "viewer") -> App:
    app = await check_app(user, app_id, db, role)
    if not devbox.is_devbox(app):
        raise HTTPException(400, "App không phải môi trường phát triển")
    return app


@router.get("/{app_id}/devbox")
async def devbox_info(app_id: int, user: CurrentUser, db: DB, reveal: bool = False):
    app = await _devbox_app(user, app_id, db, "deployer" if reveal else "viewer")
    out = await devbox.info(app)
    if reveal:
        out["password"] = devbox.env_get(decrypt(app.env_enc), "DEV_PASSWORD") or ""
        await audit(db, user, "devbox.reveal_password", app.slug)
    return out


class DevboxIn(BaseModel):
    password: str | None = None  # None = giữ nguyên, "" = tắt đăng nhập bằng mật khẩu
    keys: str | None = None
    sudo: bool | None = None


@router.put("/{app_id}/devbox")
async def devbox_update(app_id: int, body: DevboxIn, user: CurrentUser, db: DB, request: Request):
    app = await _devbox_app(user, app_id, db, "deployer")
    env = decrypt(app.env_enc)
    cur = await devbox.info(app)
    try:
        pw = devbox.check_password(body.password) if body.password is not None else None
        keys = devbox.clean_keys(body.keys) if body.keys is not None else None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    has_pw = bool(pw) if pw is not None else cur["password_auth"]
    has_keys = bool(keys) if keys is not None else bool(devbox.clean_keys(cur["keys"]) if cur["keys"] else "")
    if not has_pw and not has_keys:
        raise HTTPException(400, "Cần mật khẩu hoặc ít nhất một public key để đăng nhập SSH")
    if pw is not None:
        env = devbox.env_set(env, "DEV_PASSWORD", pw)
    if body.sudo is not None:
        env = devbox.env_set(env, "DEV_SUDO", "true" if body.sudo else "false")
    app.env_enc = encrypt(env)
    await db.commit()
    await db.refresh(app)
    compose.write_env(app, await _service_env(db, app))
    if keys is not None:
        devbox.prepare_dir(app, keys)
    job = await enqueue(db, "deploy", {"app_id": app.id, "pull": False}, app.id, user.id)
    await audit(db, user, "devbox.update", app.slug,
                {"password": "changed" if body.password else ("disabled" if body.password == "" else None),
                 "keys": len(keys.splitlines()) if keys is not None else None, "sudo": body.sudo}, request)
    return {"job_id": job.id}


# ---- deploy webhook
@router.post("/{app_id}/deploy-token")
async def rotate_deploy_token(app_id: int, user: CurrentUser, db: DB):
    app = await check_app(user, app_id, db, "owner")
    app.deploy_token = secrets.token_urlsafe(32)
    await db.commit()
    return {"token": app.deploy_token, "url": f"/api/hooks/deploy/{app.deploy_token}"}


@router.delete("/{app_id}/deploy-token")
async def revoke_deploy_token(app_id: int, user: CurrentUser, db: DB):
    app = await check_app(user, app_id, db, "owner")
    app.deploy_token = None
    await db.commit()
    return {"ok": True}


@router.get("/{app_id}/overview")
async def overview(app_id: int, user: CurrentUser, db: DB):
    """Tóm tắt cho tab tổng quan: routes, bindings, backup gần nhất."""
    app = await check_app(user, app_id, db)
    routes = (await db.execute(select(ProxyRoute).where(ProxyRoute.app_id == app_id))).scalars().all()
    bindings = (await db.execute(select(ServiceBinding).where(ServiceBinding.app_id == app_id))).scalars().all()
    last_backup = (await db.execute(select(Backup).where(Backup.app_id == app_id).order_by(Backup.id.desc()).limit(1))).scalar_one_or_none()
    return {
        "routes": [{"id": r.id, "hosts": r.hosts, "enabled": r.enabled, "health": r.health_status} for r in routes],
        "bindings": [{"id": b.id, "kind": b.service.kind, "service": b.service.name, "resource": b.resource} for b in bindings],
        "last_backup": {"id": last_backup.id, "status": last_backup.status, "started_at": last_backup.started_at, "size": last_backup.size_bytes}
        if last_backup else None,
    }


# ======================================================================= file trong thư mục app (vendor-free)
MAX_EDIT = 1_000_000


def _root(app: App) -> Path:
    if app.kind == "managed":
        return Path(app.work_dir)
    if compose.files_accessible(app):
        return Path(app.work_dir)
    raise HTTPException(400, "Không truy cập được thư mục của app này")


def _safe_path(root: Path, rel: str) -> Path:
    p = (root / rel.lstrip("/")).resolve()
    if p != root.resolve() and not str(p).startswith(str(root.resolve()) + os.sep):
        raise HTTPException(400, "Đường dẫn nằm ngoài thư mục app")
    return p


@router.get("/{app_id}/files")
async def files(app_id: int, user: CurrentUser, db: DB, path: str = ""):
    app = await check_app(user, app_id, db, "deployer")
    root = _root(app)
    p = _safe_path(root, path)
    if p.is_dir():
        entries = []
        for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if e.name in (".git", "node_modules"):
                continue
            st = e.lstat()
            entries.append({"name": e.name, "path": str(e.relative_to(root)), "dir": e.is_dir(), "size": st.st_size, "modified": st.st_mtime})
        return {"root": str(root), "path": str(p.relative_to(root)) if p != root else "", "entries": entries}
    if not p.is_file():
        raise HTTPException(404, "Không có file")
    if p.stat().st_size > MAX_EDIT:
        raise HTTPException(400, "File quá lớn để xem trong panel")
    raw = p.read_bytes()
    if b"\x00" in raw[:4096]:
        raise HTTPException(400, "File nhị phân")
    generated = p.name in ("docker-compose.override.yml", "compose.th.yml", "README.tekar.md")
    return {"root": str(root), "path": str(p.relative_to(root)), "content": raw.decode("utf-8", "replace"), "generated": generated}


class FileIn(BaseModel):
    path: str
    content: str


@router.put("/{app_id}/files")
async def save_file(app_id: int, body: FileIn, user: CurrentUser, db: DB, request: Request):
    app = await check_app(user, app_id, db, "deployer")
    root = _root(app)
    p = _safe_path(root, body.path)
    if p.is_dir():
        raise HTTPException(400, "Là thư mục")
    if len(body.content.encode()) > MAX_EDIT:
        raise HTTPException(400, "Nội dung quá lớn")
    if p.name in ("docker-compose.yml", "compose.yml", "compose.yaml", "docker-compose.yaml"):
        try:
            compose.validate_yaml(body.content)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + ".bak"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content)
    if p.name == ".env":
        p.chmod(0o600)
    compose.fix_owner(app)
    if compose.sync_from_disk(app):
        await db.commit()
    await audit(db, user, "app.file_save", app.slug, {"path": body.path}, request)
    return {"ok": True, "note": "Bấm Deploy để áp dụng thay đổi"}


@router.get("/{app_id}/export")
async def export(app_id: int, user: CurrentUser, db: DB, request: Request, include_data: bool = False, include_env: bool = True):
    """Tải thư mục app (compose, env, override, README, file cấu hình) để tự vận hành ở nơi khác."""
    import tarfile
    import tempfile

    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    app = await check_app(user, app_id, db, "owner" if include_env else "deployer")
    root = _root(app)
    tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    tmp.close()

    def _build():
        def flt(ti: tarfile.TarInfo):
            name = ti.name.split("/")[-1]
            if name in (".git", "node_modules", "__pycache__") or name.endswith(".bak"):
                return None
            if not include_env and name.startswith(".env"):
                return None
            return ti

        with tarfile.open(tmp.name, "w:gz") as tar:
            tar.add(root, arcname=app.slug, filter=flt)
            if app.kind == "adopted" and compose.override_path(app).exists():
                tar.add(compose.override_path(app), arcname=f"{app.slug}/docker-compose.override.yml")
            if include_data:
                for label, d in (("volumes", app.volumes_dir), ("data", app.data_dir)):
                    if d and os.path.isdir(d):
                        tar.add(d, arcname=f"{app.slug}/_{label}")

    await docker_svc.run(_build)
    await audit(db, user, "app.export", app.slug, {"include_data": include_data, "include_env": include_env}, request)
    return FileResponse(tmp.name, filename=f"{app.slug}.tar.gz", media_type="application/gzip",
                        background=BackgroundTask(os.unlink, tmp.name))
