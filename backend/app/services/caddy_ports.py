"""Quản lý cổng publish của th-caddy (HTTP/HTTPS + stream TCP/UDP) và chuyển đổi từ Nginx Proxy Manager.

Cổng được ghi vào docker-compose.override.yml cạnh docker-compose.yml của panel (compose tự nạp file này,
nên `docker compose up -d` chạy tay vẫn giữ đúng cổng), rồi tạo lại container th-caddy.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import settings
from ..core.jobs import JobContext, job_handler
from ..core.store import get_setting, set_setting
from ..db import SessionLocal
from ..models import Stream
from . import docker_svc
from .compose import run_cmd

OVERRIDE_NAME = "docker-compose.override.yml"
NPM_IMAGE_HINT = "nginx-proxy-manager"


async def project_dir() -> Path:
    c = await docker_svc.get_container(settings.caddy_container)
    wd = (c.labels or {}).get(docker_svc.COMPOSE_WORKDIR) if c else None
    wd = await docker_svc.translate_path(wd or "") if wd else None
    if not wd or not os.path.isfile(os.path.join(wd, "docker-compose.yml")):
        raise RuntimeError("Không tìm thấy thư mục dự án của panel (docker-compose.yml)")
    return Path(wd)


async def current_ports(db) -> tuple[int, int]:
    http = await get_setting("caddy_http_port", db) or settings.caddy_http_port
    https = await get_setting("caddy_https_port", db) or settings.caddy_https_port
    return int(http), int(https)


async def desired_ports(db) -> list[str]:
    http, https = await current_ports(db)
    ports = [f"{http}:80/tcp", f"{https}:443/tcp", f"{https}:443/udp"]
    for s in (await db.execute(select(Stream).where(Stream.enabled.is_(True)))).scalars():
        for p in (["tcp", "udp"] if s.protocol == "both" else [s.protocol]):
            ports.append(f"{s.listen_port}:{s.listen_port}/{p}")
    return ports


def _host_listening() -> dict[tuple[int, str], bool]:
    """Cổng đang LISTEN trong network namespace của host (đọc /proc/1/net/*)."""
    out: dict[tuple[int, str], bool] = {}
    for proto, files in (("tcp", ("tcp", "tcp6")), ("udp", ("udp", "udp6"))):
        for f in files:
            try:
                lines = Path(settings.host_proc, "1", "net", f).read_text().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                parts = line.split()
                port = int(parts[1].rsplit(":", 1)[1], 16)
                state = parts[3]
                if proto == "udp" or state == "0A":  # 0A = LISTEN
                    out[(port, proto)] = True
    return out


async def port_conflicts(ports: list[str]) -> list[str]:
    """Cổng host đã bị container khác (không phải th-caddy) hoặc tiến trình host chiếm."""
    owners: dict[tuple[int, str], str] = {}
    for c in await docker_svc.list_containers():
        if c["name"] == settings.caddy_container:
            continue
        for p in c["ports"]:
            if p["host_port"]:
                proto = p["container"].split("/")[1] if "/" in p["container"] else "tcp"
                owners[(int(p["host_port"]), proto)] = c["name"]
    caddy = await docker_svc.get_container(settings.caddy_container)
    caddy_ports = set()
    if caddy:
        for cport, binds in (caddy.attrs["NetworkSettings"].get("Ports") or {}).items():
            for b in binds or []:
                caddy_ports.add((int(b["HostPort"]), cport.split("/")[1]))
    listening = await asyncio.to_thread(_host_listening)
    issues = []
    for spec in ports:
        host_port, rest = spec.split(":", 1)
        key = (int(host_port), rest.split("/")[1])
        if key in owners:
            issues.append(f"{key[0]}/{key[1]} đang dùng bởi container {owners[key]}")
        elif key in listening and key not in caddy_ports:
            issues.append(f"{key[0]}/{key[1]} đang được một tiến trình trên host sử dụng")
    return issues


async def write_override(db) -> Path:
    ports = await desired_ports(db)
    d = await project_dir()
    lines = [
        "# Sinh tự động bởi Tekar Hosting (Proxy → Cổng & chuyển đổi). Không sửa tay.",
        "services:",
        "  th-caddy:",
        "    ports: !override",
        *[f'      - "{p}"' for p in ports],
        "",
    ]
    path = d / OVERRIDE_NAME
    path.write_text("\n".join(lines))
    return path


async def recreate_caddy(ctx: JobContext | None) -> None:
    d = await project_dir()
    await run_cmd(["docker", "compose", "--project-directory", str(d), "-p", settings.compose_project,
                   "up", "-d", "--no-deps", "--no-build", settings.caddy_container], ctx)
    # chờ admin socket sẵn sàng rồi đẩy lại cấu hình (--resume đã nạp bản autosave, đẩy lại cho chắc)
    from .proxy import apply, caddy_status

    for _ in range(30):
        if (await caddy_status()).get("online"):
            break
        await asyncio.sleep(1)
    async with SessionLocal() as s:
        await apply(s)


async def npm_containers() -> list[Any]:
    def _list():
        return [c for c in docker_svc.client().containers.list(all=True)
                if NPM_IMAGE_HINT in (c.attrs["Config"].get("Image") or "")]

    return await docker_svc.run(_list)


@job_handler("caddy_ports")
async def job_caddy_ports(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as s:
        ports = await desired_ports(s)
        issues = await port_conflicts(ports)
        if issues and not params.get("force"):
            raise RuntimeError("Xung đột cổng: " + "; ".join(issues))
        path = await write_override(s)
    await ctx.log(f"Ghi {path}: {', '.join(ports)}")
    await recreate_caddy(ctx)
    return {"ports": ports}


@job_handler("proxy_cutover")
async def job_cutover(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    """Chuyển cổng 80/443 từ NPM sang Caddy. Lỗi giữa chừng → tự hoàn tác."""
    http, https = int(params.get("http_port") or 80), int(params.get("https_port") or 443)
    npms = [c for c in await npm_containers() if c.status == "running"]
    stopped: list[str] = []
    async with SessionLocal() as s:
        old = await current_ports(s)
        old_public = bool(await get_setting("caddy_public", s))
    try:
        for c in npms:
            await ctx.log(f"Dừng Nginx Proxy Manager: {c.name}")
            await docker_svc.run(c.stop, timeout=20)
            stopped.append(c.name)
        async with SessionLocal() as s:
            await set_setting("caddy_http_port", http, s)
            await set_setting("caddy_https_port", https, s)
            await set_setting("caddy_public", True, s)
            await set_setting("npm_stopped_containers", stopped, s)
            await s.commit()
            issues = await port_conflicts(await desired_ports(s))
            if issues:
                raise RuntimeError("Xung đột cổng: " + "; ".join(issues))
            path = await write_override(s)
        await ctx.log(f"Ghi {path}")
        await recreate_caddy(ctx)
        await ctx.log(f"Caddy đã nhận cổng {http}/{https}. Cert Let's Encrypt (HTTP-01) sẽ được cấp trong ít phút.")
        return {"stopped": stopped, "ports": [http, https]}
    except Exception as e:
        await ctx.log(f"LỖI: {e} — hoàn tác")
        async with SessionLocal() as s:
            await set_setting("caddy_http_port", old[0], s)
            await set_setting("caddy_https_port", old[1], s)
            await set_setting("caddy_public", old_public, s)
            await s.commit()
            try:
                await write_override(s)
                await recreate_caddy(ctx)
            except Exception as e2:  # noqa: BLE001
                await ctx.log(f"⚠ hoàn tác Caddy lỗi: {e2}")
        for name in stopped:
            c = await docker_svc.get_container(name)
            if c:
                await docker_svc.run(c.start)
                await ctx.log(f"Khởi động lại {name}")
        raise


@job_handler("proxy_rollback")
async def job_rollback(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    """Trả 80/443 lại cho NPM: Caddy về cổng phụ, bật lại các container NPM đã dừng."""
    async with SessionLocal() as s:
        names = await get_setting("npm_stopped_containers", s) or []
        await set_setting("caddy_http_port", settings.caddy_http_port, s)
        await set_setting("caddy_https_port", settings.caddy_https_port, s)
        await set_setting("caddy_public", False, s)
        await s.commit()
        await write_override(s)
    await recreate_caddy(ctx)
    started = []
    for name in names or [c.name for c in await npm_containers()]:
        c = await docker_svc.get_container(name)
        if c and c.status != "running":
            await docker_svc.run(c.start)
            started.append(name)
            await ctx.log(f"Khởi động lại {name}")
    async with SessionLocal() as s:
        await set_setting("npm_stopped_containers", [], s)
        await s.commit()
    return {"started": started}
