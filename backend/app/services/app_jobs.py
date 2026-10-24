"""Job handler cho vòng đời app: deploy, start/stop/restart, xoá, cập nhật image, dọn dẹp docker."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import settings
from ..core.jobs import JobContext, job_handler
from ..db import SessionLocal
from ..models import App, GitCredential, Job, ServiceBinding
from . import compose, devbox, docker_svc, git, platform


async def _load(app_id: int) -> App:
    async with SessionLocal() as s:
        app = await s.get(App, app_id)
        if app is None:
            raise RuntimeError("App không tồn tại")
        return app


async def refresh_status(app: App) -> str:
    cs = await docker_svc.project_containers(app.compose_project)
    if not cs:
        status = "stopped"
    else:
        states = [c.status for c in cs]
        if all(s == "running" for s in states):
            status = "running"
        elif any(s == "running" for s in states):
            status = "partial"
        elif any(s == "restarting" for s in states):
            status = "restarting"
        else:
            status = "stopped"
    async with SessionLocal() as s:
        row = await s.get(App, app.id)
        if row:
            row.status = status
            await s.commit()
    return status


async def apply_live_limits(app: App, ctx: JobContext | None = None) -> None:
    """docker update giới hạn ngay không cần recreate."""
    for c in await docker_svc.project_containers(app.compose_project):
        svc = (c.labels or {}).get(docker_svc.COMPOSE_SERVICE)
        lim = (app.service_limits or {}).get(svc) or {}
        # gọi thẳng API update với NanoCpus (container do compose tạo dùng NanoCpus, không trộn được với CpuQuota)
        data: dict[str, Any] = {}
        if lim.get("cpus"):
            data["NanoCpus"] = int(float(lim["cpus"]) * 1e9)
        if lim.get("memory_mb"):
            data["Memory"] = int(lim["memory_mb"]) * 1024 * 1024
            data["MemorySwap"] = data["Memory"] * 2
        if data:
            try:
                api = docker_svc.client().api
                await docker_svc.run(lambda: api._result(api._post_json(api._url("/containers/{0}/update", c.id), data=data), True))
                if ctx:
                    await ctx.log(f"docker update {c.name}: {data}")
            except Exception as e:  # noqa: BLE001
                if ctx:
                    await ctx.log(f"⚠ {c.name}: {e}")


async def connect_edge(app: App, ctx: JobContext | None = None) -> None:
    for c in await docker_svc.project_containers(app.compose_project):
        svc = (c.labels or {}).get(docker_svc.COMPOSE_SERVICE)
        if svc in (app.edge_services or []) and c.status == "running":
            if await docker_svc.connect_network(c.name, settings.edge_network) and ctx:
                await ctx.log(f"Gắn {c.name} vào mạng {settings.edge_network}")


async def platform_bindings(app: App) -> tuple[dict[str, str], set[str]]:
    """Env tiêm từ binding + tên container/host của dịch vụ nền tảng mà app kết nối tới."""
    env: dict[str, str] = {}
    hosts: set[str] = set()
    async with SessionLocal() as s:
        for b in (await s.execute(select(ServiceBinding).where(ServiceBinding.app_id == app.id))).scalars():
            env.update(platform.binding_env(b))
            hosts.update(h for h in (b.service.container_name, platform.app_host(b.service)[0]) if h)
    return env, hosts


async def connect_platform(app: App, hosts: set[str], ctx: JobContext | None = None) -> str | None:
    """Tạo mạng th-svc-<slug> (external, `compose down` không xoá) và gắn container dịch vụ nền tảng vào đó.
    Host không phải container trên máy này (server external qua IP/DNS) thì bỏ qua."""
    if not hosts:
        return None
    net = compose.platform_network(app)
    await docker_svc.ensure_network(net)
    for h in sorted(hosts):
        try:
            if await docker_svc.get_container(h) is None:
                continue
            if await docker_svc.connect_network(h, net) and ctx:
                await ctx.log(f"Gắn {h} vào mạng {net}")
        except Exception as e:  # noqa: BLE001 — tên không hợp lệ làm tên container (IP:port, URL…)
            if ctx:
                await ctx.log(f"⚠ gắn {h} vào {net}: {e}")
    return net


@job_handler("deploy")
async def job_deploy(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    app = await _load(params["app_id"])
    async with SessionLocal() as s:  # ổ đĩa là nguồn sự thật: nạp lại compose/.env người dùng có thể đã sửa tay
        row = await s.get(App, app.id)
        if compose.sync_from_disk(row):
            await s.commit()
            await ctx.log("Nạp thay đổi compose/.env từ đĩa")
        app = row
    await docker_svc.ensure_network(settings.edge_network)
    await ctx.log(f"Deploy {app.name} ({app.kind}) project={app.compose_project}")
    service_env, hosts = await platform_bindings(app)
    svc_net = await connect_platform(app, hosts, ctx)
    build = params.get("build", bool((app.git or {}).get("repo")))  # app từ git: build lại image từ code
    if devbox.is_devbox(app):
        devbox.prepare_dir(app)
        await devbox.ensure_image(ctx)
    await compose.deploy(app, ctx, pull=params.get("pull", True), service_env=service_env, build=build, svc_network=svc_net)
    status = await refresh_status(app)
    async with SessionLocal() as s:
        row = await s.get(App, app.id)
        row.last_deployed_at = datetime.now(UTC)
        await s.commit()
    from .notify import resolve

    await resolve(f"deploy:{app.slug}")
    return {"status": status}


async def git_credential(cred_id: int | None) -> GitCredential | None:
    if not cred_id:
        return None
    async with SessionLocal() as s:
        cred = await s.get(GitCredential, cred_id)
    if cred is None:
        raise RuntimeError("Kết nối git của app đã bị xoá — chọn kết nối khác trong tab Git")
    return cred


@job_handler("git_update")
async def job_git_update(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    """Clone (lần đầu) hoặc kéo code mới về <work_dir>/src, cập nhật compose theo repo (nếu bật), rồi build + deploy."""
    app = await _load(params["app_id"])
    g = dict(app.git or {})
    if not g.get("repo"):
        raise RuntimeError("App không có nguồn git")
    cred = await git_credential(g.get("credential_id"))
    src = Path(app.work_dir) / git.SRC
    old = g.get("commit")
    if (src / ".git").is_dir():
        await ctx.log(f"Kéo code {g['repo']} ({g.get('branch') or 'nhánh mặc định'})")
        await git.update(src, g["repo"], g.get("branch"), cred, ctx)
    else:
        if src.exists() and any(src.iterdir()):
            raise RuntimeError(f"{src} đã có dữ liệu nhưng không phải repo git — đổi tên/xoá thư mục rồi thử lại")
        await ctx.log(f"Clone {g['repo']} → {src}")
        if src.exists():
            src.rmdir()
        await git.clone(g["repo"], g.get("branch"), src, cred, ctx)
    info = await git.head(src)
    await ctx.log(f"Commit {info['commit'][:10]}: {info['commit_msg']}" + (" (không đổi)" if info["commit"] == old else ""))
    git.chown_tree(src, compose.storage_owner(Path(app.work_dir)))
    async with SessionLocal() as s:
        row = await s.get(App, app.id)
        g = dict(row.git or {})
        g.update(commit=info["commit"], commit_msg=info["commit_msg"], author=info["author"], commit_date=info["date"],
                 pulled_at=datetime.now(UTC).isoformat(), branch=g.get("branch") or info["branch"], error=None)
        if g.get("compose_path") and g.get("sync_compose") and not params.get("initial"):
            text = git.compose_from_repo(src, g["compose_path"])
            compose.validate_yaml(text)
            if text != row.compose_yaml:
                row.compose_yaml = text
                compose.write_compose(row)
                compose.fix_owner(row)
                await ctx.log(f"Cập nhật docker-compose.yml theo {g['compose_path']} trong repo")
        row.git = g
        await s.commit()
    if not params.get("deploy", True):
        return {"commit": info["commit"], "deployed": False}
    if params.get("only_if_changed") and info["commit"] == old:
        return {"commit": info["commit"], "deployed": False}
    res = await job_deploy(ctx, {"app_id": app.id, "pull": True, "build": True})
    return {"commit": info["commit"], "previous": old, "deployed": True, **res}


async def git_poll() -> None:
    """App bật auto_pull: so commit trên remote, có commit mới thì kéo + deploy."""
    async with SessionLocal() as s:
        apps = [a for a in (await s.execute(select(App))).scalars() if (a.git or {}).get("auto_pull") and (a.git or {}).get("repo")]
        busy = set((await s.execute(select(Job.app_id).where(Job.kind.in_(("git_update", "deploy", "app_clone")),
                                                             Job.status.in_(("queued", "claimed", "running"))))).scalars())
    from ..core.jobs import enqueue

    for a in apps:
        if a.id in busy:
            continue
        g = a.git or {}
        try:
            sha = await git.remote_head(g["repo"], g.get("branch"), await git_credential(g.get("credential_id")))
        except Exception as e:  # noqa: BLE001
            async with SessionLocal() as s:
                row = await s.get(App, a.id)
                row.git = {**(row.git or {}), "error": str(e)[:300]}
                await s.commit()
            continue
        if sha and sha != g.get("commit"):
            async with SessionLocal() as s:
                await enqueue(s, "git_update", {"app_id": a.id, "deploy": True, "trigger": "poll"}, a.id, None)


@job_handler("app_action")
async def job_action(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    app = await _load(params["app_id"])
    verb = params["action"]
    await compose.action(app, verb, ctx)
    if verb in ("start", "restart"):
        await connect_edge(app, ctx)
    return {"status": await refresh_status(app)}


@job_handler("apply_limits")
async def job_limits(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    app = await _load(params["app_id"])
    if compose.files_accessible(app):
        await compose.write_override(app, await connect_platform(app, (await platform_bindings(app))[1], ctx))
    else:
        await ctx.log("Không truy cập được compose → chỉ áp dụng trực tiếp (docker update / network connect), mất khi container bị tạo lại")
    await apply_live_limits(app, ctx)
    await connect_edge(app, ctx)
    return {}


@job_handler("app_delete")
async def job_delete(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    app = await _load(params["app_id"])
    if params.get("remove_containers", True):
        args = ["down", "--remove-orphans"] + (["--volumes"] if params.get("remove_volumes") else [])
        await compose.run_cmd(compose.base_cmd(app) + args, ctx, check=False)
    async with SessionLocal() as s:
        bindings = (await s.execute(select(ServiceBinding).where(ServiceBinding.app_id == app.id))).scalars().all()
        for b in bindings:
            try:
                await platform.deprovision(b.service, b.resource, b.username, bool(params.get("drop_service_data")))
                await ctx.log(f"Thu hồi {b.service.kind} {b.resource}")
            except Exception as e:  # noqa: BLE001
                await ctx.log(f"⚠ thu hồi {b.resource}: {e}")
        row = await s.get(App, app.id)
        await s.delete(row)
        await s.commit()
    if params.get("remove_containers", True):
        try:
            await docker_svc.remove_network(compose.platform_network(app))
        except Exception as e:  # noqa: BLE001
            await ctx.log(f"⚠ xoá mạng {compose.platform_network(app)}: {e}")
    import shutil

    if params.get("remove_files"):
        shutil.rmtree(compose.app_dir(app), ignore_errors=True)
        await ctx.log(f"Xoá thư mục {compose.app_dir(app)}")
    elif app.kind == "adopted":
        shutil.rmtree(compose.override_path(app).parent, ignore_errors=True)
    else:
        await ctx.log(f"Giữ lại thư mục {app.work_dir} (compose + .env)")
    async with SessionLocal() as s:
        from .proxy import apply

        await apply(s)
    return {}


@job_handler("auto_update")
async def job_auto_update(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    """Pull image mới; nếu có thay đổi thì up -d."""
    app = await _load(params["app_id"])
    before = {c.name: c.attrs["Image"] for c in await docker_svc.project_containers(app.compose_project)}
    await compose.run_cmd(compose.base_cmd(app) + ["pull", "--ignore-buildable", "--quiet"], ctx, check=False)
    await compose.run_cmd(compose.base_cmd(app) + ["up", "-d"], ctx)
    after = {c.name: c.attrs["Image"] for c in await docker_svc.project_containers(app.compose_project)}
    changed = [n for n in after if before.get(n) != after[n]]
    await ctx.log(f"Container cập nhật image: {changed or 'không có'}")
    return {"changed": changed}


@job_handler("docker_prune")
async def job_prune(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    c = docker_svc.client()
    res: dict[str, Any] = {}
    res["containers"] = await docker_svc.run(c.containers.prune)
    res["images"] = await docker_svc.run(c.images.prune, filters={"dangling": not params.get("all_images", False)})
    res["build_cache"] = await docker_svc.run(c.api.prune_builds)
    if params.get("volumes"):
        res["volumes"] = await docker_svc.run(c.volumes.prune)
    freed = sum((v or {}).get("SpaceReclaimed", 0) or 0 for v in res.values() if isinstance(v, dict))
    await ctx.log(f"Giải phóng {freed / 1024 / 1024:.1f} MB")
    return {"freed_bytes": freed}
