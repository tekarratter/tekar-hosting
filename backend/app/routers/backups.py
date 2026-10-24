from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from ..core.audit import audit
from ..core.deps import DB, CurrentUser, check_app, visible_app_ids
from ..core.jobs import enqueue
from ..models import App, Backup
from ..services.backup import delete_backup_files

router = APIRouter(prefix="/api/backups", tags=["backups"])


def backup_out(b: Backup, app_name: str | None = None) -> dict:
    return {
        "id": b.id, "app_id": b.app_id, "app_name": app_name, "status": b.status, "trigger": b.trigger,
        "path": b.path, "remote_key": b.remote_key, "size_bytes": b.size_bytes, "items": b.items,
        "error": b.error, "started_at": b.started_at, "finished_at": b.finished_at,
    }


@router.get("")
async def list_backups(user: CurrentUser, db: DB, app_id: int | None = None, limit: int = 100):
    q = select(Backup, App.name).join(App, App.id == Backup.app_id).order_by(Backup.id.desc()).limit(limit)
    if app_id is not None:
        await check_app(user, app_id, db)
        q = q.where(Backup.app_id == app_id)
    else:
        ids = await visible_app_ids(user, db)
        if ids is not None:
            q = q.where(Backup.app_id.in_(ids or [-1]))
    return [backup_out(b, n) for b, n in (await db.execute(q)).all()]


@router.post("/apps/{app_id}")
async def run_backup(app_id: int, user: CurrentUser, db: DB, request: Request):
    app = await check_app(user, app_id, db, "deployer")
    job = await enqueue(db, "backup", {"app_id": app.id, "trigger": "manual"}, app.id, user.id)
    await audit(db, user, "backup.run", app.slug, request=request)
    return {"job_id": job.id}


class RestoreIn(BaseModel):
    items: list[str] | None = None  # danh sách file trong backup; None = tất cả
    confirm: str


@router.post("/{backup_id}/restore")
async def restore(backup_id: int, body: RestoreIn, user: CurrentUser, db: DB, request: Request):
    b = await db.get(Backup, backup_id)
    if b is None or b.status != "success":
        raise HTTPException(400, "Backup không hợp lệ")
    app = await check_app(user, b.app_id, db, "owner")
    if body.confirm != app.slug:
        raise HTTPException(400, "Nhập đúng slug để xác nhận")
    job = await enqueue(db, "restore", {"backup_id": b.id, "items": body.items}, app.id, user.id)
    await audit(db, user, "backup.restore", app.slug, {"backup_id": b.id, "items": body.items}, request)
    return {"job_id": job.id}


@router.delete("/{backup_id}")
async def delete(backup_id: int, user: CurrentUser, db: DB, request: Request):
    b = await db.get(Backup, backup_id)
    if b is None:
        raise HTTPException(404)
    app = await check_app(user, b.app_id, db, "owner")
    await delete_backup_files(b, db)
    await db.delete(b)
    await db.commit()
    await audit(db, user, "backup.delete", app.slug, {"backup_id": backup_id}, request)
    return {"ok": True}
