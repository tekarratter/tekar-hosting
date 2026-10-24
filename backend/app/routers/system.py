from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from ..config import settings
from ..core.audit import audit
from ..core.deps import DB, AdminUser, CurrentUser, StaffUser, check_app, is_staff, visible_app_ids
from ..core.jobs import enqueue
from ..core.store import DEFAULTS, all_settings, set_setting
from ..models import Alert, App, AuditLog, Backup, Job, ProxyRoute, ScanResult
from ..services import docker_svc

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/dashboard")
async def dashboard(user: CurrentUser, db: DB):
    ids = await visible_app_ids(user, db)
    q = select(App)
    if ids is not None:
        q = q.where(App.id.in_(ids or [-1]))
    apps = (await db.execute(q)).scalars().all()
    containers = await docker_svc.list_containers()
    if ids is not None:
        projects = {a.compose_project for a in apps}
        containers = [c for c in containers if c["project"] in projects]
    alerts = (await db.execute(select(Alert).where(Alert.resolved.is_(False)).order_by(Alert.id.desc()).limit(20))).scalars().all() if is_staff(user) else []
    failed_backups = (await db.execute(select(func.count()).select_from(Backup).where(Backup.status == "failed"))).scalar()
    cfg_scan = (await db.execute(select(ScanResult).where(ScanResult.kind == "config").order_by(ScanResult.id.desc()).limit(1))).scalar_one_or_none()
    routes = (await db.execute(select(func.count()).select_from(ProxyRoute).where(ProxyRoute.enabled.is_(True)))).scalar()
    down_routes = (await db.execute(select(func.count()).select_from(ProxyRoute).where(ProxyRoute.health_status == "down"))).scalar()
    return {
        "apps": len(apps),
        "containers": {
            "total": len(containers),
            "running": sum(1 for c in containers if c["state"] == "running"),
            "unhealthy": sum(1 for c in containers if c["health"] == "unhealthy" or c["state"] == "restarting"),
            "stopped": sum(1 for c in containers if c["state"] in ("exited", "dead", "created")),
        },
        "routes": {"total": routes, "down": down_routes},
        "alerts": [{"id": a.id, "level": a.level, "title": a.title, "message": a.message, "created_at": a.created_at} for a in alerts],
        "failed_backups": failed_backups,
        "security": cfg_scan.summary if cfg_scan and is_staff(user) else None,
    }


# ---------------------------------------------------------------- jobs
def job_out(j: Job, with_log: bool = False) -> dict[str, Any]:
    d = {"id": j.id, "kind": j.kind, "status": j.status, "app_id": j.app_id, "params": j.params, "result": j.result,
         "created_by": j.created_by, "created_at": j.created_at, "started_at": j.started_at, "finished_at": j.finished_at}
    if with_log:
        d["log"] = j.log
    return d


@router.get("/jobs")
async def jobs(user: CurrentUser, db: DB, limit: int = 50, kind: str | None = None):
    q = select(Job).order_by(Job.id.desc()).limit(limit)
    if kind:
        q = q.where(Job.kind == kind)
    ids = await visible_app_ids(user, db)
    if ids is not None:
        q = q.where(Job.app_id.in_(ids or [-1]))
    return [job_out(j) for j in (await db.execute(q)).scalars()]


@router.get("/jobs/{job_id}")
async def job(job_id: int, user: CurrentUser, db: DB):
    j = await db.get(Job, job_id)
    if j is None:
        raise HTTPException(404)
    if not is_staff(user):
        if j.app_id is None:
            raise HTTPException(403)
        await check_app(user, j.app_id, db)
    return job_out(j, with_log=True)


# ---------------------------------------------------------------- audit / alerts
@router.get("/audit")
async def audit_logs(user: StaffUser, db: DB, limit: int = 200, action: str | None = None, q: str | None = None):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if q:
        stmt = stmt.where((AuditLog.target.ilike(f"%{q}%")) | (AuditLog.user_email.ilike(f"%{q}%")))
    return [
        {"id": a.id, "user": a.user_email, "action": a.action, "target": a.target, "detail": a.detail, "ip": a.ip, "created_at": a.created_at}
        for a in (await db.execute(stmt)).scalars()
    ]


@router.get("/alerts")
async def alerts(user: StaffUser, db: DB, resolved: bool = False, limit: int = 100):
    rows = (await db.execute(select(Alert).where(Alert.resolved.is_(resolved)).order_by(Alert.id.desc()).limit(limit))).scalars()
    return [{"id": a.id, "key": a.key, "level": a.level, "title": a.title, "message": a.message, "resolved": a.resolved,
             "created_at": a.created_at, "resolved_at": a.resolved_at} for a in rows]


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int, user: StaffUser, db: DB):
    a = await db.get(Alert, alert_id)
    if a:
        a.resolved, a.resolved_at = True, datetime.now(UTC)
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- settings
@router.get("/settings")
async def get_settings(user: AdminUser, db: DB):
    s = await all_settings(db)
    s["_info"] = {"data_dir": str(settings.data_dir), "apps_root": str(settings.apps_root), "edge_network": settings.edge_network}
    return s


