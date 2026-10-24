from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ApiToken, App, AppMember, User
from .security import decode_jwt, hash_token

ROLE_RANK = {"viewer": 1, "deployer": 2, "owner": 3}
COOKIE = "th_session"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


async def auth_from_token(token: str | None, db: AsyncSession) -> tuple[User | None, bool]:
    """→ (user, read_only). API token th_* có thể chỉ đọc; JWT bị vô hiệu khi token_version của user tăng
    (đổi mật khẩu, reset 2FA...)."""
    if not token:
        return None, False
    if token.startswith("th_"):
        row = (await db.execute(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))).scalar_one_or_none()
        if row is None or not row.user.is_active:
            return None, False
        row.last_used_at = datetime.now(UTC)
        await db.commit()
        return row.user, bool(row.read_only)
    payload = decode_jwt(token)
    if not payload or payload.get("purpose"):
        return None, False
    user = await db.get(User, int(payload["sub"]))
    if user is None or not user.is_active or int(payload.get("ver") or 0) != int(user.token_version or 0):
        return None, False
    return user, False


async def user_from_token(token: str | None, db: AsyncSession) -> User | None:
    return (await auth_from_token(token, db))[0]


def _extract_token(request: Request | WebSocket) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    tok = request.cookies.get(COOKIE)
    # ?token= chỉ cho WebSocket (trình duyệt không gửi được header); với HTTP sẽ lọt vào access log / lịch sử
    if not tok and request.scope.get("type") == "websocket":
        tok = request.query_params.get("token")
    return tok


async def current_user(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> User:
    user, read_only = await auth_from_token(_extract_token(request), db)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Chưa đăng nhập")
    if read_only and request.method not in SAFE_METHODS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token chỉ đọc")
    request.state.read_only = read_only
    return user


async def ws_user(ws: WebSocket, db: AsyncSession) -> User | None:
    user, read_only = await auth_from_token(_extract_token(ws), db)
    return None if read_only else user  # terminal/log stream là thao tác ghi hoặc nhạy cảm


def client_ip(request: Request | None) -> str:
    """IP khách. uvicorn (--proxy-headers, --forwarded-allow-ips = mạng docker của Caddy/NPM) đã thay client
    bằng địa chỉ đáng tin trong X-Forwarded-For; không tin header do client tự gửi (cf-connecting-ip...)."""
    if request is None or request.client is None:
        return ""
    return request.client.host


CurrentUser = Annotated[User, Depends(current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]


def is_staff(user: User) -> bool:
    return user.role in ("admin", "operator")


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cần quyền admin")
    return user


def require_staff(user: CurrentUser) -> User:
    if not is_staff(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cần quyền admin/operator")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
StaffUser = Annotated[User, Depends(require_staff)]


async def app_role(user: User, app: App, db: AsyncSession) -> str | None:
    if is_staff(user):
        return "owner"
    if app.owner_id == user.id:
        return "owner"
    m = (
        await db.execute(select(AppMember).where(AppMember.app_id == app.id, AppMember.user_id == user.id))
    ).scalar_one_or_none()
    return m.role if m else None


async def check_app(user: User, app_id: int, db: AsyncSession, min_role: str = "viewer") -> App:
    app = await db.get(App, app_id)
    if app is None:
        raise HTTPException(404, "Không tìm thấy ứng dụng")
    role = await app_role(user, app, db)
    if role is None or ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise HTTPException(403, f"Cần quyền {min_role} trên ứng dụng này")
    return app


async def visible_app_ids(user: User, db: AsyncSession) -> set[int] | None:
    """None = thấy tất cả."""
    if is_staff(user):
        return None
    owned = (await db.execute(select(App.id).where(App.owner_id == user.id))).scalars().all()
    member = (await db.execute(select(AppMember.app_id).where(AppMember.user_id == user.id))).scalars().all()
    return set(owned) | set(member)
