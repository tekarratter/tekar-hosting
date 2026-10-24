from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.audit import audit
from ..core.deps import DB, CurrentUser, StaffUser, check_app
from ..core.security import decrypt, encrypt, random_password
from ..models import App, PlatformService, ServiceBinding, User
from ..services import platform

router = APIRouter(prefix="/api/platform", tags=["platform"])
KINDS = ("postgres", "redis", "s3")


def svc_out(s: PlatformService, bindings: int = 0) -> dict[str, Any]:
    return {
        "id": s.id, "name": s.name, "kind": s.kind, "mode": s.mode, "host": s.host, "port": s.port,
        "public_host": s.public_host, "public_port": s.public_port, "admin_user": s.admin_user,
        "extra": s.extra or {}, "container_name": s.container_name, "created_at": s.created_at, "bindings": bindings,
    }


@router.get("/services")
async def list_services(user: CurrentUser, db: DB):
    counts = dict((await db.execute(select(ServiceBinding.service_id, func.count()).group_by(ServiceBinding.service_id))).all())
    rows = (await db.execute(select(PlatformService).order_by(PlatformService.kind, PlatformService.name))).scalars().all()
    if user.role == "member":
        return [{"id": s.id, "name": s.name, "kind": s.kind} for s in rows]
    return [svc_out(s, counts.get(s.id, 0)) for s in rows]


class ServiceIn(BaseModel):
    name: str
    kind: str
    host: str
    port: int
    public_host: str | None = None
    public_port: int | None = None
    admin_user: str = ""
    admin_password: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


@router.post("/services")
async def add_service(body: ServiceIn, user: StaffUser, db: DB, request: Request):
    if body.kind not in KINDS:
        raise HTTPException(400, "kind phải là postgres | redis | s3")
    s = PlatformService(
        name=body.name, kind=body.kind, mode="external", host=body.host, port=body.port,
        public_host=body.public_host or None, public_port=body.public_port, admin_user=body.admin_user,
        admin_password_enc=encrypt(body.admin_password or ""), extra=body.extra,
    )
    try:
        info = await platform.test_connection(s)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Không kết nối được: {e}") from e
    db.add(s)
    await db.commit()
    await audit(db, user, "platform.add", s.name, {"kind": s.kind, "host": s.host}, request)
    return {**svc_out(s), "info": info}


@router.put("/services/{sid}")
async def update_service(sid: int, body: ServiceIn, user: StaffUser, db: DB, request: Request):
    s = await db.get(PlatformService, sid)
    if s is None:
        raise HTTPException(404)
    s.name, s.host, s.port = body.name, body.host, body.port
    s.public_host, s.public_port, s.admin_user, s.extra = body.public_host or None, body.public_port, body.admin_user, body.extra
    if body.admin_password:
        s.admin_password_enc = encrypt(body.admin_password)
    try:
        await platform.test_connection(s)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Không kết nối được: {e}") from e
    await db.commit()
    await audit(db, user, "platform.update", s.name, request=request)
    return svc_out(s)


class ManagedIn(BaseModel):
    name: str
    kind: str


@router.post("/services/managed")
async def create_managed(body: ManagedIn, user: StaffUser, db: DB, request: Request):
    if body.kind not in KINDS:
        raise HTTPException(400, "kind không hợp lệ")
    if (await db.execute(select(PlatformService).where(PlatformService.name == body.name))).scalar_one_or_none():
        raise HTTPException(400, "Tên đã tồn tại")
    info = await platform.create_managed(body.kind, body.name)
    s = PlatformService(
        name=body.name, kind=body.kind, mode="managed", host=info["host"], port=info["port"], admin_user=info["admin_user"],
        admin_password_enc=encrypt(info["admin_password"]), container_name=info["container_name"],
        extra={"provider": "minio"} if body.kind == "s3" else {},
    )
    db.add(s)
    await db.commit()
    await audit(db, user, "platform.create_managed", s.name, {"kind": s.kind}, request)
    return svc_out(s)


@router.get("/services/{sid}")
async def get_service(sid: int, user: StaffUser, db: DB):
    s = await db.get(PlatformService, sid)
    if s is None:
        raise HTTPException(404)
    try:
        info = await platform.test_connection(s)
        online = True
    except Exception as e:  # noqa: BLE001
        info, online = {"error": str(e)}, False
    rows = (await db.execute(select(ServiceBinding, App).join(App, App.id == ServiceBinding.app_id).where(ServiceBinding.service_id == sid))).all()
    return {
        **svc_out(s, len(rows)), "online": online, "info": info,
        "bindings": [{"id": b.id, "app_id": a.id, "app": a.name, "resource": b.resource, "username": b.username} for b, a in rows],
    }


@router.get("/services/{sid}/credentials")
async def service_credentials(sid: int, user: StaffUser, db: DB, request: Request):
    s = await db.get(PlatformService, sid)
    if s is None:
        raise HTTPException(404)
    await audit(db, user, "platform.reveal", s.name, request=request)
    return {"admin_user": s.admin_user, "admin_password": decrypt(s.admin_password_enc)}


