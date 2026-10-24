"""Thu thập CPU/RAM/IO của container (đọc thẳng cgroup v2 — nhanh hơn docker stats) và của host."""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil
from sqlalchemy import delete, select

from ..config import settings
from ..db import SessionLocal
from ..models import App, ContainerSample, HostSample
from . import docker_svc

psutil.PROCFS_PATH = settings.host_proc
_prev: dict[str, tuple[float, int]] = {}  # container id -> (t, usage_usec)
LATEST: dict[str, dict[str, Any]] = {}  # name -> sample mới nhất (cho API realtime khi chạy trong worker)


def _read_kv(path: Path) -> dict[str, int]:
    out = {}
    try:
        for line in path.read_text().splitlines():
            k, _, v = line.partition(" ")
            if v.strip().isdigit():
                out[k] = int(v)
    except OSError:
        pass
    return out


def _read_int(path: Path) -> int | None:
    try:
        v = path.read_text().strip()
        return None if v == "max" else int(v)
    except (OSError, ValueError):
        return None


def _cgroup_dir(cid: str) -> Path | None:
    root = Path(settings.host_cgroup)
    for cand in (root / "system.slice" / f"docker-{cid}.scope", root / "docker" / cid):
        if cand.exists():
            return cand
    return None


def _net(pid: int) -> tuple[int, int]:
    rx = tx = 0
    try:
        for line in Path(settings.host_proc, str(pid), "net", "dev").read_text().splitlines()[2:]:
            iface, _, rest = line.partition(":")
            if iface.strip() == "lo":
                continue
            f = rest.split()
            rx += int(f[0])
            tx += int(f[8])
    except (OSError, IndexError, ValueError):
        pass
    return rx, tx


def sample_containers() -> list[dict[str, Any]]:
    now = time.time()
    out = []
    for c in docker_svc.client().containers.list():
        cg = _cgroup_dir(c.id)
        if cg is None:
            continue
        usage = _read_kv(cg / "cpu.stat").get("usage_usec", 0)
        cpu_pct = 0.0
        if c.id in _prev:
            pt, pu = _prev[c.id]
            if now > pt:
                cpu_pct = max(0.0, (usage - pu) / ((now - pt) * 1e6) * 100)
        _prev[c.id] = (now, usage)
        mem_cur = _read_int(cg / "memory.current") or 0
        mstat = _read_kv(cg / "memory.stat")
        mem = max(0, mem_cur - mstat.get("inactive_file", 0))
        mem_limit = _read_int(cg / "memory.max") or 0
        rb = wb = 0
        try:
            for line in (cg / "io.stat").read_text().splitlines():
                for part in line.split()[1:]:
                    k, _, v = part.partition("=")
                    if k == "rbytes":
                        rb += int(v)
                    elif k == "wbytes":
                        wb += int(v)
        except OSError:
            pass
        rx, tx = _net(c.attrs["State"].get("Pid") or 0)
        labels = c.labels or {}
        out.append(
            {
                "name": c.name,
                "project": labels.get(docker_svc.COMPOSE_PROJECT),
                "cpu_pct": round(cpu_pct, 2),
                "mem_bytes": mem,
                "mem_limit": mem_limit,
                "net_rx": rx,
                "net_tx": tx,
                "blk_read": rb,
                "blk_write": wb,
            }
        )
    alive = {c["name"] for c in out}
    for k in list(LATEST):
        if k not in alive:
            LATEST.pop(k, None)
    for s in out:
        LATEST[s["name"]] = s
    return out


_prev_cpu: tuple[int, int] | None = None  # (busy, total) jiffies