@router.put("/settings")
async def put_settings(body: dict[str, Any], user: AdminUser, db: DB, request: Request):
    changed = []
    for k, v in body.items():
        if k not in DEFAULTS:
            continue
        if k.endswith("_secret") and (v is None or str(v).startswith("••••")):
            continue  # giữ nguyên secret khi client gửi lại giá trị đã che
        await set_setting(k, v, db)
        changed.append(k)
    await db.commit()
    await audit(db, user, "settings.update", "", {"keys": changed}, request)
    return {"ok": True, "changed": changed}


@router.post("/settings/test-notify")
async def test_notify(user: AdminUser):
    from ..services.notify import send_external

    await send_external("info", "Thử thông báo", f"Gửi bởi {user.email}")
    return {"ok": True}


@router.post("/settings/test-disk-alert")
async def test_disk_alert(user: AdminUser, db: DB, request: Request):
    """Gửi ngay tin cảnh báo ổ đĩa (đánh dấu THỬ) kèm nút thật — bấm nút sẽ xoá image cũ thật."""
    from ..services import disk_alert

    cfg = await disk_alert._cfg()
    if not cfg:
        raise HTTPException(400, "Bật tính năng và nhập Telegram bot token + chat ID, rồi lưu trước khi gửi thử")
    try:
        await disk_alert.send_alert(cfg, disk_alert.disk_pct(), test=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    await audit(db, user, "settings.test_disk_alert", "", None, request)
    return {"ok": True}


# ---------------------------------------------------------------- docker housekeeping
class PruneIn(BaseModel):
    all_images: bool = False
    volumes: bool = False


@router.post("/system/prune")
async def prune(body: PruneIn, user: AdminUser, db: DB, request: Request):
    job = await enqueue(db, "docker_prune", body.model_dump(), None, user.id)
    await audit(db, user, "system.prune", "", body.model_dump(), request)
    return {"job_id": job.id}


@router.post("/system/panel-backup")
async def panel_backup_now(user: AdminUser, db: DB, request: Request):
    job = await enqueue(db, "panel_backup", {}, None, user.id)
    await audit(db, user, "system.panel_backup", "", request=request)
    return {"job_id": job.id}


@router.get("/system/panel-backups")
async def panel_backups(user: AdminUser):
    from ..services import panel_backup

    return {"files": panel_backup.list_local(), "dir": str(panel_backup.local_dir()), "targets": await panel_backup.targets_for_ui()}


@router.get("/system/df")
async def disk_usage(user: StaffUser):
    def _df():
        d = docker_svc.client().df()
        return {
            "images": {"count": len(d.get("Images") or []), "size": sum(i.get("Size", 0) for i in d.get("Images") or [])},
            "containers": {"count": len(d.get("Containers") or []), "size": sum(c.get("SizeRw", 0) or 0 for c in d.get("Containers") or [])},
            "volumes": {"count": len(d.get("Volumes") or []), "size": sum((v.get("UsageData") or {}).get("Size", 0) for v in d.get("Volumes") or [])},
            "build_cache": {"count": len(d.get("BuildCache") or []), "size": sum(b.get("Size", 0) for b in d.get("BuildCache") or [])},
        }

    return await docker_svc.run(_df)


@router.get("/system/info")
async def info(user: StaffUser):
    def _info():
        i = docker_svc.client().info()
        return {"docker_version": i.get("ServerVersion"), "os": i.get("OperatingSystem"), "kernel": i.get("KernelVersion"),
                "cpus": i.get("NCPU"), "mem_total": i.get("MemTotal"), "containers": i.get("Containers"), "images": i.get("Images"),
                "storage_driver": i.get("Driver"), "cgroup": i.get("CgroupVersion")}

    return await docker_svc.run(_info)


# ---------------------------------------------------------------- deploy webhook (không cần đăng nhập)
hooks = APIRouter(prefix="/api/hooks", tags=["hooks"])


@hooks.post("/deploy/{token}")
async def deploy_hook(token: str, db: DB, request: Request):
    if len(token) < 20:
        raise HTTPException(404)
    app = (await db.execute(select(App).where(App.deploy_token == token))).scalar_one_or_none()
    if app is None:
        raise HTTPException(404)
    g = app.git or {}
    if g.get("repo"):
        # webhook push của GitHub/GitLab: bỏ qua sự kiện ping và push vào nhánh khác
        if request.headers.get("x-github-event") == "ping":
            return {"ok": True, "app": app.slug, "note": "pong"}
        ref = None
        try:
            ref = (await request.json()).get("ref")
        except Exception:  # noqa: BLE001
            pass
        if ref and g.get("branch") and ref != f"refs/heads/{g['branch']}":
            return {"ok": True, "app": app.slug, "skipped": f"{ref} không phải nhánh {g['branch']}"}
        job = await enqueue(db, "git_update", {"app_id": app.id, "deploy": True, "trigger": "webhook"}, app.id, None)
    else:
        job = await enqueue(db, "deploy", {"app_id": app.id, "pull": True}, app.id, None)
    await audit(db, None, "app.deploy_webhook", app.slug, request=request)
    return {"job_id": job.id, "app": app.slug}
