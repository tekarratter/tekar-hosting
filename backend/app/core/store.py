"""Cấu hình hệ thống lưu trong bảng settings. Khoá có hậu tố `_secret` được mã hoá."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import Setting
from .security import decrypt, encrypt

DEFAULTS: dict[str, Any] = {
    "base_domain": "",
    "wildcard_enabled": False,
    "cloudflare_api_token_secret": "",
    "cloudflare_account_id": "",
    "cloudflare_zone_id": "",
    "cloudflare_tunnel_id": "",
    "cloudflare_tunnel_name": "tekar-hosting",
    "cloudflare_tunnel_token_secret": "",
    "acme_email": "",
    "anthropic_api_key_secret": "",
    "claude_model": "claude-opus-5-5",
    "claude_effort": "medium",
    "claude_engine": "auto",  # auto | api | subscription
    "claude_sub_model": "",  # model cho Claude Code (gói Claude); trống = mặc định của gói
    "claude_oauth_token_secret": "",  # token dài hạn từ `claude setup-token` (thay cho đăng nhập trong container)
    "telegram_bot_token_secret": "",
    "telegram_chat_id": "",
    "webhook_url": "",
    "alert_cpu_pct": 90,
    "alert_mem_pct": 90,
    "alert_disk_pct": 85,
    # cảnh báo ổ đĩa qua Telegram kèm nút xoá image cũ (bật thì thay cho cảnh báo ổ đĩa thường)
    "disk_tg_enabled": False,
    "disk_tg_pct": 80,
    "disk_tg_image_days": 14,
    # môi trường phát triển (app devbox): host hiển thị trong lệnh ssh + dải cổng SSH cấp cho từng app
    "dev_ssh_host": "",
    "dev_ssh_ports": "2201-2299",
    "scan_cron": "0 3 * * *",
    "report_cron": "",
    # sao lưu chính panel (DB + cert Caddy + .env) — xem services/panel_backup.py
    "panel_backup_cron": "15 3 * * *",
    "panel_backup_keep": 7,
    "panel_backup_target_id": "",
    "registry_auths_secret": "",  # JSON: [{"registry","username","password"}]
    "public_panel_url": "",
    "cloudflare_zone_name": "",
    # proxy
    "caddy_public": False,  # Caddy giữ cổng 80/443 public (đã thay NPM) → bật ACME HTTP-01
    "caddy_http_port": None,  # None = lấy từ TH_CADDY_HTTP_PORT
    "caddy_https_port": None,
    "proxy_default_site": {"mode": "404", "value": ""},
    "npm_stopped_containers": [],
    # lưu trữ vendor-free (rỗng = mặc định trong TH_DATA_DIR)
    "storage_apps_dir": "",
    "storage_backups_dir": "",
    "storage_volumes_dir": "",  # đặt → volume của app mới lưu thành thư mục <dir>/<slug>/<volume>
    "storage_data_dir": "",  # đặt → app mới có APP_DATA_DIR=<dir>/<slug> để bind mount dữ liệu
    "default_backup_destination_id": None,
}


async def storage_path(kind: str, db: AsyncSession | None = None):
    """Thư mục lưu trữ theo cấu hình (apps | backups | volumes | data). volumes/data trả None nếu không đặt."""
    from pathlib import Path

    from ..config import settings

    v = (await get_setting(f"storage_{kind}_dir", db) or "").strip()
    if v:
        return Path(v)
    return {"apps": settings.apps_dir, "backups": settings.backups_dir}.get(kind)


def _is_secret(key: str) -> bool:
    return key.endswith("_secret")


async def get_setting(key: str, db: AsyncSession | None = None) -> Any:
    async def _get(s: AsyncSession) -> Any:
        row = await s.get(Setting, key)
        if row is None:
            return DEFAULTS.get(key)
        return decrypt(row.value) if _is_secret(key) else row.value

    if db is not None:
        return await _get(db)
    async with SessionLocal() as s:
        return await _get(s)


async def set_setting(key: str, value: Any, db: AsyncSession) -> None:
    stored = encrypt(value) if _is_secret(key) else value
    row = await db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=stored))
    else:
        row.value = stored


async def all_settings(db: AsyncSession, reveal: bool = False) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        row = await db.get(Setting, key)
        if row is None:
            continue
        if _is_secret(key):
            plain = decrypt(row.value)
            out[key] = plain if reveal else ("••••" + plain[-4:] if plain else "")
        else:
            out[key] = row.value
    return out
