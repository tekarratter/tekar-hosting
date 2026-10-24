"""Chạy trợ lý bằng gói Claude (Pro/Max/Team) qua Claude Code CLI trong container th-claude.

Claude Code chạy headless (`claude -p --output-format stream-json`), không có tool có sẵn (Bash/Edit…),
chỉ dùng MCP server của panel → cùng bộ tool, cùng phân quyền như trợ lý dùng API key.
Token MCP cho mỗi lượt là token tạm trong bộ nhớ (thc_…); tool thay đổi hệ thống vẫn chờ người dùng duyệt
ngay trong MCP server (xem mcp_server.py).
"""

import asyncio
import json
import logging
import os
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings
from ..core.store import get_setting
from ..models import User
from ..services import docker_svc
from .tools import TOOLS

log = logging.getLogger("th.claude.sub")
MCP_NAME = "tekar-hosting"
PREFIX = f"mcp__{MCP_NAME}__"
MCP_URL = os.environ.get("TH_MCP_INTERNAL_URL", "http://th-api:8000/mcp")
HOME = Path("/claude-home")  # volume claude-home dùng chung với /home/claude của th-claude


# ======================================================================= token MCP tạm cho từng lượt chat
@dataclass
class Ephemeral:
    user: User
    approve: Any = None  # async (tool_id, name, args) -> bool ; None = chỉ đọc
    read_only: bool = False
    tool_ids: dict[str, list[str]] = field(default_factory=dict)

    async def match_tool_id(self, name: str) -> str:
        """Lấy tool_use id tương ứng lời gọi MCP (để UI hiện nút duyệt đúng khối tool)."""
        for _ in range(30):
            ids = self.tool_ids.get(name)
            if ids:
                return ids.pop(0)
            await asyncio.sleep(0.1)
        return f"mcp_{secrets.token_hex(6)}"


EPHEMERAL: dict[str, Ephemeral] = {}


# ======================================================================= trạng thái đăng nhập
def credentials_file() -> bool:
    try:
        return (HOME / ".claude" / ".credentials.json").stat().st_size > 0
    except OSError:
        return False


async def is_logged_in() -> bool:
    return credentials_file() or bool(await get_setting("claude_oauth_token_secret"))


