"""Worker: xử lý job queue + lịch định kỳ (metrics, backup, quét bảo mật, health check, cảnh báo, báo cáo)."""

import asyncio
import logging
import time
from datetime import UTC, datetime

import httpx
from croniter import croniter
from sqlalchemy import select

from .config import settings
from .core import jobs
from .core.jobs import enqueue, job_handler
from .core.store import get_setting
from .db import SessionLocal, init_db
from .models import App, Job, ProxyRoute, Report, User

# đăng ký job handler
from .services import app_clone, app_jobs, backup, caddy_ports, panel_backup, security  # noqa: F401
from .routers import stacks as _stacks  # noqa: F401  (job stack_convert)
from .routers.storage import refresh_stack_usage  # (job stack_usage)
from .services import disk_alert, metrics
from .services.notify import notify, resolve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("th.worker")
MAX_CONCURRENT = 3


@job_handler("claude_report")
async def job_report(ctx, params):
    from .ai.assistant import generate_report

    async with SessionLocal() as s:
        user = await s.get(User, params["user_id"]) if params.get("user_id") else (
            await s.execute(select(User).where(User.role == "admin").order_by(User.id).limit(1))
        ).scalar_one()
    await ctx.log("Claude đang thu thập dữ liệu và viết báo cáo…")
    content = await generate_report(user)
    title = f"Báo cáo hệ thống {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    async with SessionLocal() as s:
        r = Report(title=title, content=content, created_by=user.id)
        s.add(r)
        await s.commit()
    if params.get("send"):
        from .services.notify import send_external

        await send_external("info", title, content[:3500])
    return {"report_id": r.id}


# ---------------------------------------------------------------- job loop
async def job_loop() -> None:
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def run(job_id: int):
        async with sem:
            await jobs.run_job(job_id)

    while True:
        try:
            if not sem.locked():
                job_id = await jobs.claim_next()
                if job_id:
                    asyncio.create_task(run(job_id))
                    continue
        except Exception:  # noqa: BLE001
            log.exception("job loop")
        await asyncio.sleep(1)


# ---------------------------------------------------------------- checks
_down_since: dict[str, float] = {}


async def check_alerts(sample: dict) -> None:
    host = sample["host"]
    cpu_t = float(await get_setting("alert_cpu_pct") or 90)
    mem_t = float(await get_setting("alert_mem_pct") or 90)
    disk_t = float(await get_setting("alert_disk_pct") or 85)
    mem_pct = host["mem_used"] / host["mem_total"] * 100
    disk_pct = host["disk_used"] / host["disk_total"] * 100
    checks = [("host:mem", mem_pct, mem_t, "RAM")]
    if await get_setting("disk_tg_enabled"):  # cảnh báo ổ đĩa do disk_alert gửi (Telegram + nút xoá image)
        await disk_alert.check()
        await resolve("host:disk")
    else:
        checks.append(("host:disk", disk_pct, disk_t, "Ổ đĩa"))
    for key, val, thr, label in checks:
        if val >= thr:
            await notify("warning" if val < 97 else "critical", f"{label} host cao: {val:.0f}%", f"Ngưỡng {thr:.0f}%", key=key)
        elif val < thr - 5:
            await resolve(key)
    # CPU: chỉ cảnh báo khi kéo dài (≥5 phút)
    now = time.time()
    if host["cpu_pct"] >= cpu_t:
        _down_since.setdefault("host:cpu", now)
        if now - _down_since["host:cpu"] > 300:
            await notify("warning", f"CPU host cao kéo dài: {host['cpu_pct']:.0f}%", "", key="host:cpu")
    else:
        _down_since.pop("host:cpu", None)
        await resolve("host:cpu")
    # container sắp chạm giới hạn RAM
    for c in sample["containers"]:
        if c["mem_limit"] and c["mem_bytes"] / c["mem_limit"] > 0.95:
            await notify("warning", f"{c['name']} gần chạm giới hạn RAM", f"{c['mem_bytes'] // 2**20}MB / {c['mem_limit'] // 2**20}MB", key=f"memlimit:{c['name']}")
        elif c["mem_limit"]:
            await resolve(f"memlimit:{c['name']}")


async def check_containers() -> None:
    """Container của app được quản lý bị dừng/restart loop/unhealthy."""
    from .services import docker_svc

    async with SessionLocal() as s:
        apps = {a.compose_project: a for a in (await s.execute(select(App))).scalars()}
    now = time.time()
    for c in await docker_svc.list_containers():
        app = apps.get(c["project"] or "")
        if not app:
            continue
        key = f"container:{c['name']}"
        bad = c["state"] in ("exited", "dead", "restarting") or c["health"] == "unhealthy"
        if bad and app.status not in ("stopped",):
            first = _down_since.setdefault(key, now)
            if now - first > 120:
                await notify("critical", f"{c['name']} ({app.name}) {c['health'] or c['state']}", "Xem log trong panel", key=key)
        else:
            _down_since.pop(key, None)
            await resolve(key)
    # đồng bộ trạng thái app
    for a in apps.values():
        try:
            await app_jobs.refresh_status(a)
        except Exception:  # noqa: BLE001
            pass


