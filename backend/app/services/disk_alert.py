"""Cảnh báo ổ đĩa qua Telegram kèm nút inline "Xoá image cũ".

Worker long-poll `getUpdates` (chỉ nhận callback_query) nên không cần URL public / webhook. Bot phải dành riêng cho
panel: bot đã đặt webhook hoặc đang được app khác poll sẽ báo 409 và nút không hoạt động.
Nút chỉ chạy khi bấm trong đúng chat đã cấu hình và mang đúng nonce của lần cảnh báo gần nhất (dùng một lần).
"""

import asyncio
import logging
import os
import secrets
import time
from typing import Any

import httpx

from ..config import settings
from ..core.audit import audit
from ..core.store import get_setting, set_setting
from ..db import SessionLocal
from . import docker_svc
from .notify import notify, resolve

log = logging.getLogger("th.disk_alert")
KEY = "host:disk-tg"
PENDING = "disk_tg_pending"  # setting nội bộ (không có trong DEFAULTS → không hiện / không sửa được qua API)
NONCE_TTL = 7 * 86400


async def _cfg() -> dict[str, Any] | None:
    if not await get_setting("disk_tg_enabled"):
        return None
    token, chat = await get_setting("telegram_bot_token_secret"), await get_setting("telegram_chat_id")
    if not token or not chat:
        return None
    return {"token": token, "chat": str(chat), "pct": float(await get_setting("disk_tg_pct") or 80),
            "days": int(await get_setting("disk_tg_image_days") or 14)}


def disk_pct() -> float:
    du = os.statvfs(str(settings.apps_root))
    total = du.f_blocks * du.f_frsize
    return (total - du.f_bavail * du.f_frsize) / total * 100 if total else 0.0


def _gb(n: float) -> str:
    return f"{n / 1024**3:.1f} GB"


def _old_images(days: int) -> tuple[int, int]:
    """Số image không container nào dùng và tạo trước `days` ngày + tổng dung lượng (ước tính, layer chung tính trùng)."""
    cli = docker_svc.client()
    used = {c.attrs.get("Image") for c in cli.containers.list(all=True)}
    cutoff = time.time() - days * 86400
    n = size = 0
    for img in cli.api.images():
        if img["Id"] not in used and img.get("Created", 0) < cutoff:
            n += 1
            size += img.get("Size", 0)
    return n, size


def _prune(days: int) -> dict[str, Any]:
    r = docker_svc.client().images.prune(filters={"dangling": False, "until": f"{days * 24}h"})
    deleted = [d for d in (r.get("ImagesDeleted") or []) if d.get("Deleted")]
    untagged = [d for d in (r.get("ImagesDeleted") or []) if d.get("Untagged")]
    return {"untagged": len(untagged), "deleted_layers": len(deleted), "freed": r.get("SpaceReclaimed") or 0}


async def _tg(cfg: dict[str, Any], method: str, **payload: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=payload.pop("_timeout", 15)) as http:
            r = await http.post(f"https://api.telegram.org/bot{cfg['token']}/{method}", json=payload)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:  # không đưa URL (chứa token) vào log
        raise RuntimeError(f"telegram {method}: {type(e).__name__}") from None
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method}: {data.get('error_code')} {data.get('description')}")
    return data["result"]


async def send_alert(cfg: dict[str, Any], pct: float, test: bool = False) -> None:
    n, size = await docker_svc.run(_old_images, cfg["days"])
    nonce = secrets.token_urlsafe(9)
    text = (f"{'🧪 [THỬ] ' if test else ''}⚠️ [Tekar Hosting] Ổ đĩa đã dùng {pct:.0f}% (ngưỡng {cfg['pct']:.0f}%)\n"
            f"Image không dùng, cũ hơn {cfg['days']} ngày: {n} image (~{_gb(size)}).")
    markup = {"inline_keyboard": [[{"text": f"🗑 Xoá image cũ hơn {cfg['days']} ngày", "callback_data": f"prune:{nonce}"}]]} if n else None
    msg = await _tg(cfg, "sendMessage", chat_id=cfg["chat"], text=text, **({"reply_markup": markup} if markup else {}))
    async with SessionLocal() as s:
        await set_setting(PENDING, {"nonce": nonce, "ts": time.time(), "message_id": msg["message_id"], "text": text}, s)
        await s.commit()


