import time
from datetime import UTC, datetime

import pyotp
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from ..config import settings
from ..core.audit import audit
from ..core.deps import COOKIE, DB, CurrentUser, client_ip
from ..core.security import create_jwt, decode_jwt, hash_password, new_api_token, verify_password
from ..models import ApiToken, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# chống dò mật khẩu: 5 lần sai / 5 phút cho mỗi (IP, email) + 20 lần / 15 phút cho mỗi email (mọi IP)
_FAILS: dict[str, list[float]] = {}
LIMITS = {"ip": (5, 300), "email": (20, 900)}


def _keys(request: Request, email: str) -> dict[str, str]:
    email = email.lower().strip()
    return {"ip": f"{client_ip(request)}|{email}", "email": f"email|{email}"}


def _throttled(keys: dict[str, str]) -> bool:
    now = time.time()
    hit = False
    for kind, key in keys.items():
        limit, window = LIMITS[kind]
        _FAILS[key] = [t for t in _FAILS.get(key, []) if now - t < window]
        hit = hit or len(_FAILS[key]) >= limit
    if len(_FAILS) > 10000:  # tránh phình bộ nhớ khi bị quét
        for k in [k for k, v in _FAILS.items() if not v]:
            _FAILS.pop(k, None)
    return hit


def _fail(keys: dict[str, str]) -> None:
    for key in keys.values():
        _FAILS.setdefault(key, []).append(time.time())


def _set_session(response: Response, request: Request, u: User) -> str:
    token = create_jwt(u.id, ver=u.token_version)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=secure, max_age=settings.jwt_ttl_hours * 3600)
    return token


class LoginIn(BaseModel):
    email: str
    password: str
    otp: str | None = None


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active, "totp": bool(u.totp_secret),
            "created_at": u.created_at, "last_login_at": u.last_login_at}


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: DB):
    keys = _keys(request, body.email)
    if _throttled(keys):
        raise HTTPException(429, "Sai quá nhiều lần, thử lại sau ít phút")
    u = (await db.execute(select(User).where(func.lower(User.email) == body.email.lower().strip()))).scalar_one_or_none()
    if u is None or not u.is_active or not verify_password(body.password, u.password_hash):
        _fail(keys)
        await audit(db, None, "auth.login_failed", body.email, request=request)
        raise HTTPException(401, "Sai email hoặc mật khẩu")
    if u.totp_secret:
        if not body.otp:
            return {"otp_required": True}
        if not pyotp.TOTP(u.totp_secret).verify(body.otp, valid_window=1):
            _fail(keys)
            raise HTTPException(401, "Mã OTP không đúng")
    for key in keys.values():
        _FAILS.pop(key, None)
    u.last_login_at = datetime.now(UTC)
    await db.commit()
    token = _set_session(response, request, u)
    await audit(db, u, "auth.login", request=request)
    return {"user": user_out(u), "token": token}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: CurrentUser):
    return user_out(user)


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/password")
async def change_password(body: PasswordIn, user: CurrentUser, db: DB, request: Request, response: Response):
    if not verify_password(body.current, user.password_hash):
        raise HTTPException(400, "Mật khẩu hiện tại không đúng")
    if len(body.new) < 8:
        raise HTTPException(400, "Mật khẩu tối thiểu 8 ký tự")
    user.password_hash = hash_password(body.new)
    user.token_version = (user.token_version or 0) + 1  # đăng xuất mọi phiên khác
    await db.commit()
    await audit(db, user, "auth.password_changed", request=request)
    return {"ok": True, "token": _set_session(response, request, user)}


@router.post("/totp/setup")
async def totp_setup(user: CurrentUser):
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(user.email, issuer_name="Tekar Hosting")
    return {"secret": secret, "uri": uri, "token": create_jwt(user.id, {"purpose": "totp", "secret": secret}, ttl_hours=1)}


class TotpIn(BaseModel):
    token: str
    otp: str


@router.post("/totp/enable")
async def totp_enable(body: TotpIn, user: CurrentUser, db: DB):
    p = decode_jwt(body.token)
    if not p or p.get("purpose") != "totp" or p.get("sub") != str(user.id):
        raise HTTPException(400, "Phiên thiết lập hết hạn")
    if not pyotp.TOTP(p["secret"]).verify(body.otp, valid_window=1):
        raise HTTPException(400, "Mã OTP không đúng")
    user.totp_secret = p["secret"]
    await db.commit()
    return {"ok": True}


class ConfirmPasswordIn(BaseModel):
    current: str
    otp: str | None = None
    new: str | None = None  # giữ tương thích client cũ


@router.post("/totp/disable")
async def totp_disable(body: ConfirmPasswordIn, user: CurrentUser, db: DB, request: Request, response: Response):
    if not verify_password(body.current, user.password_hash):
        raise HTTPException(400, "Mật khẩu không đúng")
    # cần cả mã OTP hiện tại: kẻ lấy được phiên + mật khẩu không tự tắt được 2FA
    if user.totp_secret and not (body.otp and pyotp.TOTP(user.totp_secret).verify(body.otp, valid_window=1)):
        raise HTTPException(400, "Cần mã OTP hiện tại để tắt 2FA")
    user.totp_secret = None
    user.token_version = (user.token_version or 0) + 1
    await db.commit()
    await audit(db, user, "auth.totp_disabled", request=request)
    return {"ok": True, "token": _set_session(response, request, user)}


# ---- API tokens (dùng cho MCP / CLI / CI)
@router.get("/tokens")
async def list_tokens(user: CurrentUser, db: DB):
    rows = (await db.execute(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id.desc()))).scalars().all()
    return [{"id": t.id, "name": t.name, "prefix": t.prefix, "read_only": t.read_only, "created_at": t.created_at, "last_used_at": t.last_used_at} for t in rows]


class TokenIn(BaseModel):
    name: str
    read_only: bool = False


@router.post("/tokens")
async def create_token(body: TokenIn, user: CurrentUser, db: DB, request: Request):
    raw, prefix, h = new_api_token()
    db.add(ApiToken(user_id=user.id, name=body.name[:100], prefix=prefix, token_hash=h, read_only=body.read_only))
    await db.commit()
    await audit(db, user, "token.create", body.name, {"read_only": body.read_only}, request=request)
    return {"token": raw, "prefix": prefix}


@router.delete("/tokens/{token_id}")
async def delete_token(token_id: int, user: CurrentUser, db: DB):
    t = await db.get(ApiToken, token_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(404)
    await db.delete(t)
    await db.commit()
    return {"ok": True}
