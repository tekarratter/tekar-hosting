"""Cấu hình thư mục lưu trữ (vendor-free) + đích backup (local / S3 / SFTP)."""

import asyncio
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import settings
from ..core.audit import audit
from ..core.deps import DB, AdminUser, CurrentUser, StaffUser, check_app
from ..core.jobs import JobContext, enqueue, job_handler
from ..core.security import encrypt
from ..core.store import get_setting, set_setting, storage_path
from ..models import App, Backup, BackupTarget, Job
from ..services import backup_targets
from ..services import stack_usage as stack_usage_svc

router = APIRouter(prefix="/api/storage", tags=["storage"])
KINDS = ("apps", "backups", "volumes", "data")


def _accessible_roots() -> list[str]:
    roots = {str(settings.data_dir), str(settings.apps_root), os.environ.get("TH_STORAGE_ROOT", "/data")}
    return sorted(roots)


def _check_dir(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        raise HTTPException(400, f"{path}: phải là đường dẫn tuyệt đối")
    roots = _accessible_roots()
    if not any(str(p) == r or str(p).startswith(r.rstrip("/") + "/") for r in roots):
        raise HTTPException(400, f"{path} nằm ngoài vùng panel truy cập được ({', '.join(roots)}). "
                                 "Đổi TH_STORAGE_ROOT trong .env rồi chạy lại docker compose up -d")
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".tekar-write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise HTTPException(400, f"Không ghi được vào {path}: {e}") from e
    u = shutil.disk_usage(p)
    return {"path": str(p), "free": u.free, "total": u.total}


@router.get("")
async def get_storage(user: StaffUser, db: DB):
    out: dict[str, Any] = {"roots": _accessible_roots(), "dirs": {}}
    for k in KINDS:
        p = await storage_path(k, db)
        info: dict[str, Any] = {"path": str(p) if p else None, "custom": bool((await get_setting(f"storage_{k}_dir", db) or "").strip())}
        if p and p.exists():
            u = shutil.disk_usage(p)
            info.update(free=u.free, total=u.total)
        out["dirs"][k] = info
    out["default_backup_destination_id"] = await get_setting("default_backup_destination_id", db)
    return out


class StorageIn(BaseModel):
    apps: str = ""
    backups: str = ""
    volumes: str = ""
    data: str = ""
    default_backup_destination_id: int | None = None


@router.put("")
async def put_storage(body: StorageIn, user: AdminUser, db: DB, request: Request):
    checked = {}
    for k in KINDS:
        v = getattr(body, k).strip()
        if v:
            checked[k] = _check_dir(v)
        await set_setting(f"storage_{k}_dir", v, db)
    await set_setting("default_backup_destination_id", body.default_backup_destination_id, db)
    await db.commit()
    await audit(db, user, "storage.update", "", body.model_dump(), request)
    return {"ok": True, "checked": checked,
            "note": "Áp dụng cho app tạo mới / backup mới. App hiện có giữ nguyên thư mục của nó."}


# ======================================================================= backup targets
def target_out(t: BackupTarget, apps: int = 0) -> dict[str, Any]:
    cfg = dict(t.config or {})
    return {"id": t.id, "name": t.name, "kind": t.kind, "config": cfg, "has_secret": bool(t.secret_enc), "apps": apps,
            "created_at": t.created_at}


class TargetIn(BaseModel):
    name: str
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None  # s3 secret key | sftp password | sftp private key — None/"" khi sửa = giữ nguyên


def _validate_target(body: TargetIn) -> None:
    c = body.config
    if body.kind == "local":
        if not c.get("path"):
            raise HTTPException(400, "Cần đường dẫn thư mục")
        _check_dir(c["path"])
    elif body.kind == "s3":
        for k in ("bucket", "access_key"):
            if not c.get(k):
                raise HTTPException(400, f"Thiếu {k}")
    elif body.kind == "sftp":
        for k in ("host", "username"):
            if not c.get(k):
                raise HTTPException(400, f"Thiếu {k}")
        c.setdefault("port", 22)
        c.setdefault("auth", "password")
    else:
        raise HTTPException(400, "kind phải là local | s3 | sftp")


@router.get("/targets")
async def list_targets(user: CurrentUser, db: DB):
    counts = dict((await db.execute(select(App.backup_destination_id, func.count()).group_by(App.backup_destination_id))).all())
    rows = (await db.execute(select(BackupTarget).order_by(BackupTarget.name))).scalars().all()
    if user.role == "member":
        return [{"id": t.id, "name": t.name, "kind": t.kind} for t in rows]
    return [target_out(t, counts.get(t.id, 0)) for t in rows]


@router.post("/targets/test")
async def test_unsaved(body: TargetIn, user: StaffUser, db: DB, target_id: int | None = None):
    _validate_target(body)
    t = BackupTarget(name=body.name, kind=body.kind, config=body.config, secret_enc=encrypt(body.secret or ""))
    if target_id and not body.secret:
        old = await db.get(BackupTarget, target_id)
        if old:
            t.secret_enc = old.secret_enc
    try:
        return {"ok": True, "message": await asyncio.to_thread(backup_targets.test, t)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


@router.post("/targets")
async def create_target(body: TargetIn, user: StaffUser, db: DB, request: Request):
    _validate_target(body)
    t = BackupTarget(name=body.name, kind=body.kind, config=body.config, secret_enc=encrypt(body.secret or ""))
    db.add(t)
    await db.commit()
    await audit(db, user, "backup_target.create", t.name, {"kind": t.kind}, request)
    return target_out(t)


@router.put("/targets/{tid}")
async def update_target(tid: int, body: TargetIn, user: StaffUser, db: DB, request: Request):
    t = await db.get(BackupTarget, tid)
    if t is None:
        raise HTTPException(404)
    _validate_target(body)
    t.name, t.kind, t.config = body.name, body.kind, body.config
    if body.secret:
        t.secret_enc = encrypt(body.secret)
    await db.commit()
    await audit(db, user, "backup_target.update", t.name, request=request)
    return target_out(t)


@router.delete("/targets/{tid}")
async def delete_target(tid: int, user: StaffUser, db: DB, request: Request):
    t = await db.get(BackupTarget, tid)
    if t is None:
        raise HTTPException(404)
    await db.delete(t)
    await db.commit()
    await audit(db, user, "backup_target.delete", t.name, request=request)
    return {"ok": True}


@router.get("/targets/{tid}/files")
async def target_files(tid: int, user: StaffUser, db: DB, app_slug: str | None = None):
    t = await db.get(BackupTarget, tid)
    if t is None:
        raise HTTPException(404)
    try:
        files = await asyncio.to_thread(backup_targets.list_files, t, app_slug or "")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e
    known = set((await db.execute(select(Backup.remote_key).where(Backup.remote_key.like(f"target:{tid}:%")))).scalars())
    for f in files:
        f["in_panel"] = f"target:{tid}:{f['key']}" in known
    return files


class ImportRemoteIn(BaseModel):
    key: str  # vd "my-app/20261018-030000.tar.gz"
    app_id: int


@router.post("/targets/{tid}/import")
async def import_remote(tid: int, body: ImportRemoteIn, user: CurrentUser, db: DB, request: Request):
    """Đưa một bản backup có trên đích (vd sau khi cài lại panel) vào danh sách để khôi phục."""
    t = await db.get(BackupTarget, tid)
    if t is None:
        raise HTTPException(404)
    app = await check_app(user, body.app_id, db, "owner")
    # key do backup job tạo luôn có dạng <slug>/<stamp>.tar.gz; chặn ".." và key của app khác
    m = re.fullmatch(r"([a-z0-9][a-z0-9-]{0,62})/(\d{8}-\d{6})\.tar\.gz", body.key.strip())
    if not m or m.group(1) != app.slug:
        raise HTTPException(400, f"Key không hợp lệ — cần dạng {app.slug}/YYYYMMDD-HHMMSS.tar.gz")
    stamp = m.group(2)
    root = await storage_path("backups", db)
    b = Backup(app_id=app.id, status="success", trigger="import", path=str(Path(root) / app.slug / stamp),
               remote_key=f"target:{tid}:{body.key}", items=[], finished_at=datetime.now(UTC))
    db.add(b)
    await db.commit()
    await audit(db, user, "backup.import_remote", app.slug, {"key": body.key}, request)
    return {"backup_id": b.id}


@router.post("/apps/{app_id}/backup-now")
async def backup_now(app_id: int, user: CurrentUser, db: DB):
    app = await check_app(user, app_id, db, "deployer")
    job = await enqueue(db, "backup", {"app_id": app.id, "trigger": "manual"}, app.id, user.id)
    return {"job_id": job.id}


# ======================================================================= dung lượng theo stack
@router.get("/usage")
async def stack_usage(user: StaffUser, db: DB):
    data = await get_setting("stack_usage", db) or {}
    running = (await db.execute(select(Job.id).where(Job.kind == "stack_usage", Job.status.in_(("queued", "claimed", "running"))).limit(1))).scalar()
    return {**data, "job_id": running}


@router.post("/usage/refresh")
async def refresh_usage(user: StaffUser, db: DB):
    running = (await db.execute(select(Job.id).where(Job.kind == "stack_usage", Job.status.in_(("queued", "claimed", "running"))).limit(1))).scalar()
    if running:
        return {"job_id": running}
    job = await enqueue(db, "stack_usage", {}, None, user.id)
    return {"job_id": job.id}


@job_handler("stack_usage")
async def job_stack_usage(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    await refresh_stack_usage(ctx)
    return {"ok": True}


async def refresh_stack_usage(ctx: JobContext | None = None) -> None:
    from ..db import SessionLocal

    async with SessionLocal() as s:
        root = await storage_path("backups", s)
        apps = {a.compose_project: {"id": a.id, "name": a.name, "slug": a.slug} for a in (await s.execute(select(App))).scalars()}
    if ctx:
        await ctx.log("Đang tính dung lượng (du) các thư mục, volume, container…")
    data = await asyncio.to_thread(stack_usage_svc.collect, str(root) if root else None, apps)
    async with SessionLocal() as s:
        await set_setting("stack_usage", data, s)
        await s.commit()
    if ctx:
        await ctx.log(f"Xong: {len(data['stacks'])} stack trong {data['duration']}s")
