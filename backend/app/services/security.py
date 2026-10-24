"""Quét bảo mật: lỗ hổng image (Trivy) + kiểm tra cấu hình container/host."""

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ..core.jobs import JobContext, job_handler
from ..db import SessionLocal
from ..models import App, ProxyRoute, ScanResult
from . import docker_svc
from .notify import notify

SENSITIVE_PORTS = {
    5432: "PostgreSQL", 3306: "MySQL", 6379: "Redis", 27017: "MongoDB", 9200: "Elasticsearch",
    11211: "Memcached", 2375: "Docker API", 2379: "etcd", 5672: "RabbitMQ", 9000: "MinIO/S3 API",
}
SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
SECRET_ENV = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)", re.I)


def _finding(sev: str, rule: str, target: str, title: str, fix: str) -> dict[str, Any]:
    return {"severity": sev, "rule": rule, "target": target, "title": title, "fix": fix}


def audit_containers() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in docker_svc.client().containers.list(all=True):
        a = c.attrs
        hc = a["HostConfig"]
        name = c.name
        if hc.get("Privileged"):
            out.append(_finding("HIGH", "privileged", name, "Container chạy privileged", "Bỏ privileged, chỉ cấp cap_add cần thiết"))
        caps = set(hc.get("CapAdd") or [])
        if caps & {"ALL", "SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE"}:
            out.append(_finding("MEDIUM", "capabilities", name, f"Cấp capability nguy hiểm: {', '.join(sorted(caps))}", "Thu hẹp cap_add"))
        for m in a.get("Mounts", []):
            if m.get("Source") == "/var/run/docker.sock":
                sev = "MEDIUM" if (a["Config"].get("Labels") or {}).get("th.system") or name.startswith("th-") else "HIGH"
                out.append(_finding(sev, "docker-sock", name, "Mount docker.sock (toàn quyền host)", "Chỉ cho công cụ quản trị tin cậy; dùng docker-socket-proxy nếu có thể"))
            if m.get("Type") == "bind" and m.get("Source") in ("/", "/etc", "/root") and m.get("RW"):
                out.append(_finding("HIGH", "host-mount", name, f"Mount ghi thư mục nhạy cảm {m['Source']}", "Mount read-only hoặc thu hẹp đường dẫn"))
        if hc.get("NetworkMode") == "host":
            out.append(_finding("MEDIUM", "host-network", name, "Dùng network_mode: host", "Dùng mạng bridge và publish cổng cần thiết"))
        if hc.get("PidMode") == "host":
            out.append(_finding("HIGH", "host-pid", name, "Dùng pid: host", "Bỏ pid: host"))
        reported_ports: set[str] = set()
        for cport, binds in (a["NetworkSettings"].get("Ports") or {}).items():
            num = int(cport.split("/")[0])
            for b in binds or []:
                if b.get("HostPort") in reported_ports:
                    continue  # IPv4 + IPv6 cùng cổng → 1 phát hiện
                if b.get("HostIp") in ("0.0.0.0", "::", "") and num in SENSITIVE_PORTS:
                    reported_ports.add(b.get("HostPort"))
                    out.append(
                        _finding("HIGH", "exposed-db", name, f"{SENSITIVE_PORTS[num]} mở ra mọi interface ({b.get('HostIp') or '*'}:{b.get('HostPort')})",
                                 "Bind 127.0.0.1 hoặc bỏ publish; app kết nối qua mạng docker / dịch vụ nền tảng")
                    )
        if not a["Config"].get("User") and c.status == "running":
            out.append(_finding("LOW", "root-user", name, "Tiến trình chạy bằng root", "Đặt user: trong compose / USER trong Dockerfile"))
        if not hc.get("Memory"):
            out.append(_finding("LOW", "no-mem-limit", name, "Không giới hạn RAM", "Đặt giới hạn trong tab Tài nguyên của app"))
        if (hc.get("RestartPolicy") or {}).get("Name") in ("", "no") and c.status == "running":
            out.append(_finding("LOW", "no-restart", name, "Không có restart policy", "Đặt restart: unless-stopped"))
        image = a["Config"].get("Image") or ""
        if image.endswith(":latest") or (":" not in image.split("/")[-1] and not image.startswith("sha256:")):
            out.append(_finding("LOW", "latest-tag", name, f"Image dùng tag latest/không ghim ({image})", "Ghim phiên bản cụ thể để deploy tái lập được"))
        plain = [e.split("=", 1)[0] for e in a["Config"].get("Env") or [] if SECRET_ENV.search(e.split("=", 1)[0]) and "=" in e and e.split("=", 1)[1]]
        if plain:
            out.append(_finding("INFO", "env-secrets", name, f"Secret truyền qua biến môi trường: {', '.join(plain[:6])}", "Cân nhắc docker secrets / file mount; đảm bảo .env có quyền 600"))
        if c.status == "restarting" or a.get("RestartCount", 0) > 5:
            out.append(_finding("MEDIUM", "restart-loop", name, f"Container restart nhiều lần ({a.get('RestartCount', 0)})", "Xem log để tìm nguyên nhân"))
    return out


