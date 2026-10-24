from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from ..core.deps import DB, CurrentUser, StaffUser, check_app
from ..core.jobs import enqueue
from ..models import ScanResult

router = APIRouter(prefix="/api/security", tags=["security"])


class ScanIn(BaseModel):
    kind: str = "all"  # all | config | images
    images: list[str] | None = None


@router.post("/scan")
async def scan(body: ScanIn, user: StaffUser, db: DB):
    jobs = []
    if body.kind in ("all", "config"):
        jobs.append((await enqueue(db, "scan_config", {}, None, user.id)).id)
    if body.kind in ("all", "images"):
        jobs.append((await enqueue(db, "scan_images", {"images": body.images or []}, None, user.id)).id)
    return {"job_ids": jobs}


@router.post("/apps/{app_id}/scan")
async def scan_app(app_id: int, user: CurrentUser, db: DB):
    app = await check_app(user, app_id, db, "deployer")
    from ..services import docker_svc

    images = sorted({c.attrs["Config"]["Image"] for c in await docker_svc.project_containers(app.compose_project)})
    job = await enqueue(db, "scan_images", {"images": images}, app.id, user.id)
    return {"job_id": job.id}


@router.get("/overview")
async def overview(user: CurrentUser, db: DB):
    """Kết quả mới nhất: 1 bản config + bản mới nhất của mỗi image."""
    config = (await db.execute(select(ScanResult).where(ScanResult.kind == "config").order_by(ScanResult.id.desc()).limit(1))).scalar_one_or_none()
    latest_ids = select(func.max(ScanResult.id)).where(ScanResult.kind == "image").group_by(ScanResult.target)
    images = (await db.execute(select(ScanResult).where(ScanResult.id.in_(latest_ids)).order_by(ScanResult.created_at.desc()))).scalars().all()
    if user.role == "member":
        from ..core.deps import visible_app_ids

        ids = await visible_app_ids(user, db) or set()
        images = [i for i in images if i.app_id in ids]
        config = None
    return {
        "config": {"id": config.id, "created_at": config.created_at, "summary": config.summary, "findings": config.findings} if config else None,
        "images": [{"id": i.id, "target": i.target, "app_id": i.app_id, "summary": i.summary, "created_at": i.created_at} for i in images],
    }


@router.get("/results/{rid}")
async def result(rid: int, user: CurrentUser, db: DB):
    r = await db.get(ScanResult, rid)
    if r is None:
        raise HTTPException(404)
    if user.role == "member":
        if r.app_id is None:
            raise HTTPException(403)
        await check_app(user, r.app_id, db)
    return {"id": r.id, "kind": r.kind, "target": r.target, "summary": r.summary, "findings": r.findings, "created_at": r.created_at}
