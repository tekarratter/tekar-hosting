"""Claude: trợ lý chat (WebSocket), báo cáo, và Claude Code console (đăng nhập tài khoản Claude + MCP)."""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select

from ..ai import assistant, subscription
from ..config import settings
from ..core.audit import audit
from ..core.deps import DB, AdminUser, CurrentUser, StaffUser, ws_user
from ..core.security import new_api_token
from ..db import SessionLocal
from ..core.store import get_setting, set_setting
from ..models import ApiToken, AuditLog, ChatSession, Report
from ..services import docker_svc

router = APIRouter(prefix="/api/claude", tags=["claude"])


@router.get("/status")
async def status(user: CurrentUser):
    console = await docker_svc.get_container(settings.claude_container)
    return {
        "api_configured": await assistant.api_configured(),
        "subscription_logged_in": await subscription.is_logged_in(),
        "configured": await assistant.is_configured(),
        "engine": await subscription.engine(),
        "console": {"exists": console is not None, "state": console.status if console else None,
                    "logged_in": await subscription.is_logged_in(), "container": settings.claude_container},
    }


# ---------------------------------------------------------------- sessions
@router.get("/sessions")
async def sessions(user: CurrentUser, db: DB):
    rows = (await db.execute(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc()).limit(50))).scalars()
    return [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in rows]


