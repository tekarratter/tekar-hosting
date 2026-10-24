from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..core.audit import audit
from ..core.deps import DB, CurrentUser, check_app, is_staff
from ..models import App, User
from ..services import docker_svc

router = APIRouter(prefix="/api/containers", tags=["containers"])


async def authorize_container(user: User, name: str, db, min_role: str = "viewer"):
    """Container thuộc app → kiểm tra quyền app; container ngoài app → chỉ staff."""
    c = await docker_svc.get_container(name)
    if c is None:
        raise HTTPException(404, "Không thấy container")
    if is_staff(user):
        return c
    project = (c.labels or {}).get(docker_svc.COMPOSE_PROJECT)
    app = (await db.execute(select(App).where(App.compose_project == project))).scalar_one_or_none() if project else None
    if app is None:
        raise HTTPException(403, "Không có quyền với container này")
    await check_app(user, app.id, db, min_role)
    return c


@router.get("")
async def list_all(user: CurrentUser, db: DB):
    items = await docker_svc.list_containers()
    if is_staff(user):
        return items
    from ..core.deps import visible_app_ids

    ids = await visible_app_ids(user, db) or set()
    projects = set((await db.execute(select(App.compose_project).where(App.id.in_(ids or [-1])))).scalars())
    return [c for c in items if c["project"] in projects]


@router.get("/{name}")
async def inspect(name: str, user: CurrentUser, db: DB):
    c = await authorize_container(user, name, db)
    return docker_svc.inspect_safe(c)


@router.get("/{name}/logs")
async def logs(name: str, user: CurrentUser, db: DB, tail: int = 300):
    c = await authorize_container(user, name, db)
    return {"logs": await docker_svc.logs(c.id, tail=min(tail, 5000))}


@router.post("/{name}/{verb}")
async def action(name: str, verb: str, user: CurrentUser, db: DB, request: Request):
    if verb not in ("start", "stop", "restart", "kill"):
        raise HTTPException(400)
    c = await authorize_container(user, name, db, "deployer")
    if c.name.startswith("th-api") or c.name == "th-db":
        raise HTTPException(400, "Không thao tác container lõi của panel tại đây")
    await docker_svc.run(getattr(c, verb))
    await audit(db, user, f"container.{verb}", c.name, request=request)
    return {"ok": True}
