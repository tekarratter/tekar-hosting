"""Cloudflare: quản lý Tunnel (remote-managed), DNS record, wildcard.

API token cần quyền: Account › Cloudflare Tunnel: Edit, Zone › DNS: Edit, Zone › Zone: Read.
"""

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.store import get_setting, set_setting
from ..models import ProxyRoute
from . import docker_svc

log = logging.getLogger("th.cf")
API = "https://api.cloudflare.com/client/v4"


class CFError(RuntimeError):
    pass


class CF:
    def __init__(self, token: str, account_id: str = "", zone_id: str = ""):
        if not token:
            raise CFError("Chưa cấu hình Cloudflare API token")
        self.account_id, self.zone_id = account_id, zone_id
        self.http = httpx.AsyncClient(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=30)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        await self.http.aclose()

    async def req(self, method: str, path: str, **kw) -> Any:
        r = await self.http.request(method, path, **kw)
        try:
            data = r.json()
        except ValueError as e:
            raise CFError(f"Cloudflare trả về {r.status_code}") from e
        if not data.get("success", False):
            errs = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in data.get("errors", []))
            raise CFError(errs or f"HTTP {r.status_code}")
        return data.get("result")

    # ---- account / zone
    async def verify(self) -> dict[str, Any]:
        return await self.req("GET", "/user/tokens/verify")

    async def accounts(self) -> list[dict[str, Any]]:
        return await self.req("GET", "/accounts", params={"per_page": 50})

    async def zones(self) -> list[dict[str, Any]]:
        return await self.req("GET", "/zones", params={"per_page": 50})

    # ---- tunnel
    async def tunnels(self) -> list[dict[str, Any]]:
        return await self.req("GET", f"/accounts/{self.account_id}/cfd_tunnel", params={"is_deleted": "false", "per_page": 100})

    async def create_tunnel(self, name: str) -> dict[str, Any]:
        return await self.req("POST", f"/accounts/{self.account_id}/cfd_tunnel", json={"name": name, "config_src": "cloudflare"})

    async def tunnel_token(self, tunnel_id: str) -> str:
        return await self.req("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/token")

    async def tunnel(self, tunnel_id: str) -> dict[str, Any]:
        return await self.req("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}")

    async def get_tunnel_config(self, tunnel_id: str) -> dict[str, Any]:
        return await self.req("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations")

    async def put_tunnel_config(self, tunnel_id: str, ingress: list[dict[str, Any]]) -> Any:
        return await self.req(
            "PUT", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations", json={"config": {"ingress": ingress}}
        )

    # ---- dns
    async def dns_records(self, name: str | None = None, type_: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": 500}
        if name:
            params["name"] = name
        if type_:
            params["type"] = type_
        return await self.req("GET", f"/zones/{self.zone_id}/dns_records", params=params)

    async def upsert_cname(self, name: str, target: str, proxied: bool = True, comment: str = "tekar-hosting") -> dict[str, Any]:
        existing = await self.dns_records(name=name)
        body = {"type": "CNAME", "name": name, "content": target, "proxied": proxied, "ttl": 1, "comment": comment}
        for rec in existing:
            if rec["type"] == "CNAME":
                return await self.req("PUT", f"/zones/{self.zone_id}/dns_records/{rec['id']}", json=body)
            if rec["type"] in ("A", "AAAA"):
                raise CFError(f"{name} đã có record {rec['type']} → {rec['content']}; xoá trước nếu muốn chuyển sang tunnel")
        return await self.req("POST", f"/zones/{self.zone_id}/dns_records", json=body)

    async def delete_record(self, record_id: str) -> Any:
        return await self.req("DELETE", f"/zones/{self.zone_id}/dns_records/{record_id}")


async def cf_from_settings(db: AsyncSession | None = None) -> CF:
    return CF(
        await get_setting("cloudflare_api_token_secret", db),
        await get_setting("cloudflare_account_id", db) or "",
        await get_setting("cloudflare_zone_id", db) or "",
    )


def caddy_origin() -> str:
    return f"http://{settings.caddy_container}:80"


async def desired_ingress(db: AsyncSession) -> list[dict[str, Any]]:
    """Ingress cho tunnel: mọi host của route có cf_tunnel → Caddy; thêm wildcard nếu bật."""
    routes = (
        await db.execute(select(ProxyRoute).where(ProxyRoute.enabled.is_(True), ProxyRoute.cf_tunnel.is_(True)))
    ).scalars().all()
    base = (await get_setting("base_domain", db) or "").strip().lower()
    wildcard = bool(await get_setting("wildcard_enabled", db)) and base
    hosts: list[str] = []
    for r in routes:
        for h in r.hosts:
            if h not in hosts:
                hosts.append(h)
    ingress = []
    for h in sorted(hosts, key=lambda x: (x.startswith("*"), x)):
        if wildcard and h.endswith("." + base) and h.count(".") == base.count(".") + 1:
            continue  # đã được wildcard phủ
        ingress.append({"hostname": h, "service": caddy_origin()})
    if wildcard:
        ingress.append({"hostname": f"*.{base}", "service": caddy_origin()})
        ingress.append({"hostname": base, "service": caddy_origin()})
    ingress.append({"service": "http_status:404"})
    return ingress


async def sync_tunnel(db: AsyncSession, ensure_dns: bool = True) -> dict[str, Any]:
    tunnel_id = await get_setting("cloudflare_tunnel_id", db)
    if not tunnel_id:
        raise CFError("Chưa tạo/kết nối tunnel")
    ingress = await desired_ingress(db)
    target = f"{tunnel_id}.cfargotunnel.com"
    dns_done, dns_errors = [], []
    async with await cf_from_settings(db) as cf:
        await cf.put_tunnel_config(tunnel_id, ingress)
        if ensure_dns and cf.zone_id:
            zone = await cf.req("GET", f"/zones/{cf.zone_id}")
            zone_name = zone["name"]
            for rule in ingress:
                h = rule.get("hostname")
                if not h or not (h == zone_name or h.endswith("." + zone_name)):
                    continue
                try:
                    await cf.upsert_cname(h, target)
                    dns_done.append(h)
                except CFError as e:
                    dns_errors.append(f"{h}: {e}")
    return {"ingress": ingress, "dns": dns_done, "dns_errors": dns_errors}


async def setup_tunnel(db: AsyncSession, name: str) -> dict[str, Any]:
    async with await cf_from_settings(db) as cf:
        existing = [t for t in await cf.tunnels() if t["name"] == name]
        tunnel = existing[0] if existing else await cf.create_tunnel(name)
        token = await cf.tunnel_token(tunnel["id"])
    await set_setting("cloudflare_tunnel_id", tunnel["id"], db)
    await set_setting("cloudflare_tunnel_name", name, db)
    await set_setting("cloudflare_tunnel_token_secret", token, db)
    await db.commit()
    await run_connector(token)
    return {"id": tunnel["id"], "name": name}


async def run_connector(token: str) -> None:
    """(Re)tạo container cloudflared chạy tunnel bằng token, gắn mạng edge."""
    name = settings.cloudflared_container

    def _run():
        c = docker_svc.client()
        try:
            c.containers.get(name).remove(force=True)
        except Exception:  # noqa: BLE001
            pass
        image = "cloudflare/cloudflared:2026.7.3"
        try:
            c.images.get(image)
        except Exception:  # noqa: BLE001
            c.images.pull(image)
        c.containers.run(
            image,
            ["tunnel", "--no-autoupdate", "--metrics", "0.0.0.0:2000", "run"],
            name=name,
            detach=True,
            environment={"TUNNEL_TOKEN": token},
            network=settings.edge_network,
            restart_policy={"Name": "unless-stopped"},
            labels={"th.system": "cloudflared"},
        )

    await docker_svc.run(_run)
    # container mới có IP mới → đẩy lại cấu hình Caddy để trusted_proxies trỏ đúng cloudflared
    from ..db import SessionLocal
    from .proxy import apply

    try:
        async with SessionLocal() as s:
            await apply(s)
    except Exception:  # noqa: BLE001
        log.exception("apply caddy sau khi tạo cloudflared")


async def tunnel_status(db: AsyncSession) -> dict[str, Any]:
    tunnel_id = await get_setting("cloudflare_tunnel_id", db)
    out: dict[str, Any] = {"tunnel_id": tunnel_id, "connector": None}
    c = await docker_svc.get_container(settings.cloudflared_container)
    if c:
        out["connector"] = {"state": c.status, "started_at": c.attrs["State"].get("StartedAt")}
    if tunnel_id:
        try:
            async with await cf_from_settings(db) as cf:
                t = await cf.tunnel(tunnel_id)
                out["status"] = t.get("status")
                out["connections"] = len(t.get("connections") or [])
                out["name"] = t.get("name")
                cfg = await cf.get_tunnel_config(tunnel_id)
                out["ingress"] = ((cfg or {}).get("config") or {}).get("ingress", [])
        except CFError as e:
            out["error"] = str(e)
    return out
