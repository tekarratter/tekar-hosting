"""Cảnh báo: lưu bảng alerts (chống trùng theo key) + gửi Telegram / webhook."""

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from ..core.store import get_setting
from ..db import SessionLocal
from ..models import Alert

log = logging.getLogger("th.notify")
ICON = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨", "resolved": "✅"}


async def send_external(level: str, title: str, message: str) -> None:
    text = f"{ICON.get(level, '')} [Tekar Hosting] {title}\n{message}".strip()
    token = await get_setting("telegram_bot_token_secret")
    chat_id = await get_setting("telegram_chat_id")
    hook = await get_setting("webhook_url")
    async with httpx.AsyncClient(timeout=10) as http:
        if token and chat_id:
            try:
                await http.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text[:4000]})
            except httpx.HTTPError as e:
                log.warning("telegram: %s", e)
        if hook:
            try:
                await http.post(hook, json={"level": level, "title": title, "message": message})
            except httpx.HTTPError as e:
                log.warning("webhook: %s", e)


async def notify(level: str, title: str, message: str = "", key: str | None = None, send: bool = True) -> bool:
    """Tạo cảnh báo. Nếu key đang có cảnh báo chưa resolve thì bỏ qua. Trả về True nếu là cảnh báo mới."""
    key = key or title
    async with SessionLocal() as s:
        existing = (await s.execute(select(Alert).where(Alert.key == key, Alert.resolved.is_(False)))).scalar_one_or_none()
        if existing:
            return False
        s.add(Alert(key=key, level=level, title=title, message=message))
        await s.commit()
    if send:
        await send_external(level, title, message)
    return True


async def resolve(key: str, message: str = "") -> None:
    async with SessionLocal() as s:
        rows = (await s.execute(select(Alert).where(Alert.key == key, Alert.resolved.is_(False)))).scalars().all()
        if not rows:
            return
        for r in rows:
            r.resolved = True
            r.resolved_at = datetime.now(UTC)
        await s.commit()
        title = rows[0].title
    await send_external("resolved", f"Đã ổn: {title}", message)
