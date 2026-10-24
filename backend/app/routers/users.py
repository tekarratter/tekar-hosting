from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from ..core.audit import audit
from ..core.deps import DB, AdminUser, CurrentUser
from ..core.security import hash_password
from ..models import App, AppMember, User
from .auth import user_out

router = APIRouter(prefix="/api/users", tags=["users"])
ROLES = ("admin", "operator", "member")


@router.get("")
async def list_users(user: CurrentUser, db: DB):
    rows = (await db.execute(select(User).order_by(User.id))).scalars().all()
    if user.role == "admin":
        return [user_out(u) for u in rows]
    # user thường chỉ cần danh sách để chọn thành viên
    return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role} for u in rows if u.is_active]


class UserIn(BaseModel):
    email: str
    name: str = ""
    password: str | None = None
    role: str = "member"
    is_active: bool = True


@router.post("")
async def create_user(body: UserIn, admin: AdminUser, db: DB, request: Request):
    if body.role not in ROLES:
        raise HTTPException(400, "Role không hợp lệ")
    if not body.password or len(body.password) < 8:
        raise HTTPException(400, "Mật khẩu tối thiểu 8 ký tự")
    if (await db.execute(select(User).where(func.lower(User.email) == body.email.lower()))).scalar_one_or_none():
        raise HTTPException(400, "Email đã tồn tại")
    u = User(email=body.email.lower().strip(), name=body.name, role=body.role, is_active=body.is_active, password_hash=hash_password(body.password))
    db.add(u)
    await db.commit()
    await audit(db, admin, "user.create", u.email, {"role": u.role}, request)
    return user_out(u)


@router.put("/{user_id}")
async def update_user(user_id: int, body: UserIn, admin: AdminUser, db: DB, request: Request):
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    if body.role not in ROLES:
        raise HTTPException(400, "Role không hợp lệ")
    if u.id == admin.id and (body.role != "admin" or not body.is_active):
        raise HTTPException(400, "Không thể tự hạ quyền / khoá chính mình")
    u.name, u.role, u.is_active = body.name, body.role, body.is_active
    if body.password:
        u.password_hash = hash_password(body.password)
    if body.password or not body.is_active:
        u.token_version = (u.token_version or 0) + 1  # vô hiệu phiên đang mở của user đó
    await db.commit()
    await audit(db, admin, "user.update", u.email, {"role": u.role, "active": u.is_active}, request)
    return user_out(u)


@router.post("/{user_id}/reset-2fa")
async def reset_2fa(user_id: int, admin: AdminUser, db: DB, request: Request):
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    u.totp_secret = None
    u.token_version = (u.token_version or 0) + 1
    await db.commit()
    await audit(db, admin, "user.reset_2fa", u.email, request=request)
    return {"ok": True}


@router.delete("/{user_id}")
async def delete_user(user_id: int, admin: AdminUser, db: DB, request: Request):
    u = await db.get(User, user_id)
    if u is None or u.id == admin.id:
        raise HTTPException(400, "Không thể xoá")
    await db.delete(u)
    await db.commit()
    await audit(db, admin, "user.delete", u.email, request=request)
    return {"ok": True}


@router.get("/{user_id}/apps")
async def user_apps(user_id: int, admin: AdminUser, db: DB):
    rows = (await db.execute(select(AppMember, App).join(App, App.id == AppMember.app_id).where(AppMember.user_id == user_id))).all()
    owned = (await db.execute(select(App).where(App.owner_id == user_id))).scalars().all()
    return {
        "owned": [{"id": a.id, "slug": a.slug, "name": a.name} for a in owned],
        "member": [{"id": a.id, "slug": a.slug, "name": a.name, "role": m.role} for m, a in rows],
    }