async def check_routes() -> None:
    """Health check: gọi thẳng upstream (không qua Caddy) để biết backend còn sống."""
    from .services.proxy import parse_upstream

    async with SessionLocal() as s:
        routes = (await s.execute(select(ProxyRoute).where(ProxyRoute.enabled.is_(True)))).scalars().all()
    async with httpx.AsyncClient(timeout=10, verify=False, follow_redirects=False) as http:
        for r in routes:
            o = r.options or {}
            if (r.kind or "proxy") != "proxy" or not r.upstreams or o.get("maintenance") or o.get("redirect_to"):
                continue
            host = r.hosts[0]
            results = []
            for u in r.upstreams:
                try:
                    dial, https = parse_upstream(u)
                except ValueError:
                    continue
                path = r.health_path or o.get("health_uri") or "/"
                try:
                    resp = await http.get(f"{'https' if https else 'http'}://{dial}{path}",
                                          headers={"Host": host, "User-Agent": "TekarHosting-Health/1"})
                    results.append(resp.status_code < 500)
                except httpx.HTTPError:
                    results.append(False)
            if not results:
                continue
            status = "up" if all(results) else ("degraded" if any(results) else "down")
            key = f"route:{r.id}"
            if status == "down":
                await notify("critical", f"Domain {host} không phản hồi", f"Upstream: {', '.join(r.upstreams)}", key=key)
            else:
                await resolve(key)
            async with SessionLocal() as s:
                row = await s.get(ProxyRoute, r.id)
                if row:
                    row.health_status, row.health_checked_at = status, datetime.now(UTC)
                    await s.commit()


async def check_certs() -> None:
    """Cảnh báo chứng chỉ sắp hết hạn: cert tải lên (không tự gia hạn) và cert ACME (gia hạn lỗi)."""
    from .models import Certificate
    from .services.proxy import caddy_certs

    now = datetime.now(UTC)
    async with SessionLocal() as s:
        for c in (await s.execute(select(Certificate))).scalars():
            if not c.not_after:
                continue
            days = (c.not_after - now).days
            key = f"cert:custom:{c.id}"
            if days <= 14:
                await notify("critical" if days <= 3 else "warning", f"Chứng chỉ '{c.name}' hết hạn sau {days} ngày",
                             ", ".join(c.domains), key=key)
            else:
                await resolve(key)
    for c in await caddy_certs():
        key = f"cert:acme:{c['name']}"
        if c["days_left"] <= 10:  # Caddy gia hạn trước ~30 ngày → còn <10 ngày là gia hạn đang lỗi
            await notify("warning", f"Cert {c['name']} chưa gia hạn được (còn {c['days_left']} ngày)", "Xem log th-caddy", key=key)
        else:
            await resolve(key)


async def schedule_crons(now: datetime) -> None:
    """Chạy mỗi phút: backup theo cron của app, quét bảo mật, báo cáo Claude, auto update."""
    minute = now.replace(second=0, microsecond=0)

    def due(expr: str | None) -> bool:
        if not expr or not croniter.is_valid(expr):
            return False
        return croniter.match(expr, minute)

    async with SessionLocal() as s:
        for app in (await s.execute(select(App))).scalars():
            if due(app.backup_cron):
                await enqueue(s, "backup", {"app_id": app.id, "trigger": "schedule"}, app.id)
            if app.auto_update and due("30 4 * * *"):
                await enqueue(s, "auto_update", {"app_id": app.id}, app.id)
        if due(await get_setting("scan_cron", s)):
            await enqueue(s, "scan_config", {})
            await enqueue(s, "scan_images", {})
        if due(await get_setting("report_cron", s)):
            await enqueue(s, "claude_report", {"send": True})
        if due(await get_setting("panel_backup_cron", s)):
            await enqueue(s, "panel_backup", {})


async def periodic(name: str, interval: float, fn, *args) -> None:
    while True:
        t0 = time.time()
        try:
            await fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("periodic %s", name)
        await asyncio.sleep(max(1.0, interval - (time.time() - t0)))


async def metrics_tick() -> None:
    sample = await metrics.collect_once()
    await check_alerts(sample)


async def cron_tick() -> None:
    await schedule_crons(datetime.now())


async def reset_stuck_jobs() -> None:
    async with SessionLocal() as s:
        for j in (await s.execute(select(Job).where(Job.status.in_(("running", "claimed"))))).scalars():
            j.status = "failed"
            j.result = {"error": "Worker khởi động lại khi job đang chạy"}
        await s.commit()


async def main() -> None:
    await init_db()
    await reset_stuck_jobs()
    log.info("Worker started")

    async def cron_aligned():
        while True:
            await asyncio.sleep(60 - datetime.now().second + 0.5)
            try:
                await cron_tick()
            except Exception:  # noqa: BLE001
                log.exception("cron")

    await asyncio.gather(
        job_loop(),
        periodic("metrics", settings.metrics_interval, metrics_tick),
        periodic("containers", 60, check_containers),
        periodic("routes", 120, check_routes),
        periodic("certs", 6 * 3600, check_certs),
        periodic("prune", 3600, metrics.prune),
        periodic("disk", 1800, metrics.update_app_disk_usage),
        periodic("stack_usage", 6 * 3600, refresh_stack_usage),
        periodic("git_poll", 300, app_jobs.git_poll),
        disk_alert.poll_loop(),
        cron_aligned(),
    )


if __name__ == "__main__":
    asyncio.run(main())
