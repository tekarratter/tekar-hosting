"""WebSocket: terminal tương tác (docker exec TTY) + stream logs."""

import asyncio
import json
import secrets
import socket
import threading

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..config import settings
from ..core.audit import audit
from ..core.deps import ws_user
from ..db import SessionLocal
from ..services import docker_svc
from .containers import authorize_container

router = APIRouter(tags=["terminal"])


# Lệnh dựng sẵn cho container Claude Code (đăng nhập gói Claude)
CLAUDE_RUN = {"login": ["claude", "auth", "login", "--claudeai"], "setup-token": ["claude", "setup-token"], "claude": ["claude"]}


@router.websocket("/api/ws/terminal/{name}")
async def terminal(ws: WebSocket, name: str, shell: str = "auto", user_name: str | None = None, run: str | None = None):
    await ws.accept()
    async with SessionLocal() as db:
        user = await ws_user(ws, db)
        if user is None:
            await ws.close(4401, "unauthorized")
            return
        try:
            c = await authorize_container(user, name, db, "deployer")
        except HTTPException as e:
            await ws.send_text(f"\r\n\x1b[31m{e.detail}\x1b[0m\r\n")
            await ws.close(4403)
            return
        await audit(db, user, "terminal.open", c.name, {"shell": shell})

    if c.status != "running":
        await ws.send_text("\r\n\x1b[33mContainer không chạy.\x1b[0m\r\n")
        await ws.close()
        return

    if c.name == settings.claude_container and not user_name:
        user_name = "claude"
    api = docker_svc.client().api
    cmd = ["/bin/sh", "-c", "if command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh; fi"] if shell == "auto" else [shell]
    if run and c.name == settings.claude_container and run in CLAUDE_RUN:
        cmd = CLAUDE_RUN[run]
    # Docker không gửi SIGHUP khi đóng kết nối → ghi PID để dọn tiến trình khi đóng terminal
    pidfile = f"/tmp/.th-term-{secrets.token_hex(6)}"
    if shell == "auto" or run:
        cmd = ["/bin/sh", "-c", f'echo $$ > {pidfile}; exec "$@"', "sh", *cmd]
    else:
        pidfile = None
    exec_id = await docker_svc.run(
        api.exec_create, c.id, cmd, stdin=True, tty=True, user=user_name or "",
        environment={"TERM": "xterm-256color", "COLUMNS": "120", "LINES": "32"},
    )
    exec_id = exec_id["Id"]
    sock_io = await docker_svc.run(api.exec_start, exec_id, tty=True, socket=True)
    raw: socket.socket = sock_io._sock  # type: ignore[attr-defined]
    raw.settimeout(None)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def reader():
        try:
            while True:
                data = raw.recv(65536)
                if not data:
                    break
                loop.call_soon_threadsafe(queue.put_nowait, data)
        except OSError:
            pass
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, daemon=True).start()

    async def pump_out():
        while True:
            data = await queue.get()
            if data is None:
                await ws.send_text("\r\n\x1b[90m[phiên đã kết thúc]\x1b[0m\r\n")
                await ws.close()
                return
            await ws.send_bytes(data)

    out_task = asyncio.create_task(pump_out())
    try:
        while True:
            msg = await ws.receive_text()
            try:
                m = json.loads(msg)
            except ValueError:
                continue
            if m.get("type") == "input":
                await asyncio.to_thread(raw.sendall, m["data"].encode())
            elif m.get("type") == "resize":
                try:
                    await docker_svc.run(api.exec_resize, exec_id, height=int(m["rows"]), width=int(m["cols"]))
                except Exception:  # noqa: BLE001
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        out_task.cancel()
        try:
            raw.shutdown(socket.SHUT_RDWR)
            raw.close()
        except OSError:
            pass
        if pidfile:
            kill = f"p=$(cat {pidfile} 2>/dev/null) && [ -n \"$p\" ] && kill -HUP $p 2>/dev/null; sleep 1; [ -n \"$p\" ] && kill -KILL $p 2>/dev/null; rm -f {pidfile}"
            try:
                await docker_svc.run(c.exec_run, ["/bin/sh", "-c", kill], user=user_name or "", detach=True)
            except Exception:  # noqa: BLE001
                pass


@router.websocket("/api/ws/logs/{name}")
async def logs_stream(ws: WebSocket, name: str, tail: int = 200):
    await ws.accept()
    async with SessionLocal() as db:
        user = await ws_user(ws, db)
        if user is None:
            await ws.close(4401)
            return
        try:
            c = await authorize_container(user, name, db)
        except HTTPException as e:
            await ws.send_text(e.detail)
            await ws.close(4403)
            return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=5000)
    stop = threading.Event()

    def reader():
        try:
            for chunk in c.logs(stream=True, follow=True, tail=tail, timestamps=True):
                if stop.is_set():
                    break
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception:  # noqa: BLE001
            pass
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, daemon=True).start()

    async def watch_close():
        try:
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            stop.set()
            queue.put_nowait(None)

    closer = asyncio.create_task(watch_close())
    try:
        while True:
            chunk = await queue.get()
            if chunk is None or stop.is_set():
                break
            await ws.send_text(chunk.decode("utf-8", "replace"))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        stop.set()
        closer.cancel()
