"""Bộ công cụ vận hành dùng chung cho trợ lý Claude (web chat) và MCP server (Claude Code).

Mỗi tool kiểm tra quyền theo user đang gọi. Tool `write=True` thay đổi hệ thống:
  - Web chat: cần người dùng bấm Duyệt trước khi chạy.
  - MCP: Claude Code tự hỏi quyền theo cấu hình permission của nó.
"""

import json
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from ..core.audit import audit
from ..core.deps import check_app, is_staff, visible_app_ids
from ..core.jobs import enqueue
from ..db import SessionLocal
from ..models import Alert, App, AuditLog, Backup, ContainerSample, HostSample, Job, PlatformService, ProxyRoute, ScanResult, User
from ..services import docker_svc

MAX_OUT = 24000


@dataclass
class ToolContext:
    user: User
    source: str = "chat"  # chat | mcp | report


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    fn: Callable[..., Awaitable[Any]]
    write: bool = False
    staff_only: bool = False
    required: list[str] = field(default_factory=list)

    def api_def(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None,
         write: bool = False, staff_only: bool = False):
    schema = {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}

    def deco(fn):
        TOOLS[name] = Tool(name, description, schema, fn, write, staff_only, required or [])
        return fn

    return deco


async def _app_by_slug(ctx: ToolContext, db, slug: str, min_role: str = "viewer") -> App:
    app = (await db.execute(select(App).where(App.slug == slug))).scalar_one_or_none()
    if app is None:
        raise ValueError(f"Không có app '{slug}'")
    await check_app(ctx.user, app.id, db, min_role)
    return app


async def _authorize_container(ctx: ToolContext, db, name: str, min_role: str = "viewer"):
    from ..routers.containers import authorize_container

    return await authorize_container(ctx.user, name, db, min_role)


# ======================================================================= read tools
@tool("list_apps", "Liệt kê các ứng dụng (app) người dùng được thấy, kèm trạng thái và số container.")
async def list_apps(ctx: ToolContext):
    async with SessionLocal() as db:
        ids = await visible_app_ids(ctx.user, db)
        q = select(App)
        if ids is not None:
            q = q.where(App.id.in_(ids or [-1]))
        apps = (await db.execute(q)).scalars().all()
    by_proj: dict[str, list] = {}
    for c in await docker_svc.list_containers():
        by_proj.setdefault(c["project"] or "", []).append(c)
    return [
        {"slug": a.slug, "name": a.name, "kind": a.kind, "project": a.compose_project,
         "containers": [{"name": c["name"], "state": c["state"], "health": c["health"]} for c in by_proj.get(a.compose_project, [])],
         "cpu_quota": a.cpu_limit, "mem_quota_mb": a.mem_limit_mb, "disk_used_mb": a.disk_usage_mb, "backup_cron": a.backup_cron}
        for a in apps
    ]


@tool("get_app", "Chi tiết một app: container, giới hạn tài nguyên, route proxy, dịch vụ nền tảng, backup gần nhất.",
      {"slug": {"type": "string"}}, ["slug"])
async def get_app(ctx: ToolContext, slug: str):
    async with SessionLocal() as db:
        app = await _app_by_slug(ctx, db, slug)
        routes = (await db.execute(select(ProxyRoute).where(ProxyRoute.app_id == app.id))).scalars().all()
        from ..models import ServiceBinding

        binds = (await db.execute(select(ServiceBinding).where(ServiceBinding.app_id == app.id))).scalars().all()
        last = (await db.execute(select(Backup).where(Backup.app_id == app.id).order_by(Backup.id.desc()).limit(3))).scalars().all()
    cs = await docker_svc.project_containers(app.compose_project)
    return {
        "slug": app.slug, "name": app.name, "kind": app.kind, "work_dir": app.work_dir, "compose_files": app.compose_files,
        "service_limits": app.service_limits, "edge_services": app.edge_services,
        "quota": {"cpu": app.cpu_limit, "mem_mb": app.mem_limit_mb, "disk_mb": app.disk_quota_mb, "disk_used_mb": app.disk_usage_mb},
        "containers": [docker_svc.summarize(c) for c in cs],
        "routes": [{"id": r.id, "hosts": r.hosts, "path": r.path_prefix, "upstreams": r.upstreams, "enabled": r.enabled, "health": r.health_status} for r in routes],
        "services": [{"kind": b.service.kind, "service": b.service.name, "resource": b.resource} for b in binds],
        "backups": [{"id": b.id, "status": b.status, "at": b.started_at.isoformat(), "size": b.size_bytes, "error": b.error} for b in last],
        "backup_policy": {"cron": app.backup_cron, "retention": app.backup_retention},
    }