def _display(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chuyển lịch sử API sang dạng hiển thị."""
    out: list[dict[str, Any]] = []
    results: dict[str, dict] = {}
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    c = b.get("content")
                    results[b["tool_use_id"]] = {"content": c if isinstance(c, str) else json.dumps(c)[:4000], "is_error": b.get("is_error", False)}
    for m in messages:
        if m["role"] == "user":
            if isinstance(m["content"], str):
                out.append({"role": "user", "text": m["content"]})
            continue
        blocks = []
        for b in m["content"]:
            if b.get("type") == "text":
                blocks.append({"type": "text", "text": b["text"]})
            elif b.get("type") == "thinking" and b.get("thinking"):
                blocks.append({"type": "thinking", "text": b["thinking"]})
            elif b.get("type") == "tool_use":
                blocks.append({"type": "tool", "id": b["id"], "name": b["name"], "input": b.get("input"), "result": results.get(b["id"])})
        if out and out[-1]["role"] == "assistant":
            out[-1]["blocks"].extend(blocks)
        else:
            out.append({"role": "assistant", "blocks": blocks})
    return out


@router.get("/sessions/{sid}")
async def session(sid: int, user: CurrentUser, db: DB):
    s = await db.get(ChatSession, sid)
    if s is None or s.user_id != user.id:
        raise HTTPException(404)
    return {"id": s.id, "title": s.title, "messages": _display(s.messages)}


@router.delete("/sessions/{sid}")
async def delete_session(sid: int, user: CurrentUser, db: DB):
    s = await db.get(ChatSession, sid)
    if s and s.user_id == user.id:
        await db.delete(s)
        await db.commit()
    return {"ok": True}


@router.websocket("/ws")
async def chat_ws(ws: WebSocket):
    await ws.accept()
    async with SessionLocal() as db:
        user = await ws_user(ws, db)
    if user is None:
        await ws.close(4401)
        return
    if not await assistant.is_configured():
        await ws.send_json({"type": "error", "message": "Chưa cấu hình Claude: thêm Anthropic API key (Cài đặt → Claude) hoặc đăng nhập gói Claude (Claude → Gói Claude)."})

    pending: dict[str, asyncio.Future] = {}
    inbox: asyncio.Queue[dict] = asyncio.Queue()

    async def reader():
        try:
            while True:
                m = await ws.receive_json()
                if m.get("type") == "approval" and m.get("id") in pending:
                    pending[m["id"]].set_result(bool(m.get("approved")))
                else:
                    await inbox.put(m)
        except (WebSocketDisconnect, RuntimeError, ValueError):
            for f in pending.values():
                if not f.done():
                    f.set_result(False)
            await inbox.put({"type": "_closed"})

    async def emit(e: dict[str, Any]) -> None:
        try:
            await ws.send_json(e)
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def approve(tool_id: str, name: str, args: dict[str, Any]) -> bool:
        fut = asyncio.get_running_loop().create_future()
        pending[tool_id] = fut
        await emit({"type": "approval_request", "id": tool_id, "name": name, "input": args})
        try:
            return await asyncio.wait_for(fut, timeout=600)
        except TimeoutError:
            return False
        finally:
            pending.pop(tool_id, None)

    rtask = asyncio.create_task(reader())
    try:
        while True:
            m = await inbox.get()
            if m.get("type") == "_closed":
                break
            if m.get("type") != "message" or not (m.get("text") or "").strip():
                continue
            async with SessionLocal() as db:
                s = await db.get(ChatSession, m["session_id"]) if m.get("session_id") else None
                if s is None or s.user_id != user.id:
                    s = ChatSession(user_id=user.id, title=m["text"].strip()[:80], messages=[])
                    db.add(s)
                    await db.commit()
                    await db.refresh(s)
                history = list(s.messages)
            eng = await subscription.engine()
            await emit({"type": "session", "id": s.id, "title": s.title, "engine": eng})
            text = m["text"].strip()
            engine_session = s.engine_session if s.engine == "subscription" else None
            if eng == "subscription":
                try:
                    new_msgs, engine_session = await subscription.run_turn(history, text, user, emit, approve, session_id=s.engine_session)
                    history = history + [{"role": "user", "content": text}] + new_msgs
                except Exception as e:  # noqa: BLE001
                    await emit({"type": "error", "message": str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"})
            else:
                history.append({"role": "user", "content": text})
                # lịch sử từ Claude Code có thể có block không hợp lệ với API (thinking không chữ ký…) → chỉ giữ text/tool
                for msg in history:
                    if msg["role"] == "assistant":
                        msg["content"] = [b for b in msg["content"] if b.get("type") in ("text", "tool_use") or b.get("signature")]
                try:
                    history = await assistant.run_turn(history, user, emit, approve)
                except Exception as e:  # noqa: BLE001
                    await emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
                    # bỏ lượt dở dang để lịch sử luôn hợp lệ
                    while history and history[-1]["role"] != "user":
                        history.pop()
                    if history and isinstance(history[-1]["content"], str):
                        history.pop()
            async with SessionLocal() as db:
                row = await db.get(ChatSession, s.id)
                row.messages = history
                row.engine = eng
                row.engine_session = engine_session
                await db.commit()
            await emit({"type": "done"})
    finally:
        rtask.cancel()


# ---------------------------------------------------------------- reports
@router.get("/reports")
async def reports(user: StaffUser, db: DB):
    rows = (await db.execute(select(Report).order_by(Report.id.desc()).limit(50))).scalars()
    return [{"id": r.id, "title": r.title, "created_at": r.created_at} for r in rows]


@router.get("/reports/{rid}")
async def report(rid: int, user: StaffUser, db: DB):
    r = await db.get(Report, rid)
    if r is None:
        raise HTTPException(404)
    return {"id": r.id, "title": r.title, "content": r.content, "created_at": r.created_at}


@router.post("/reports")
async def create_report(user: StaffUser, db: DB):
    from ..core.jobs import enqueue

    job = await enqueue(db, "claude_report", {"user_id": user.id}, None, user.id)
    return {"job_id": job.id}


# ---------------------------------------------------------------- Claude Code console
@router.post("/console/setup")
async def console_setup(user: StaffUser, db: DB):
    """Tạo API token cho MCP và ghi cấu hình MCP vào home của container console."""
    raw, prefix, h = new_api_token()
    db.add(ApiToken(user_id=user.id, name="claude-console-mcp", prefix=prefix, token_hash=h))
    await db.commit()
    from pathlib import Path

    home = Path("/claude-home")  # volume dùng chung với /home/claude của th-claude
    if not home.is_dir():
        raise HTTPException(400, "Chưa bật Claude console (docker compose --profile claude up -d)")
    mcp = {"mcpServers": {"tekar-hosting": {"type": "http", "url": "http://th-api:8000/mcp", "headers": {"Authorization": f"Bearer {raw}"}}}}
    p = home / ".mcp.json"
    p.write_text(json.dumps(mcp, indent=2))
    p.chmod(0o600)
    import os

    try:
        os.chown(p, 1000, 1000)
    except OSError:
        pass
    await audit(db, user, "claude.console_setup")
    return {"ok": True, "container": settings.claude_container,
            "hint": "Mở Terminal của container th-claude, chạy `claude` rồi làm theo hướng dẫn /login bằng tài khoản Claude."}


# ---------------------------------------------------------------- Gói Claude (subscription qua Claude Code)
@router.get("/subscription")
async def subscription_status(user: StaffUser):
    console = await docker_svc.get_container(settings.claude_container)
    running = bool(console and console.status == "running")
    return {
        "container": {"name": settings.claude_container, "exists": console is not None, "state": console.status if console else None},
        "auth": await subscription.auth_status() if running else None,
        "version": await subscription.cli_version() if running else None,
        "credentials_file": subscription.credentials_file(),
        "token_configured": bool(await get_setting("claude_oauth_token_secret")),
        "engine_setting": await get_setting("claude_engine") or "auto",
        "engine": await subscription.engine(),
        "api_configured": await assistant.api_configured(),
        "model": await get_setting("claude_sub_model") or "",
    }


class SubscriptionIn(BaseModel):
    engine: str | None = None  # auto | api | subscription
    model: str | None = None
    oauth_token: str | None = None  # None = giữ nguyên, "" = xoá


@router.put("/subscription")
async def subscription_update(body: SubscriptionIn, user: AdminUser, db: DB, request: Request):
    if body.engine is not None:
        if body.engine not in ("auto", "api", "subscription"):
            raise HTTPException(400, "engine phải là auto | api | subscription")
        await set_setting("claude_engine", body.engine, db)
    if body.model is not None:
        await set_setting("claude_sub_model", body.model.strip(), db)
    if body.oauth_token is not None:
        tok = body.oauth_token.strip()
        if tok and not tok.startswith("sk-ant-"):
            raise HTTPException(400, "Token không đúng dạng (bắt đầu bằng sk-ant-…, tạo bằng `claude setup-token`)")
        await set_setting("claude_oauth_token_secret", tok, db)
    await db.commit()
    await audit(db, user, "claude.subscription_update", "", {"engine": body.engine, "model": body.model,
                                                            "token": None if body.oauth_token is None else bool(body.oauth_token)}, request)
    return {"ok": True}


@router.post("/subscription/test")
async def subscription_test(user: StaffUser):
    cmd = ["claude", "-p", "Trả lời đúng một từ: OK", "--output-format", "json", "--tools", "", "--strict-mcp-config"]
    model = await get_setting("claude_sub_model")
    if model:
        cmd += ["--model", model]
    try:
        code, out, err = await subscription.exec_capture(cmd, 180)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "message": (err or out or f"exit {code}").strip()[-1500:]}
    models = list((data.get("modelUsage") or {}).keys())
    return {"ok": not data.get("is_error"), "message": (data.get("result") or "").strip()[:500],
            "duration_ms": data.get("duration_ms"), "models": models}


@router.post("/subscription/logout")
async def subscription_logout(user: AdminUser, db: DB, request: Request):
    try:
        await subscription.exec_capture(["claude", "auth", "logout"], 60)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e
    await audit(db, user, "claude.subscription_logout", request=request)
    return {"ok": True}


# ---------------------------------------------------------------- MCP
@router.get("/mcp")
async def mcp_info(user: CurrentUser, db: DB, request: Request):
    from ..ai.tools import available_tools

    tools = [{"name": t.name, "description": t.description, "write": t.write, "staff_only": t.staff_only} for t in available_tools(user)]
    tokens = (await db.execute(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id.desc()))).scalars().all()
    calls = (await db.execute(
        select(AuditLog).where(AuditLog.action == "mcp.call")
        .where(AuditLog.user_id == user.id).order_by(AuditLog.id.desc()).limit(30))).scalars().all()
    base = str(request.base_url).rstrip("/")
    fwd = request.headers.get("x-forwarded-proto")
    if fwd and base.startswith("http://") and fwd == "https":
        base = "https://" + base[7:]
    return {
        "url": f"{base}/mcp", "internal_url": subscription.MCP_URL,
        "tools": tools,
        "tokens": [{"id": t.id, "name": t.name, "prefix": t.prefix, "read_only": t.read_only, "created_at": t.created_at,
                    "last_used_at": t.last_used_at} for t in tokens],
        "calls": [{"id": a.id, "action": a.action, "target": a.target, "detail": a.detail, "created_at": a.created_at} for a in calls],
    }