def _host_cpu_pct() -> float:
    """Tự tính từ /proc/stat (psutil.cpu_percent lưu trạng thái theo thread, không hợp với to_thread)."""
    global _prev_cpu
    try:
        f = [int(x) for x in Path(settings.host_proc, "stat").read_text().splitlines()[0].split()[1:9]]
    except (OSError, ValueError):
        return 0.0
    idle = f[3] + f[4]
    total = sum(f)
    busy = total - idle
    pct = 0.0
    if _prev_cpu and total > _prev_cpu[1]:
        pct = (busy - _prev_cpu[0]) / (total - _prev_cpu[1]) * 100
    _prev_cpu = (busy, total)
    return round(max(0.0, min(100.0, pct)), 1)


def sample_host() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    du = os.statvfs(str(settings.apps_root))
    total = du.f_blocks * du.f_frsize
    free = du.f_bavail * du.f_frsize
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = 0.0
    return {
        "cpu_pct": _host_cpu_pct(),
        "cpu_count": psutil.cpu_count(),
        "load1": load1,
        "mem_used": vm.total - vm.available,
        "mem_total": vm.total,
        "disk_used": total - free,
        "disk_total": total,
        "uptime_s": int(time.time() - psutil.boot_time()),
    }


async def collect_once() -> dict[str, Any]:
    containers = await asyncio.to_thread(sample_containers)
    host = await asyncio.to_thread(sample_host)
    ts = datetime.now(UTC)
    async with SessionLocal() as s:
        s.add_all([ContainerSample(ts=ts, **c) for c in containers])
        s.add(HostSample(ts=ts, **{k: host[k] for k in ("cpu_pct", "load1", "mem_used", "mem_total", "disk_used", "disk_total")}))
        await s.commit()
    return {"host": host, "containers": containers}


async def prune() -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=settings.metrics_retention_hours)
    async with SessionLocal() as s:
        await s.execute(delete(ContainerSample).where(ContainerSample.ts < cutoff))
        await s.execute(delete(HostSample).where(HostSample.ts < cutoff))
        await s.commit()


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


async def disk_usage_by_project(projects: set[str]) -> dict[str, dict[str, Any]]:
    """Dung lượng volume + bind mount (nằm trong apps_root) theo project compose (chỉ các project được quản lý)."""

    def _calc():
        df = docker_svc.client().df()
        vol_size = {v["Name"]: (v.get("UsageData") or {}).get("Size", 0) for v in df.get("Volumes") or []}
        result: dict[str, dict[str, Any]] = {}
        seen_bind: set[str] = set()
        for c in docker_svc.client().containers.list(all=True):
            p = (c.labels or {}).get(docker_svc.COMPOSE_PROJECT)
            if p not in projects:
                continue
            info = result.setdefault(p, {"volumes": 0, "binds": 0, "images": 0})
            for m in c.attrs.get("Mounts", []):
                if m.get("Type") == "volume" and m.get("Name") in vol_size:
                    info["volumes"] += max(0, vol_size.pop(m["Name"]))
                elif m.get("Type") == "bind":
                    src = m.get("Source") or ""
                    if src.startswith(str(settings.apps_root)) and src not in seen_bind and os.path.isdir(src):
                        seen_bind.add(src)
                        info["binds"] += _dir_size(src)
        return result

    return await asyncio.to_thread(_calc)


async def update_app_disk_usage() -> None:
    from .notify import notify, resolve

    async with SessionLocal() as s:
        projects = set((await s.execute(select(App.compose_project))).scalars())
    if not projects:
        return
    usage = await disk_usage_by_project(projects)

    async with SessionLocal() as s:
        for app in (await s.execute(select(App))).scalars():
            u = usage.get(app.compose_project)
            if u is None:
                continue
            app.disk_usage_mb = round((u["volumes"] + u["binds"]) / 1024 / 1024, 1)
            if app.disk_quota_mb:
                key = f"quota:{app.slug}"
                if app.disk_usage_mb > app.disk_quota_mb:
                    await notify("warning", f"{app.name} vượt hạn mức dung lượng", f"{app.disk_usage_mb:.0f}MB / {app.disk_quota_mb}MB", key=key)
                else:
                    await resolve(key)
        await s.commit()