@tool("list_containers", "Liệt kê container (lọc theo project compose hoặc trạng thái: running/exited/restarting).",
      {"project": {"type": "string"}, "state": {"type": "string"}})
async def list_containers(ctx: ToolContext, project: str | None = None, state: str | None = None):
    items = await docker_svc.list_containers()
    if not is_staff(ctx.user):
        async with SessionLocal() as db:
            ids = await visible_app_ids(ctx.user, db) or set()
            allowed = set((await db.execute(select(App.compose_project).where(App.id.in_(ids or [-1])))).scalars())
        items = [c for c in items if c["project"] in allowed]
    if project:
        items = [c for c in items if c["project"] == project]
    if state:
        items = [c for c in items if c["state"] == state]
    return items


@tool("inspect_container", "Xem cấu hình container (env đã che secret, mounts, ports, restart policy, exit code).",
      {"name": {"type": "string"}}, ["name"])
async def inspect_container(ctx: ToolContext, name: str):
    async with SessionLocal() as db:
        c = await _authorize_container(ctx, db, name)
    return docker_svc.inspect_safe(c)


@tool("container_logs", "Đọc log gần nhất của container. `grep` để lọc dòng chứa chuỗi (không phân biệt hoa thường).",
      {"name": {"type": "string"}, "tail": {"type": "integer", "minimum": 1, "maximum": 3000}, "grep": {"type": "string"}}, ["name"])
async def container_logs(ctx: ToolContext, name: str, tail: int = 200, grep: str | None = None):
    async with SessionLocal() as db:
        c = await _authorize_container(ctx, db, name)
    text = await docker_svc.logs(c.id, tail=tail)
    if grep:
        g = grep.lower()
        text = "\n".join(line for line in text.splitlines() if g in line.lower())
    return text[-MAX_OUT:]


