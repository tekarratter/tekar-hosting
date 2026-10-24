import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("enc:" + settings.secret_key).encode()).digest()))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_jwt(user_id: int, extra: dict | None = None, ttl_hours: int | None = None, ver: int = 0) -> str:
    """ver = User.token_version: tăng khi đổi mật khẩu / reset 2FA để vô hiệu mọi phiên cũ."""
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=ttl_hours or settings.jwt_ttl_hours), "ver": int(ver or 0)}
    payload.update(extra or {})
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def encrypt(value: str | None) -> str:
    if not value:
        return ""
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def random_password(n: int = 24) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def new_api_token() -> tuple[str, str, str]:
    """Trả về (token, prefix, sha256)."""
    raw = "th_" + secrets.token_urlsafe(32)
    return raw, raw[:10], hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