@router.delete("/services/{sid}")
async def delete_service(sid: int, user: StaffUser, db: DB, request: Request, remove_container: bool = False):
    s = await db.get(PlatformService, sid)
    if s is None:
        raise HTTPException(404)
    n = (await db.execute(select(func.count()).where(ServiceBinding.service_id == sid))).scalar()
    if n:
        raise HTTPException(400, f"Còn {n} app đang dùng dịch vụ này")
    if remove_container and s.container_name:
        from ..services import docker_svc

        c = await docker_svc.get_container(s.container_name)
        if c:
            await docker_svc.run(c.remove, force=True)
    await db.delete(s)
    await db.commit()
    await audit(db, user, "platform.delete", s.name, request=request)
    return {"ok": True}


# ---------------------------------------------------------------- bindings
async def bind_service(db: AsyncSession, app: App, service_id: int, env_prefix: str, user: User, resource: str | None = None) -> ServiceBinding:
    s = await db.get(PlatformService, service_id)
    if s is None:
        raise HTTPException(404, "Không thấy dịch vụ")
    base = platform.ident(app.slug)
    if s.kind == "s3":
        resource = (resource or app.slug).lower().replace("_", "-")[:63]
        username = f"th-{app.slug}"[:60]
    else:
        resource = platform.ident(resource or base)
        username = platform.ident(f"th_{base}")
    exists = (
        await db.execute(select(ServiceBinding).where(ServiceBinding.service_id == service_id, ServiceBinding.resource == resource))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(400, f"{resource} đã được cấp trên {s.name}")
    password = random_password(28)
    try:
        await platform.provision(s, resource, username, password)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Cấp tài nguyên thất bại: {e}") from e
    b = ServiceBinding(app_id=app.id, service_id=s.id, resource=resource, username=username, password_enc=encrypt(password), env_prefix=env_prefix)
    db.add(b)
    await db.commit()
    await db.refresh(b)
    await audit(db, user, "platform.bind", app.slug, {"service": s.name, "resource": resource})
    return b


def binding_out(b: ServiceBinding, reveal: bool = False) -> dict[str, Any]:
    env = platform.binding_env(b)
    if not reveal:
        env = {k: ("***" if any(x in k for x in ("PASSWORD", "SECRET", "URL")) else v) for k, v in env.items()}
    return {
        "id": b.id, "service_id": b.service_id, "service": b.service.name, "kind": b.service.kind, "resource": b.resource,
        "username": b.username, "env_prefix": b.env_prefix, "include_in_backup": b.include_in_backup, "env": env,
        "created_at": b.created_at,
    }


@router.get("/apps/{app_id}/bindings")
async def app_bindings(app_id: int, user: CurrentUser, db: DB, reveal: bool = False):
    app = await check_app(user, app_id, db, "deployer" if reveal else "viewer")
    rows = (await db.execute(select(ServiceBinding).where(ServiceBinding.app_id == app.id))).scalars().all()
    if reveal:
        await audit(db, user, "platform.reveal_binding", app.slug)
    return [binding_out(b, reveal) for b in rows]


class BindIn(BaseModel):
    service_id: int
    env_prefix: str = ""
    resource: str | None = None


@router.post("/apps/{app_id}/bindings")
async def create_binding(app_id: int, body: BindIn, user: CurrentUser, db: DB):
    app = await check_app(user, app_id, db, "owner")
    b = await bind_service(db, app, body.service_id, body.env_prefix.upper(), user, body.resource)
    return binding_out(b)


class BindUpdate(BaseModel):
    include_in_backup: bool


@router.put("/apps/{app_id}/bindings/{bid}")
async def update_binding(app_id: int, bid: int, body: BindUpdate, user: CurrentUser, db: DB):
    await check_app(user, app_id, db, "deployer")
    b = await db.get(ServiceBinding, bid)
    if b is None or b.app_id != app_id:
        raise HTTPException(404)
    b.include_in_backup = body.include_in_backup
    await db.commit()
    return binding_out(b)


@router.post("/apps/{app_id}/bindings/{bid}/rotate")
async def rotate_binding(app_id: int, bid: int, user: CurrentUser, db: DB):
    app = await check_app(user, app_id, db, "owner")
    b = await db.get(ServiceBinding, bid)
    if b is None or b.app_id != app_id:
        raise HTTPException(404)
    pw = random_password(28)
    await platform.provision(b.service, b.resource, b.username, pw)
    b.password_enc = encrypt(pw)
    await db.commit()
    await audit(db, user, "platform.rotate", app.slug, {"resource": b.resource})
    return {"ok": True, "note": "Deploy lại app để nhận mật khẩu mới"}


@router.delete("/apps/{app_id}/bindings/{bid}")
async def delete_binding(app_id: int, bid: int, user: CurrentUser, db: DB, drop_data: bool = False):
    app = await check_app(user, app_id, db, "owner")
    b = await db.get(ServiceBinding, bid)
    if b is None or b.app_id != app_id:
        raise HTTPException(404)
    try:
        await platform.deprovision(b.service, b.resource, b.username, drop_data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Thu hồi thất bại: {e}") from e
    await db.delete(b)
    await db.commit()
    await audit(db, user, "platform.unbind", app.slug, {"resource": b.resource, "drop_data": drop_data})
    return {"ok": True}