@tool("resource_usage", "CPU/RAM/disk của host và top container theo RAM/CPU (mẫu mới nhất).")
async def resource_usage(ctx: ToolContext):
    async with SessionLocal() as db:
        host = (await db.execute(select(HostSample).order_by(HostSample.ts.desc()).limit(1))).scalar_one_or_none()
        ts = (await db.execute(select(ContainerSample.ts).order_by(ContainerSample.ts.desc()).limit(1))).scalar()
        rows = (await db.execute(select(ContainerSample).where(ContainerSample.ts == ts))).scalars().all() if ts else []
        ids = await visible_app_ids(ctx.user, db)
        if ids is not None:
            allowed = set((await db.execute(select(App.compose_project).where(App.id.in_(ids or [-1])))).scalars())
            rows = [r for r in rows if r.project in allowed]
    mb = 1024 * 1024
    return {
        "host": {"cpu_pct": host.cpu_pct, "load1": host.load1, "mem_used_mb": host.mem_used // mb, "mem_total_mb": host.mem_total // mb,
                 "disk_used_gb": round(host.disk_used / 1e9, 1), "disk_total_gb": round(host.disk_total / 1e9, 1)} if host and is_staff(ctx.user) else None,
        "top_mem": [{"name": r.name, "mem_mb": r.mem_bytes // mb, "limit_mb": r.mem_limit // mb if r.mem_limit else None, "cpu_pct": r.cpu_pct}
                    for r in sorted(rows, key=lambda x: -x.mem_bytes)[:15]],
        "top_cpu": [{"name": r.name, "cpu_pct": r.cpu_pct} for r in sorted(rows, key=lambda x: -x.cpu_pct)[:10]],
    }


@tool("list_proxy_routes", "Danh sách route reverse proxy (domain → upstream), trạng thái health.",
      {"app_slug": {"type": "string"}})
async def list_proxy_routes(ctx: ToolContext, app_slug: str | None = None):
    async with SessionLocal() as db:
        q = select(ProxyRoute)
        if app_slug:
            app = await _app_by_slug(ctx, db, app_slug)
            q = q.where(ProxyRoute.app_id == app.id)
        elif not is_staff(ctx.user):
            ids = await visible_app_ids(ctx.user, db) or set()
            q = q.where(ProxyRoute.app_id.in_(ids or [-1]))
        rows = (await db.execute(q)).scalars().all()
    return [{"id": r.id, "app_id": r.app_id, "hosts": r.hosts, "path": r.path_prefix, "upstreams": r.upstreams, "tls_mode": r.tls_mode,
             "cf_tunnel": r.cf_tunnel, "enabled": r.enabled, "health": r.health_status,
             "options": {k: v for k, v in (r.options or {}).items() if k != "basic_auth"} | ({"basic_auth": "on"} if (r.options or {}).get("basic_auth") else {})}
            for r in rows]


@tool("list_backups", "Các bản backup gần đây (toàn hệ thống hoặc theo app).", {"app_slug": {"type": "string"}})
async def list_backups(ctx: ToolContext, app_slug: str | None = None):
    async with SessionLocal() as db:
        q = select(Backup, App.slug).join(App, App.id == Backup.app_id).order_by(Backup.id.desc()).limit(40)
        if app_slug:
            app = await _app_by_slug(ctx, db, app_slug)
            q = q.where(Backup.app_id == app.id)
        else:
            ids = await visible_app_ids(ctx.user, db)
            if ids is not None:
                q = q.where(Backup.app_id.in_(ids or [-1]))
        rows = (await db.execute(q)).all()
    return [{"id": b.id, "app": s, "status": b.status, "trigger": b.trigger, "at": b.started_at.isoformat(), "size_mb": round(b.size_bytes / 1e6, 1),
             "remote": b.remote_key, "error": b.error} for b, s in rows]


@tool("security_overview", "Kết quả quét bảo mật mới nhất: lỗi cấu hình container + tóm tắt lỗ hổng theo image.", staff_only=True)
async def security_overview(ctx: ToolContext):
    from sqlalchemy import func

    async with SessionLocal() as db:
        cfg = (await db.execute(select(ScanResult).where(ScanResult.kind == "config").order_by(ScanResult.id.desc()).limit(1))).scalar_one_or_none()
        latest = select(func.max(ScanResult.id)).where(ScanResult.kind == "image").group_by(ScanResult.target)
        imgs = (await db.execute(select(ScanResult).where(ScanResult.id.in_(latest)))).scalars().all()
    return {
        "config_scan": {"at": cfg.created_at.isoformat(), "summary": cfg.summary,
                        "findings": [f for f in cfg.findings if f["severity"] in ("HIGH", "CRITICAL", "MEDIUM")][:80]} if cfg else None,
        "images": sorted([{"image": i.target, "summary": i.summary, "at": i.created_at.isoformat()} for i in imgs],
                         key=lambda x: (-x["summary"].get("CRITICAL", 0), -x["summary"].get("HIGH", 0)))[:40],
    }


@tool("image_vulnerabilities", "Danh sách lỗ hổng của một image (kết quả Trivy mới nhất).", {"image": {"type": "string"}}, ["image"], staff_only=True)
async def image_vulnerabilities(ctx: ToolContext, image: str):
    async with SessionLocal() as db:
        r = (await db.execute(select(ScanResult).where(ScanResult.kind == "image", ScanResult.target == image).order_by(ScanResult.id.desc()).limit(1))).scalar_one_or_none()
    if r is None:
        return "Chưa có kết quả quét cho image này"
    return {"summary": r.summary, "at": r.created_at.isoformat(), "findings": r.findings[:100]}


@tool("list_platform_services", "Các dịch vụ nền tảng dùng chung (Postgres/Redis/S3) và trạng thái.", staff_only=True)
async def list_platform_services(ctx: ToolContext):
    from ..services import platform

    async with SessionLocal() as db:
        rows = (await db.execute(select(PlatformService))).scalars().all()
    out = []
    for s in rows:
        try:
            info = await platform.test_connection(s)
            out.append({"name": s.name, "kind": s.kind, "host": f"{s.host}:{s.port}", "online": True, "info": info})
        except Exception as e:  # noqa: BLE001
            out.append({"name": s.name, "kind": s.kind, "host": f"{s.host}:{s.port}", "online": False, "error": str(e)})
    return out


@tool("recent_jobs", "Các job gần đây (deploy, backup, scan…) và trạng thái.", {"limit": {"type": "integer", "maximum": 100}})
async def recent_jobs(ctx: ToolContext, limit: int = 20):
    async with SessionLocal() as db:
        q = select(Job).order_by(Job.id.desc()).limit(limit)
        ids = await visible_app_ids(ctx.user, db)
        if ids is not None:
            q = q.where(Job.app_id.in_(ids or [-1]))
        rows = (await db.execute(q)).scalars().all()
    return [{"id": j.id, "kind": j.kind, "status": j.status, "app_id": j.app_id, "at": j.created_at.isoformat(), "result": j.result} for j in rows]


@tool("job_log", "Log chi tiết của một job.", {"job_id": {"type": "integer"}}, ["job_id"])
async def job_log(ctx: ToolContext, job_id: int):
    async with SessionLocal() as db:
        j = await db.get(Job, job_id)
        if j is None:
            raise ValueError("Không có job")
        if not is_staff(ctx.user):
            if j.app_id is None:
                raise ValueError("Không có quyền")
            await check_app(ctx.user, j.app_id, db)
    return {"status": j.status, "log": j.log[-MAX_OUT:]}


@tool("list_alerts", "Cảnh báo đang mở (CPU/RAM/disk cao, container down, backup lỗi, lỗ hổng…).", staff_only=True)
async def list_alerts(ctx: ToolContext):
    async with SessionLocal() as db:
        rows = (await db.execute(select(Alert).where(Alert.resolved.is_(False)).order_by(Alert.id.desc()).limit(50))).scalars().all()
    return [{"level": a.level, "title": a.title, "message": a.message, "at": a.created_at.isoformat()} for a in rows]


@tool("audit_log", "Nhật ký thao tác gần đây của người dùng trên panel.", {"limit": {"type": "integer", "maximum": 200}}, staff_only=True)
async def audit_log(ctx: ToolContext, limit: int = 50):
    async with SessionLocal() as db:
        rows = (await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))).scalars().all()
    return [{"user": a.user_email, "action": a.action, "target": a.target, "at": a.created_at.isoformat()} for a in rows]