async def env() -> dict[str, str]:
    e = {"DISABLE_AUTOUPDATER": "1", "MCP_TOOL_TIMEOUT": "900000", "MCP_TIMEOUT": "30000", "HOME": "/home/claude"}
    tok = await get_setting("claude_oauth_token_secret")
    if tok:
        e["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    return e


async def exec_capture(cmd: list[str], timeout: float = 120) -> tuple[int, str, str]:
    c = await docker_svc.get_container(settings.claude_container)
    if c is None or c.status != "running":
        raise RuntimeError(f"Container {settings.claude_container} không chạy (docker compose --profile claude up -d th-claude)")
    environment = await env()

    def _run():
        api = docker_svc.client().api
        ex = api.exec_create(c.id, cmd, user="claude", workdir="/home/claude", environment=environment)
        out, err = api.exec_start(ex["Id"], demux=True)
        return api.exec_inspect(ex["Id"]).get("ExitCode") or 0, (out or b"").decode(errors="replace"), (err or b"").decode(errors="replace")

    return await asyncio.wait_for(asyncio.to_thread(_run), timeout)


async def auth_status() -> dict[str, Any]:
    try:
        _code, out, err = await exec_capture(["claude", "auth", "status", "--json"], 60)
        data = json.loads(out or "{}")
    except Exception as e:  # noqa: BLE001
        return {"loggedIn": False, "error": f"{type(e).__name__}: {e}"}
    return data


async def cli_version() -> str | None:
    try:
        _c, out, _e = await exec_capture(["claude", "--version"], 30)
        return out.strip() or None
    except Exception:  # noqa: BLE001
        return None


async def engine() -> str:
    """'api' | 'subscription' — theo cài đặt claude_engine (auto: ưu tiên API key, không có thì dùng gói Claude)."""
    from .assistant import api_configured

    mode = await get_setting("claude_engine") or "auto"
    if mode in ("api", "subscription"):
        return mode
    if await api_configured():
        return "api"
    return "subscription" if await is_logged_in() else "api"


# ======================================================================= chạy lệnh streaming
async def _stream(cmd: list[str], environment: dict[str, str]) -> AsyncIterator[str]:
    c = await docker_svc.get_container(settings.claude_container)
    if c is None or c.status != "running":
        raise RuntimeError(f"Container {settings.claude_container} không chạy (docker compose --profile claude up -d th-claude)")
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    api = docker_svc.client().api
    ex = await docker_svc.run(api.exec_create, c.id, cmd, user="claude", workdir="/home/claude", environment=environment)

    def _pump():
        try:
            for out, err in api.exec_start(ex["Id"], stream=True, demux=True):
                if out:
                    loop.call_soon_threadsafe(q.put_nowait, ("out", out))
                if err:
                    loop.call_soon_threadsafe(q.put_nowait, ("err", err))
        except Exception as e:  # noqa: BLE001
            loop.call_soon_threadsafe(q.put_nowait, ("err", str(e).encode()))
        loop.call_soon_threadsafe(q.put_nowait, ("end", None))

    task = asyncio.create_task(asyncio.to_thread(_pump))
    buf, errbuf = b"", b""
    while True:
        kind, data = await q.get()
        if kind == "end":
            break
        if kind == "err":
            errbuf = (errbuf + data)[-8000:]
            continue
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                yield line.decode(errors="replace")
    if buf.strip():
        yield buf.decode(errors="replace")
    await task
    code = (await docker_svc.run(api.exec_inspect, ex["Id"])).get("ExitCode")
    if code:
        yield json.dumps({"type": "_exit", "code": code, "stderr": errbuf.decode(errors="replace")[-2000:]})


def _strip(name: str) -> str:
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def _tool_result_text(c: Any) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return json.dumps(c, ensure_ascii=False)


def _history_prompt(history: list[dict[str, Any]], text: str) -> str:
    """Phiên cũ chưa có session Claude Code (vd tạo bằng API key) → kèm tóm tắt lịch sử dạng văn bản."""
    lines = []
    for m in history:
        if m["role"] == "user" and isinstance(m["content"], str):
            lines.append(f"Người dùng: {m['content']}")
        elif m["role"] == "assistant":
            t = "\n".join(b.get("text", "") for b in m["content"] if b.get("type") == "text").strip()
            if t:
                lines.append(f"Trợ lý: {t}")
    if not lines:
        return text
    return "Lịch sử hội thoại trước đó (để tham khảo):\n\n" + "\n\n".join(lines[-20:]) + f"\n\n---\nYêu cầu mới: {text}"


async def run_turn(
    history: list[dict[str, Any]],
    text: str,
    user: User,
    emit,
    approve,
    session_id: str | None = None,
    read_only: bool = False,
    system: str | None = None,
    token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Chạy một lượt. Trả về (messages dạng API để nối vào lịch sử, session_id của Claude Code).
    `token`: token MCP có sẵn (worker dùng token DB tạm); không có → tạo token tạm trong bộ nhớ."""
    from .assistant import SYSTEM_PROMPT

    own_token = token is None
    if own_token:
        token = "thc_" + secrets.token_urlsafe(32)
        eph = EPHEMERAL[token] = Ephemeral(user=user, approve=approve, read_only=read_only or approve is None)
    else:
        eph = None
    mcp = {"mcpServers": {MCP_NAME: {"type": "http", "url": MCP_URL, "headers": {"Authorization": f"Bearer {token}"}}}}
    cmd = ["claude", "-p", text if session_id else _history_prompt(history, text),
           "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--mcp-config", json.dumps(mcp), "--strict-mcp-config", "--tools", "",
           "--allowedTools", f"mcp__{MCP_NAME}", "--system-prompt", system or SYSTEM_PROMPT]
    model = await get_setting("claude_sub_model")
    if model:
        cmd += ["--model", model]
    effort = await get_setting("claude_effort")
    if effort:
        cmd += ["--effort", effort]
    if session_id:
        cmd += ["--resume", session_id]

    out: list[dict[str, Any]] = []
    new_sid = session_id
    cur: dict[str, Any] | None = None  # assistant message đang gom (Claude Code có thể tách mỗi block 1 dòng)
    final_error = None
    t0 = time.time()
    try:
        async for line in _stream(cmd, await env()):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            typ = ev.get("type")
            if ev.get("session_id"):
                new_sid = ev["session_id"]
            if typ == "system" and ev.get("subtype") == "init":
                bad = [s for s in ev.get("mcp_servers") or [] if s.get("status") not in ("connected", None)]
                if bad:
                    await emit({"type": "error", "message": f"MCP server panel không kết nối được: {bad}"})
            elif typ == "stream_event":
                e = ev.get("event") or {}
                if e.get("type") == "content_block_delta":
                    d = e.get("delta") or {}
                    if d.get("type") == "text_delta":
                        await emit({"type": "text", "delta": d.get("text", "")})
                    elif d.get("type") == "thinking_delta":
                        await emit({"type": "thinking", "delta": d.get("thinking", "")})
                elif e.get("type") == "content_block_start" and (e.get("content_block") or {}).get("type") == "tool_use":
                    b = e["content_block"]
                    name = _strip(b.get("name", ""))
                    if eph is not None:
                        eph.tool_ids.setdefault(name, []).append(b["id"])
                    await emit({"type": "tool_start", "id": b["id"], "name": name})
            elif typ == "assistant":
                msg = ev.get("message") or {}
                blocks = []
                for b in msg.get("content") or []:
                    if b.get("type") == "text":
                        blocks.append({"type": "text", "text": b.get("text", "")})
                    elif b.get("type") == "tool_use":
                        name = _strip(b.get("name", ""))
                        blocks.append({"type": "tool_use", "id": b["id"], "name": name, "input": b.get("input") or {}})
                        t = TOOLS.get(name)
                        await emit({"type": "tool_use", "id": b["id"], "name": name, "input": b.get("input") or {}, "write": bool(t and t.write)})
                if cur is not None and cur.get("_id") == msg.get("id"):
                    cur["content"].extend(blocks)
                else:
                    cur = {"role": "assistant", "content": blocks, "_id": msg.get("id")}
                    out.append(cur)
            elif typ == "user":
                msg = ev.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                results = []
                for b in content:
                    if b.get("type") == "tool_result":
                        txt = _tool_result_text(b.get("content"))
                        results.append({"type": "tool_result", "tool_use_id": b["tool_use_id"], "content": txt, "is_error": bool(b.get("is_error"))})
                        await emit({"type": "tool_result", "id": b["tool_use_id"], "is_error": bool(b.get("is_error")), "content": txt[:4000]})
                if results:
                    out.append({"role": "user", "content": results})
                    cur = None
            elif typ == "result":
                await emit({"type": "usage", "engine": "subscription", "model": model or "mặc định",
                            "usage": ev.get("usage") or {}, "cost_usd": ev.get("total_cost_usd"), "duration_ms": ev.get("duration_ms")})
                if ev.get("is_error"):
                    final_error = ev.get("result") or ev.get("subtype") or "lỗi không rõ"
            elif typ == "_exit" and not final_error:
                final_error = (ev.get("stderr") or "").strip() or f"claude thoát với mã {ev.get('code')}"
    finally:
        if own_token:
            EPHEMERAL.pop(token, None)
    for m in out:
        m.pop("_id", None)
    out = [m for m in out if m["content"]]
    if final_error:
        hint = ""
        low = final_error.lower()
        if "login" in low or "auth" in low or "401" in low or "credential" in low:
            hint = " — kiểm tra đăng nhập ở Claude → Gói Claude"
        raise RuntimeError(f"Claude Code: {final_error[:800]}{hint}")
    log.info("subscription turn %.1fs, %d messages", time.time() - t0, len(out))
    return out, new_sid
