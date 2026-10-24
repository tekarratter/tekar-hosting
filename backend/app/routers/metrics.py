from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from ..core.deps import DB, CurrentUser, StaffUser, check_app, visible_app_ids
from ..models import App, ContainerSample, HostSample

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


async def _latest_ts(db, model):
    return (await db.execute(select(func.max(model.ts)))).scalar()


def _bucket(hours: int) -> int:
    """Độ phân giải (giây) để trả về ~240 điểm."""
    return max(30, int(hours * 3600 / 240))


@router.get("/host")
async def host(user: StaffUser, db: DB, hours: int = 6):
    since = datetime.now(UTC) - timedelta(hours=hours)
    b = _bucket(hours)
    bucket = func.to_timestamp(func.floor(func.extract("epoch", HostSample.ts) / b) * b).label("t")
    rows = (
        await db.execute(
            select(bucket, func.avg(HostSample.cpu_pct), func.avg(HostSample.mem_used), func.max(HostSample.mem_total),
                   func.max(HostSample.disk_used), func.max(HostSample.disk_total), func.avg(HostSample.load1))
            .where(HostSample.ts >= since).group_by("t").order_by("t")
        )
    ).all()
    latest = (await db.execute(select(HostSample).order_by(HostSample.ts.desc()).limit(1))).scalar_one_or_none()
    return {
        "latest": {k: getattr(latest, k) for k in ("ts", "cpu_pct", "load1", "mem_used", "mem_total", "disk_used", "disk_total")} if latest else None,
        "series": [{"t": r[0], "cpu": round(r[1] or 0, 1), "mem": int(r[2] or 0), "mem_total": r[3], "disk": r[4], "disk_total": r[5], "load": round(r[6] or 0, 2)} for r in rows],
    }


@router.get("/containers")
async def containers_latest(user: CurrentUser, db: DB):
    ts = await _latest_ts(db, ContainerSample)
    if ts is None:
        return []
    rows = (await db.execute(select(ContainerSample).where(ContainerSample.ts == ts))).scalars().all()
    ids = await visible_app_ids(user, db)
    if ids is not None:
        projects = set((await db.execute(select(App.compose_project).where(App.id.in_(ids or [-1])))).scalars())
        rows = [r for r in rows if r.project in projects]
    return [
        {"name": r.name, "project": r.project, "cpu_pct": r.cpu_pct, "mem_bytes": r.mem_bytes, "mem_limit": r.mem_limit,
         "net_rx": r.net_rx, "net_tx": r.net_tx, "blk_read": r.blk_read, "blk_write": r.blk_write, "ts": r.ts}
        for r in sorted(rows, key=lambda x: -x.mem_bytes)
    ]


@router.get("/apps/{app_id}")
async def app_series(app_id: int, user: CurrentUser, db: DB, hours: int = 6):
    app = await check_app(user, app_id, db)
    since = datetime.now(UTC) - timedelta(hours=hours)
    b = _bucket(hours)
    # cộng theo từng mốc thời gian gốc, rồi lấy trung bình trong bucket
    per_ts = (
        select(ContainerSample.ts.label("ts"), ContainerSample.name, func.sum(ContainerSample.cpu_pct).label("cpu"),
               func.sum(ContainerSample.mem_bytes).label("mem"))
        .where(ContainerSample.project == app.compose_project, ContainerSample.ts >= since)
        .group_by(ContainerSample.ts, ContainerSample.name)
    ).subquery()
    bucket2 = func.to_timestamp(func.floor(func.extract("epoch", per_ts.c.ts) / b) * b).label("t")
    rows = (
        await db.execute(
            select(bucket2, per_ts.c.name, func.avg(per_ts.c.cpu), func.avg(per_ts.c.mem)).group_by("t", per_ts.c.name).order_by("t")
        )
    ).all()
    series: dict[str, list] = {}
    for t, name, cpu, mem in rows:
        series.setdefault(name, []).append({"t": t, "cpu": round(cpu or 0, 2), "mem": int(mem or 0)})
    return {"containers": series}


@router.get("/allocation")
async def allocation(user: CurrentUser, db: DB):
    """Cấp phát (limit) so với sử dụng thực tế, theo app."""
    ids = await visible_app_ids(user, db)
    q = select(App)
    if ids is not None:
        q = q.where(App.id.in_(ids or [-1]))
    apps = (await db.execute(q)).scalars().all()
    ts = await _latest_ts(db, ContainerSample)
    usage: dict[str, dict] = {}
    if ts:
        for r in (await db.execute(select(ContainerSample).where(ContainerSample.ts == ts))).scalars():
            u = usage.setdefault(r.project or "", {"cpu": 0.0, "mem": 0})
            u["cpu"] += r.cpu_pct
            u["mem"] += r.mem_bytes
    host = (await db.execute(select(HostSample).order_by(HostSample.ts.desc()).limit(1))).scalar_one_or_none()
    import os

    out = []
    for a in apps:
        u = usage.get(a.compose_project, {"cpu": 0.0, "mem": 0})
        svc_cpu = sum(float(v.get("cpus") or 0) for v in (a.service_limits or {}).values())
        svc_mem = sum(int(v.get("memory_mb") or 0) for v in (a.service_limits or {}).values())
        out.append({
            "app_id": a.id, "slug": a.slug, "name": a.name,
            "cpu_quota": a.cpu_limit, "cpu_allocated": svc_cpu or None, "cpu_used": round(u["cpu"] / 100, 3),
            "mem_quota_mb": a.mem_limit_mb, "mem_allocated_mb": svc_mem or None, "mem_used_mb": round(u["mem"] / 1024 / 1024, 1),
            "disk_quota_mb": a.disk_quota_mb, "disk_used_mb": a.disk_usage_mb,
        })
    unmanaged = {p: v for p, v in usage.items() if p not in {a.compose_project for a in apps}}
    return {
        "host": {"cpus": os.cpu_count(), "mem_total": host.mem_total if host else None, "disk_total": host.disk_total if host else None,
                 "disk_used": host.disk_used if host else None},
        "apps": out,
        "unmanaged": [{"project": p or "(standalone)", "cpu_used": round(v["cpu"] / 100, 3), "mem_used_mb": round(v["mem"] / 1024 / 1024, 1)}
                      for p, v in unmanaged.items()] if ids is None else [],
        "totals": {
            "cpu_quota": sum(a.cpu_limit or 0 for a in apps),
            "mem_quota_mb": sum(a.mem_limit_mb or 0 for a in apps),
            "disk_quota_mb": sum(a.disk_quota_mb or 0 for a in apps),
        },
    }