@tool("discover_projects", "Các dự án docker compose trên host chưa được panel quản lý.", staff_only=True)
async def discover_projects(ctx: ToolContext):
    async with SessionLocal() as db:
        managed = set((await db.execute(select(App.compose_project))).scalars())
    return [p for p in (await docker_svc.compose_projects()).values() if p["project"] not in managed]


@tool("read_compose", "Đọc nội dung file compose của app.", {"slug": {"type": "string"}}, ["slug"])
async def read_compose(ctx: ToolContext, slug: str):
    from ..services import compose

    async with SessionLocal() as db:
        app = await _app_by_slug(ctx, db, slug, "deployer")
    out = {}
    for f in compose.compose_files(app):
        try:
            out[f] = open(f).read()[:12000]
        except OSError as e:
            out[f] = f"(không đọc được: {e})"
    return out


@tool("list_stacks", "Mọi stack docker compose trên host kèm công cụ đang quản lý (Dockhand, Portainer, compose CLI, panel).", staff_only=True)
async def list_stacks(ctx: ToolContext):
    from ..services import stacks

    out = []
    for i in await docker_svc.run(stacks.detect_all):
        out.append({"project": i["project"], "manager": i["manager"]["label"], "running": i["running"],
                    "containers": len(i["containers"]), "work_dir": i["host_work_dir"] or i["work_dir"]})
    return out


@tool("proxy_access_log", "Access log của reverse proxy (lọc theo domain, status tối thiểu) — dùng để điều tra lỗi 4xx/5xx, IP lạ.",
      {"host": {"type": "string"}, "status_min": {"type": "integer"}, "limit": {"type": "integer", "maximum": 500}}, staff_only=True)
async def proxy_access_log(ctx: ToolContext, host: str | None = None, status_min: int | None = None, limit: int = 100):
    from ..services import proxy

    return await docker_svc.run(proxy.read_access_log, host, limit, status_min)


@tool("proxy_traffic_stats", "Thống kê request / 4xx / 5xx / thời gian phản hồi TB theo domain từ access log gần đây.", staff_only=True)
async def proxy_traffic_stats(ctx: ToolContext):
    from ..services import proxy

    return await docker_svc.run(proxy.access_stats)