async def check() -> None:
    """Gọi định kỳ từ worker: vượt ngưỡng → gửi 1 lần (đến khi xuống dưới ngưỡng − 5%)."""
    cfg = await _cfg()
    if not cfg:
        return
    pct = disk_pct()
    if pct >= cfg["pct"]:
        # bảng alerts chống gửi trùng; notify(send=False) chỉ ghi lại, tin Telegram có nút do send_alert gửi
        if await notify("warning", f"Ổ đĩa host {pct:.0f}%", f"Ngưỡng {cfg['pct']:.0f}% — đã gửi Telegram kèm nút xoá image cũ", key=KEY, send=False):
            try:
                await send_alert(cfg, pct)
            except Exception as e:  # noqa: BLE001
                log.warning("gửi cảnh báo ổ đĩa: %s", e)
    elif pct < cfg["pct"] - 5:
        await resolve(KEY, f"Ổ đĩa còn {pct:.0f}%")


async def _handle_callback(cfg: dict[str, Any], cq: dict[str, Any]) -> None:
    msg = cq.get("message") or {}
    pend = await get_setting(PENDING) or {}
    ok = (str((msg.get("chat") or {}).get("id")) == cfg["chat"] and cq.get("data") == f"prune:{pend.get('nonce')}"
          and time.time() - pend.get("ts", 0) < NONCE_TTL)
    if not ok:
        await _tg(cfg, "answerCallbackQuery", callback_query_id=cq["id"], text="Nút đã hết hạn hoặc không hợp lệ", show_alert=True)
        return
    async with SessionLocal() as s:  # dùng một lần
        await set_setting(PENDING, {}, s)
        await s.commit()
    who = (cq.get("from") or {}).get("username") or (cq.get("from") or {}).get("id")
    await _tg(cfg, "answerCallbackQuery", callback_query_id=cq["id"], text="Đang xoá image cũ…")
    base = pend.get("text") or msg.get("text") or ""
    await _tg(cfg, "editMessageText", chat_id=cfg["chat"], message_id=msg["message_id"], text=f"{base}\n\n⏳ Đang xoá (bấm bởi {who})…")
    try:
        before = disk_pct()
        r = await docker_svc.run(_prune, cfg["days"])
        after = disk_pct()
        result = (f"✅ Đã xoá {r['untagged']} tag image, {r['deleted_layers']} layer — giải phóng {_gb(r['freed'])}.\n"
                  f"Ổ đĩa: {before:.0f}% → {after:.0f}%.")
    except Exception as e:  # noqa: BLE001
        r, result = {"error": str(e)}, f"❌ Lỗi khi xoá: {e}"
    await _tg(cfg, "editMessageText", chat_id=cfg["chat"], message_id=msg["message_id"], text=f"{base}\n\n{result}")
    async with SessionLocal() as s:
        await audit(s, None, "system.prune_images", "telegram", {"by": who, "days": cfg["days"], **r})
    log.info("prune image qua Telegram (%s): %s", who, r)


async def poll_loop() -> None:
    """Long-poll getUpdates khi tính năng bật. Chỉ một worker được chạy vòng này."""
    offset = 0
    while True:
        cfg = await _cfg()
        if not cfg:
            await asyncio.sleep(60)
            continue
        try:
            updates = await _tg(cfg, "getUpdates", offset=offset, timeout=50, allowed_updates=["callback_query"], _timeout=60)
            for u in updates:
                offset = u["update_id"] + 1
                if u.get("callback_query"):
                    try:
                        await _handle_callback(cfg, u["callback_query"])
                    except Exception:  # noqa: BLE001
                        log.exception("callback telegram")
        except Exception as e:  # noqa: BLE001 — 409: bot có webhook / bị app khác poll
            log.warning("telegram getUpdates: %s", e)
            await asyncio.sleep(300 if "409" in str(e) else 30)
