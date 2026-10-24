"""Trợ lý vận hành dùng Claude API (Anthropic SDK) với vòng lặp tool thủ công + streaming.

Vòng lặp thủ công (không dùng tool runner) vì cần dừng giữa chừng để người dùng
duyệt các tool thay đổi hệ thống trước khi chạy.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import anthropic

from ..core.store import get_setting
from ..models import User
from .tools import ToolContext, available_tools, call_tool, TOOLS

log = logging.getLogger("th.claude")
Emit = Callable[[dict[str, Any]], Awaitable[None]]
Approve = Callable[[str, str, dict[str, Any]], Awaitable[bool]]

SYSTEM_PROMPT = """Bạn là trợ lý vận hành của Tekar Hosting — panel quản lý hosting dựa trên Docker trên một server Linux.

Hệ thống gồm:
- Các ứng dụng (app) là dự án docker compose: "managed" (panel lưu compose) hoặc "adopted" (dự án có sẵn trên host).
- Dịch vụ nền tảng dùng chung: Postgres, Redis, S3 (MinIO) — mỗi app được cấp database/user/bucket riêng.
- Reverse proxy Caddy (route domain → container:port), traffic public đi qua Cloudflare Tunnel; Nginx Proxy Manager cũ vẫn chạy song song trên cổng 80/443.
- Backup theo lịch, quét bảo mật (Trivy + kiểm tra cấu hình), theo dõi CPU/RAM/disk.