@tool("list_backup_targets", "Các đích backup (local/S3/SFTP) và số app đang dùng.", staff_only=True)
async def list_backup_targets(ctx: ToolContext):
    from ..models import BackupTarget

    async with SessionLocal() as db:
        rows = (await db.execute(select(BackupTarget))).scalars().all()
        apps = (await db.execute(select(App.slug, App.backup_destination_id, App.backup_cron))).all()
    return {"targets": [{"id": t.id, "name": t.name, "kind": t.kind,
                         "where": t.config.get("host") or t.config.get("bucket") or t.config.get("path")} for t in rows],
            "apps": [{"slug": a, "destination_id": d, "cron": c} for a, d, c in apps]}


# ======================================================================= write tools
@tool("app_action", "Start / stop / restart toàn bộ app (docker compose).",
      {"slug": {"type": "string"}, "action": {"type": "string", "enum": ["start", "stop", "restart"]}}, ["slug", "action"], write=True)
async def app_action(ctx: ToolContext, slug: str, action: str):
    async with SessionLocal() as db:
        app = await _app_by_slug(ctx, db, slug, "deployer")
        job = await enqueue(db, "app_action", {"app_id": app.id, "action": action}, app.id, ctx.user.id)
        await audit(db, ctx.user, f"app.{action}", app.slug, {"via": f"claude-{ctx.source}"})
    return {"job_id": job.id, "note": "Dùng job_log để theo dõi"}


@tool("deploy_app", "Deploy lại app (pull image + docker compose up -d).", {"slug": {"type": "string"}}, ["slug"], write=True)
async def deploy_app(ctx: ToolContext, slug: str):
    async with SessionLocal() as db:
        app = await _app_by_slug(ctx, db, slug, "deployer")
        job = await enqueue(db, "deploy", {"app_id": app.id}, app.id, ctx.user.id)
        await audit(db, ctx.user, "app.deploy", app.slug, {"via": f"claude-{ctx.source}"})
    return {"job_id": job.id}


@tool("container_action", "Start / stop / restart một container.",
      {"name": {"type": "string"}, "action": {"type": "string", "enum": ["start", "stop", "restart"]}}, ["name", "action"], write=True)
async def container_action(ctx: ToolContext, name: str, action: str):
    async with SessionLocal() as db:
        c = await _authorize_container(ctx, db, name, "deployer")
        if c.name in ("th-api", "th-db", "th-worker"):
            raise ValueError("Không thao tác container lõi của panel")
        await docker_svc.run(getattr(c, action))
        await audit(db, ctx.user, f"container.{action}", c.name, {"via": f"claude-{ctx.source}"})
    return "ok"


@tool("exec_command", "Chạy một lệnh không tương tác trong container (chẩn đoán: df, env, curl health, ls…). Trả về stdout+stderr, timeout 60s.",
      {"name": {"type": "string"}, "command": {"type": "string"}}, ["name", "command"], write=True)
async def exec_command(ctx: ToolContext, name: str, command: str):
    async with SessionLocal() as db:
        c = await _authorize_container(ctx, db, name, "deployer")
        await audit(db, ctx.user, "container.exec", c.name, {"command": command[:500], "via": f"claude-{ctx.source}"})
    res = await docker_svc.run(c.exec_run, ["sh", "-c", f"timeout 60 sh -c {shlex.quote(command)} 2>&1"], demux=False)
    return {"exit_code": res.exit_code, "output": (res.output or b"").decode("utf-8", "replace")[-MAX_OUT:]}


@tool("run_backup", "Chạy backup ngay cho một app.", {"slug": {"type": "string"}}, ["slug"], write=True)
async def run_backup(ctx: ToolContext, slug: str):
    async with SessionLocal() as db:
        app = await _app_by_slug(ctx, db, slug, "deployer")
        job = await enqueue(db, "backup", {"app_id": app.id, "trigger": "manual"}, app.id, ctx.user.id)
    return {"job_id": job.id}


@tool("run_security_scan", "Chạy quét bảo mật: config (nhanh) hoặc images (Trivy, chậm).",
      {"kind": {"type": "string", "enum": ["config", "images"]}}, ["kind"], write=True, staff_only=True)
async def run_security_scan(ctx: ToolContext, kind: str):
    async with SessionLocal() as db:
        job = await enqueue(db, "scan_config" if kind == "config" else "scan_images", {}, None, ctx.user.id)
    return {"job_id": job.id}