async def audit_proxy() -> list[dict[str, Any]]:
    out = []
    async with SessionLocal() as s:
        for r in (await s.execute(select(ProxyRoute).where(ProxyRoute.enabled.is_(True)))).scalars():
            o = r.options or {}
            admin_like = any(k in " ".join(r.hosts) for k in ("admin", "pgadmin", "portainer", "dockhand", "minio", "kuma"))
            if admin_like and not (o.get("basic_auth") or o.get("ip_allow")):
                out.append(_finding("MEDIUM", "admin-public", ", ".join(r.hosts), "Trang quản trị public không có lớp bảo vệ", "Bật Basic Auth / IP allowlist hoặc Cloudflare Access"))
    return out


def summarize(findings: list[dict[str, Any]]) -> dict[str, int]:
    s: dict[str, int] = {}
    for f in findings:
        s[f["severity"]] = s.get(f["severity"], 0) + 1
    return s


async def trivy_image(image: str, ctx: JobContext | None = None) -> dict[str, Any]:
    cmd = ["trivy", "image", "--quiet", "--format", "json", "--scanners", "vuln", "--severity", "CRITICAL,HIGH,MEDIUM", "--timeout", "15m", image]
    if ctx:
        await ctx.log(f"trivy {image}")
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode()[-1000:] or f"trivy exit {proc.returncode}")
    data = json.loads(out or b"{}")
    findings = []
    for res in data.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            findings.append(
                {
                    "severity": v.get("Severity"),
                    "rule": v.get("VulnerabilityID"),
                    "target": f"{res.get('Target')} › {v.get('PkgName')} {v.get('InstalledVersion')}",
                    "title": (v.get("Title") or "")[:200],
                    "fix": f"Nâng lên {v['FixedVersion']}" if v.get("FixedVersion") else "Chưa có bản vá",
                }
            )
    findings.sort(key=lambda f: SEV_ORDER.index(f["severity"]) if f["severity"] in SEV_ORDER else 9)
    return {"findings": findings, "summary": summarize(findings)}


@job_handler("scan_config")
async def job_scan_config(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    findings = await docker_svc.run(audit_containers) + await audit_proxy()
    summary = summarize(findings)
    async with SessionLocal() as s:
        s.add(ScanResult(kind="config", target="host", summary=summary, findings=findings))
        await s.commit()
    await ctx.log(f"Kiểm tra cấu hình: {summary}")
    if summary.get("HIGH"):
        await notify("warning", f"Kiểm tra cấu hình: {summary.get('HIGH')} vấn đề HIGH", "Xem trang Bảo mật", key=f"scan-config:{datetime.now(UTC).date()}")
    return summary


@job_handler("scan_images")
async def job_scan_images(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    images: list[str] = params.get("images") or []
    app_map: dict[str, int] = {}
    async with SessionLocal() as s:
        apps = {a.compose_project: a.id for a in (await s.execute(select(App))).scalars()}
    if not images:
        seen = set()
        for c in await docker_svc.run(docker_svc.client().containers.list):
            img = c.attrs["Config"].get("Image")
            if img and img not in seen:
                seen.add(img)
                images.append(img)
                p = (c.labels or {}).get(docker_svc.COMPOSE_PROJECT)
                if p in apps:
                    app_map[img] = apps[p]
    total: dict[str, int] = {}
    for img in images:
        try:
            r = await trivy_image(img, ctx)
        except Exception as e:  # noqa: BLE001
            await ctx.log(f"  ⚠ {img}: {e}")
            continue
        async with SessionLocal() as s:
            s.add(ScanResult(kind="image", target=img, app_id=app_map.get(img), summary=r["summary"], findings=r["findings"][:500]))
            await s.commit()
        await ctx.log(f"  {img}: {r['summary']}")
        for k, v in r["summary"].items():
            total[k] = total.get(k, 0) + v
    if total.get("CRITICAL"):
        await notify("critical", f"Phát hiện {total['CRITICAL']} lỗ hổng CRITICAL trong image", "Xem trang Bảo mật để biết image cần cập nhật", key=f"scan-img:{datetime.now(UTC).date()}")
    return total
