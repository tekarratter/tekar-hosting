"""Stack compose trên host: xem ai đang quản lý (badge), xem compose/env, chuyển về panel hoặc giữ nguyên."""

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from ..core.audit import audit
from ..core.deps import DB, StaffUser
from ..core.jobs import JobContext, enqueue, job_handler
from ..core.security import encrypt
from ..core.store import storage_path
from ..db import SessionLocal
from ..models import App
from ..services import docker_svc, stacks

router = APIRouter(prefix="/api/stacks", tags=["stacks"])
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


async def _find(project: str) -> dict[str, Any]:
    for info in await docker_svc.run(stacks.detect_all):
        if info["project"] == project:
            return info
    raise HTTPException(404, "Không thấy stack")


@router.get("")
async def list_stacks(user: StaffUser, db: DB):
    apps = {a.compose_project: a for a in (await db.execute(select(App))).scalars()}
    out = []
    for info in await docker_svc.run(stacks.detect_all):
        a = apps.get(info["project"])
        info["panel"] = {"app_id": a.id, "slug": a.slug, "kind": a.kind, "converted_from": a.converted_from} if a else None
        if a and a.kind == "managed":
            info["manager"] = {"key": "tekar-hosting", "label": "Tekar Hosting"}
        out.append(stacks.json_safe(info))
    return out


@router.get("/{project}")
async def stack_detail(project: str, user: StaffUser, request: Request, db: DB, reveal: bool = False):
    info = await _find(project)
    data = await docker_svc.run(stacks.read_stack, info)
    if reveal:
        if user.role != "admin":
            raise HTTPException(403, "Chỉ admin xem được giá trị secret")
        await audit(db, user, "stack.reveal_env", project, request=request)
    else:
        data["env"] = stacks.mask_env(data["env"])
    return {**stacks.json_safe(info), **data}


class ConvertIn(BaseModel):
    slug: str
    name: str | None = None
    copy_dir: bool = False  # chép cả thư mục nguồn (code/config/dữ liệu bind tương đối) sang thư mục app mới
    start: bool = True  # tạo lại container từ thư mục mới ngay (downtime vài giây)


@router.post("/{project}/convert")
async def convert(project: str, body: ConvertIn, user: StaffUser, db: DB, request: Request):
    if not SLUG_RE.match(body.slug):
        raise HTTPException(400, "Slug chỉ gồm a-z, 0-9, '-'")
    existing = (await db.execute(select(App).where(App.slug == body.slug))).scalar_one_or_none()
    if existing and existing.compose_project != project:
        raise HTTPException(400, "Slug đã được dùng")
    info = await _find(project)
    if info["manager"]["key"] == "tekar-hosting":
        raise HTTPException(400, "Stack hệ thống không chuyển được")
    job = await enqueue(db, "stack_convert", {"project": project, **body.model_dump(), "user_id": user.id}, existing.id if existing else None, user.id)
    await audit(db, user, "stack.convert", project, body.model_dump(), request)
    return {"job_id": job.id}


@job_handler("stack_convert")
async def job_convert(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    from pathlib import Path

    from ..services import compose

    project = params["project"]
    info = next((i for i in await docker_svc.run(stacks.detect_all) if i["project"] == project), None)
    if info is None:
        raise RuntimeError("Không thấy stack")
    mgr = info["manager"]
    await ctx.log(f"Stack {project} — đang quản lý bởi: {mgr['label']}")
    data = await docker_svc.run(stacks.read_stack, info)
    for n in data["notes"]:
        await ctx.log(f"ℹ {n}")
    if not data["compose"]:
        raise RuntimeError("Không đọc được file compose của stack")
    await ctx.log(f"Compose: {data['compose_path']} | env: {data['env_path'] or '(khôi phục từ container)'}")

    async with SessionLocal() as s:
        apps_root = await storage_path("apps", s)
    new_dir = Path(apps_root) / params["slug"]
    if new_dir.exists() and any(new_dir.iterdir()):
        raise RuntimeError(f"Thư mục {new_dir} đã có dữ liệu")
    new_dir.mkdir(parents=True, exist_ok=True)

    src = data["source_dir"]
    if params.get("copy_dir") and src:
        await ctx.log("Dừng container để chép dữ liệu nhất quán")
        for c in await docker_svc.project_containers(project):
            if c.status == "running":
                await docker_svc.run(c.stop)
        await ctx.log(f"Chép {src} → {new_dir} (bỏ .git, node_modules)")
        await docker_svc.run(stacks.copy_tree, src, str(new_dir))
        compose_text = data["compose"]
        name = Path(data["compose_path"]).name if data["compose_path"] and not data["compose_path"].startswith(mgr["label"]) else "docker-compose.yml"
        if name != "docker-compose.yml" and (new_dir / name).exists():
            (new_dir / name).rename(new_dir / "docker-compose.original.yml")
    else:
        base = src or info["work_dir"]
        compose_text, changes = stacks.rewrite_relative(data["compose"], base)
        for ch in changes:
            await ctx.log(f"  đường dẫn tương đối → tuyệt đối: {ch}")
        (new_dir / "docker-compose.original.yml").write_text(data["compose"])
    (new_dir / "docker-compose.yml").write_text(compose_text)
    user_env = stacks.strip_dockhand_env(data["env"]) if data["env"] else ""

    async with SessionLocal() as s:
        app = (await s.execute(select(App).where(App.compose_project == project))).scalar_one_or_none()
        if app is None:
            app = App(slug=params["slug"], compose_project=project, owner_id=params.get("user_id"))
            s.add(app)
        app.name = params.get("name") or app.name or project
        app.kind, app.work_dir, app.compose_files = "managed", str(new_dir), []
        app.compose_yaml, app.env_enc = compose_text, encrypt(user_env)
        app.converted_from = mgr["key"]
        app.description = app.description or f"Chuyển từ {mgr['label']}"
        await s.commit()
        await s.refresh(app)

    compose.write_env(app)
    compose.write_readme(app)
    compose.fix_owner(app)
    await ctx.log(f"Đã tạo app '{app.slug}' tại {new_dir} (giữ tên project '{project}' → volume/network giữ nguyên)")
    if params.get("start", True):
        await ctx.log("Tạo lại container từ thư mục mới")
        await compose.deploy(app, ctx, pull=False)
    if mgr["key"] in ("dockhand", "portainer", "dockge"):
        await ctx.log(f"⚠ Quan trọng: xoá stack '{project}' khỏi {mgr['label']} (KHÔNG chọn xoá volume) để {mgr['label']} "
                      "không deploy đè lại (đặc biệt stack git có auto-update).")
    return {"app_id": app.id, "work_dir": str(new_dir)}