Cách làm việc:
- Trả lời bằng tiếng Việt, ngắn gọn, đi thẳng vào kết luận. Dùng Markdown (bảng cho số liệu khi hợp lý).
- Luôn dùng tool để lấy dữ liệu thật trước khi kết luận; không đoán trạng thái hệ thống.
- Khi chẩn đoán sự cố: xem trạng thái container → log (lọc lỗi) → cấu hình/tài nguyên → đề xuất nguyên nhân và cách sửa cụ thể.
- Tool thay đổi hệ thống (deploy, restart, exec, đổi giới hạn, sửa route…) cần người dùng duyệt. Chỉ gọi khi cần cho yêu cầu của người dùng, và nói rõ bạn sắp làm gì và vì sao.
- Không bao giờ in ra mật khẩu, token hay secret; nếu thấy trong log/output thì che đi.
- Nếu người dùng không có quyền với một tài nguyên, nói rõ thay vì cố vòng qua."""

REPORT_PROMPT = """Lập báo cáo tình trạng hệ thống hosting hôm nay. Dùng tool để thu thập dữ liệu thật, rồi viết báo cáo Markdown gồm:
1. Tóm tắt (3-5 gạch đầu dòng: điều quan trọng nhất cần biết).
2. Tài nguyên host (CPU/RAM/disk) và các container tiêu thụ nhiều nhất; app nào sắp chạm giới hạn.
3. Ứng dụng: app nào lỗi/không chạy đủ container, container restart nhiều.
4. Proxy & domain: route lỗi health.
5. Backup: app chưa có lịch backup, backup thất bại gần đây.
6. Bảo mật: vấn đề HIGH/CRITICAL nổi bật và image cần cập nhật.
7. Khuyến nghị hành động, xếp theo mức ưu tiên.
Chỉ đọc dữ liệu, không thực hiện thay đổi nào."""


async def _client() -> anthropic.AsyncAnthropic:
    key = await get_setting("anthropic_api_key_secret")
    if key:
        return anthropic.AsyncAnthropic(api_key=key, max_retries=3)
    # không cấu hình trong panel → để SDK tự tìm ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN trong môi trường
    return anthropic.AsyncAnthropic(max_retries=3)


async def api_configured() -> bool:
    import os

    return bool(await get_setting("anthropic_api_key_secret") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


async def is_configured() -> bool:
    from . import subscription

    return await api_configured() or await subscription.is_logged_in()


def _tool_defs(user: User, read_only: bool) -> list[dict[str, Any]]:
    defs = []
    for t in available_tools(user, include_write=not read_only):
        d = t.api_def()
        d["eager_input_streaming"] = True
        defs.append(d)
    return defs


async def run_turn(
    messages: list[dict[str, Any]],
    user: User,
    emit: Emit,
    approve: Approve | None,
    source: str = "chat",
    read_only: bool = False,
    system: str = SYSTEM_PROMPT,
    max_steps: int = 25,
) -> list[dict[str, Any]]:
    """Chạy cho tới khi Claude trả lời xong. `messages` được nối thêm tại chỗ và trả về."""
    client = await _client()
    model = await get_setting("claude_model") or "claude-opus-5-5"
    effort = await get_setting("claude_effort") or "medium"
    tools = _tool_defs(user, read_only)
    ctx = ToolContext(user=user, source=source)
    json_retries = 0

    for _ in range(max_steps):
        try:
            async with client.beta.messages.stream(
                model=model,
                max_tokens=64000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        d = event.delta
                        if d.type == "text_delta":
                            await emit({"type": "text", "delta": d.text})
                        elif d.type == "thinking_delta":
                            await emit({"type": "thinking", "delta": d.thinking})
                    elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                        await emit({"type": "tool_start", "id": event.content_block.id, "name": event.content_block.name})
                response = await stream.get_final_message()
            json_retries = 0
        except ValueError:
            # JSON tool input không parse được → gửi lại lượt này (giới hạn)
            json_retries += 1
            if json_retries > 2:
                raise
            continue

        content = [b.model_dump(mode="json", exclude_none=True) for b in response.content]
        messages.append({"role": "assistant", "content": content})
        await emit({"type": "usage", "usage": response.usage.model_dump(mode="json", exclude_none=True), "model": getattr(response, "model", model)})

        if response.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "refusal":
            await emit({"type": "text", "delta": "\n\n_(Yêu cầu bị từ chối bởi bộ lọc an toàn của mô hình.)_"})
            break
        if not tool_uses:
            break
        if response.stop_reason == "max_tokens":
            await emit({"type": "error", "message": "Phản hồi bị cắt do vượt max_tokens"})
            break

        results = []
        for tu in tool_uses:
            t = TOOLS.get(tu.name)
            args = tu.input if isinstance(tu.input, dict) else {}
            await emit({"type": "tool_use", "id": tu.id, "name": tu.name, "input": args, "write": bool(t and t.write)})
            if t is None:
                results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True, "content": f"Tool không tồn tại: {tu.name}"})
                continue
            if t.write:
                ok = await approve(tu.id, tu.name, args) if approve else False
                if not ok:
                    msg = "Người dùng không duyệt thao tác này." if approve else "Chế độ chỉ đọc: không được thay đổi hệ thống."
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True, "content": msg})
                    await emit({"type": "tool_result", "id": tu.id, "is_error": True, "content": msg})
                    continue
            try:
                out = await call_tool(tu.name, args, ctx)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
                await emit({"type": "tool_result", "id": tu.id, "is_error": False, "content": out[:4000]})
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                if "ValidationError" in err:
                    err = json.dumps({"INVALID_INPUT": err[:500]})
                results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True, "content": err})
                await emit({"type": "tool_result", "id": tu.id, "is_error": True, "content": err})
        messages.append({"role": "user", "content": results})
    return messages


def extract_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m["role"] == "assistant":
            parts = [b.get("text", "") for b in m["content"] if b.get("type") == "text"]
            if parts:
                return "\n".join(parts)
    return ""


async def generate_report(user: User) -> str:
    from . import subscription

    async def noop(_e: dict[str, Any]) -> None:
        return None

    if await subscription.engine() == "subscription":
        # worker không chung bộ nhớ với th-api (nơi phục vụ /mcp) → dùng API token chỉ-đọc tạm trong DB
        from ..core.security import new_api_token
        from ..db import SessionLocal
        from ..models import ApiToken

        raw, prefix, h = new_api_token()
        async with SessionLocal() as s:
            row = ApiToken(user_id=user.id, name="claude-report (tạm)", prefix=prefix, token_hash=h, read_only=True)
            s.add(row)
            await s.commit()
            tid = row.id
        try:
            msgs, _sid = await subscription.run_turn([], REPORT_PROMPT, user, noop, None, read_only=True, token=raw)
        finally:
            async with SessionLocal() as s:
                row = await s.get(ApiToken, tid)
                if row:
                    await s.delete(row)
                    await s.commit()
        return extract_text(msgs)

    msgs = await run_turn([{"role": "user", "content": REPORT_PROMPT}], user, noop, None, source="report", read_only=True)
    return extract_text(msgs)