@tool("set_service_limits", "Đặt giới hạn CPU (core) / RAM (MB) cho một service của app. Áp dụng ngay bằng docker update và lưu vào override.",
      {"slug": {"type": "string"}, "service": {"type": "string"}, "cpus": {"type": "number"}, "memory_mb": {"type": "integer"}},
      ["slug", "service"], write=True, staff_only=True)
async def set_service_limits(ctx: ToolContext, slug: str, service: str, cpus: float | None = None, memory_mb: int | None = None):
    async with SessionLocal() as db:
        app = await _app_by_slug(ctx, db, slug, "owner")
        limits = dict(app.service_limits or {})
        limits[service] = {k: v for k, v in {"cpus": cpus, "memory_mb": memory_mb}.items() if v}
        app.service_limits = limits
        await db.commit()
        job = await enqueue(db, "apply_limits", {"app_id": app.id}, app.id, ctx.user.id)
        await audit(db, ctx.user, "app.limits", app.slug, {"service": service, "cpus": cpus, "memory_mb": memory_mb, "via": f"claude-{ctx.source}"})
    return {"job_id": job.id}


@tool("set_route_state", "Bật/tắt một route proxy hoặc bật chế độ bảo trì (trả 503).",
      {"route_id": {"type": "integer"}, "enabled": {"type": "boolean"}, "maintenance": {"type": "boolean"}}, ["route_id"], write=True)
async def set_route_state(ctx: ToolContext, route_id: int, enabled: bool | None = None, maintenance: bool | None = None):
    from ..routers.proxy import apply_all

    async with SessionLocal() as db:
        r = await db.get(ProxyRoute, route_id)
        if r is None:
            raise ValueError("Không có route")
        if r.app_id:
            await check_app(ctx.user, r.app_id, db, "deployer")
        elif not is_staff(ctx.user):
            raise ValueError("Không có quyền")
        if enabled is not None:
            r.enabled = enabled
        if maintenance is not None:
            r.options = {**(r.options or {}), "maintenance": maintenance}
        await db.commit()
        await audit(db, ctx.user, "proxy.update", ",".join(r.hosts), {"enabled": enabled, "maintenance": maintenance, "via": f"claude-{ctx.source}"})
        return await apply_all(db)


@tool("create_proxy_route", "Tạo route proxy domain → upstream (container:port). Mặc định đi qua Cloudflare Tunnel.",
      {"hosts": {"type": "array", "items": {"type": "string"}}, "upstream": {"type": "string"}, "app_slug": {"type": "string"},
       "path_prefix": {"type": "string"}}, ["hosts", "upstream"], write=True)
async def create_proxy_route(ctx: ToolContext, hosts: list[str], upstream: str, app_slug: str | None = None, path_prefix: str = ""):
    from ..routers.proxy import RouteIn, _attach_upstreams, _conflicts, _validate, apply_all

    async with SessionLocal() as db:
        app_id = None
        if app_slug:
            app_id = (await _app_by_slug(ctx, db, app_slug, "deployer")).id
        elif not is_staff(ctx.user):
            raise ValueError("Cần app_slug")
        body = RouteIn(app_id=app_id, hosts=hosts, upstreams=[upstream], path_prefix=path_prefix)
        _validate(body, None)
        await _conflicts(db, body, None)
        await _attach_upstreams(body)
        r = ProxyRoute(**body.model_dump())
        db.add(r)
        await db.commit()
        await audit(db, ctx.user, "proxy.create", ",".join(hosts), {"via": f"claude-{ctx.source}"})
        return {"route_id": r.id, "apply": await apply_all(db)}


# ======================================================================= dispatch
def available_tools(user: User, include_write: bool = True) -> list[Tool]:
    return [t for t in TOOLS.values() if (include_write or not t.write) and (not t.staff_only or is_staff(user))]


def validate_input(t: Tool, args: Any) -> dict[str, Any]:
    import jsonschema

    if not isinstance(args, dict):
        raise ValueError("Input phải là object")
    jsonschema.validate(args, t.schema)
    return args


async def call_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    t = TOOLS.get(name)
    if t is None:
        raise ValueError(f"Tool không tồn tại: {name}")
    if t.staff_only and not is_staff(ctx.user):
        raise ValueError("Tool này cần quyền admin/operator")
    args = validate_input(t, args)
    try:
        result = await t.fn(ctx, **args)
    except HTTPException as e:
        raise ValueError(str(e.detail)) from e
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_OUT:
        text = text[:MAX_OUT] + "\n…(đã cắt bớt)"
    return text
