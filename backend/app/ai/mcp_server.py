"""MCP server (Streamable HTTP, stateless) tại /mcp — cho Claude Code / Claude Desktop kết nối.

Xác thực bằng API token của panel: `Authorization: Bearer th_...`.
Công cụ giống trợ lý web; quyền theo user sở hữu token.
"""

import contextvars
import json

import mcp.types as types
from sqlalchemy import select
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from ..core.audit import audit
from ..core.deps import user_from_token
from ..db import SessionLocal
from ..models import User
from ..core.security import hash_token
from ..models import ApiToken
from .subscription import EPHEMERAL
from .tools import TOOLS, ToolContext, available_tools, call_tool

current_user: contextvars.ContextVar[User | None] = contextvars.ContextVar("mcp_user", default=None)
current_mode: contextvars.ContextVar[dict] = contextvars.ContextVar("mcp_mode", default={})  # read_only, eph
server = Server("tekar-hosting", instructions="Công cụ quản lý hosting Tekar: app docker, proxy, backup, bảo mật, tài nguyên.")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    user = current_user.get()
    if user is None:
        return []
    return [
        types.Tool(
            name=t.name,
            description=("[THAY ĐỔI HỆ THỐNG] " if t.write else "") + t.description,
            inputSchema=t.schema,
            annotations=types.ToolAnnotations(readOnlyHint=not t.write, destructiveHint=t.write),
        )
        for t in available_tools(user, include_write=not current_mode.get().get("read_only"))
    ]


@server.call_tool()
async def handle_call(name: str, arguments: dict) -> list[types.TextContent]:
    user = current_user.get()
    if user is None:
        raise ValueError("unauthorized")
    mode = current_mode.get()
    t = TOOLS.get(name)
    if t is not None and t.write:
        if mode.get("read_only"):
            raise ValueError("Token chỉ đọc: không được dùng tool thay đổi hệ thống")
        eph = mode.get("eph")
        if eph is not None and eph.approve is not None:
            tid = await eph.match_tool_id(name)
            if not await eph.approve(tid, name, arguments or {}):
                raise ValueError("Người dùng không duyệt thao tác này.")
    source = "mcp-subscription" if mode.get("eph") else "mcp"
    ok = True
    try:
        out = await call_tool(name, arguments or {}, ToolContext(user=user, source=source))
    except Exception:
        ok = False
        raise
    finally:
        async with SessionLocal() as db:
            await audit(db, user, "mcp.call", name, {"source": source, "ok": ok, "write": bool(t and t.write)})
    return [types.TextContent(type="text", text=out)]


session_manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)


async def mcp_asgi(scope, receive, send):
    if scope["type"] != "http":
        return
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    mode: dict = {}
    user = None
    if token and token.startswith("thc_") and token in EPHEMERAL:
        eph = EPHEMERAL[token]
        user, mode = eph.user, {"eph": eph, "read_only": eph.read_only}
    elif token and token.startswith("th_"):
        async with SessionLocal() as db:
            user = await user_from_token(token, db)
            row = (await db.execute(select(ApiToken.read_only).where(ApiToken.token_hash == hash_token(token)))).scalar()
            mode = {"read_only": bool(row)}
    if user is None:
        body = json.dumps({"error": "unauthorized"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
        await send({"type": "http.response.body", "body": body})
        return
    tok = current_user.set(user)
    mtok = current_mode.set(mode)
    try:
        await session_manager.handle_request(scope, receive, send)
    finally:
        current_user.reset(tok)
        current_mode.reset(mtok)
