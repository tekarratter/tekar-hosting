from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.audit import audit
from ..core.deps import DB, AdminUser, StaffUser
from ..core.store import get_setting, set_setting
from ..services import cloudflare as cfs
from ..services.cloudflare import CFError

router = APIRouter(prefix="/api/cloudflare", tags=["cloudflare"])


def _err(e: Exception) -> HTTPException:
    return HTTPException(400, f"Cloudflare: {e}")


class TokenIn(BaseModel):
    api_token: str


@router.post("/verify")
async def verify(body: TokenIn, user: AdminUser):
    try:
        async with cfs.CF(body.api_token) as cf:
            await cf.verify()
            return {"accounts": await cf.accounts(), "zones": [{"id": z["id"], "name": z["name"], "account": z["account"]} for z in await cf.zones()]}
    except CFError as e:
        raise _err(e) from e


class ConfigIn(BaseModel):
    api_token: str | None = None
    account_id: str
    zone_id: str
    base_domain: str
    wildcard_enabled: bool = False
    acme_email: str = ""


@router.post("/config")
async def save_config(body: ConfigIn, user: AdminUser, db: DB, request: Request):
    if body.api_token:
        await set_setting("cloudflare_api_token_secret", body.api_token, db)
    await set_setting("cloudflare_account_id", body.account_id, db)
    await set_setting("cloudflare_zone_id", body.zone_id, db)
    await set_setting("base_domain", body.base_domain.strip().lower(), db)
    await set_setting("wildcard_enabled", body.wildcard_enabled, db)
    await set_setting("acme_email", body.acme_email, db)
    try:
        async with await cfs.cf_from_settings(db) as cf:
            zone = await cf.req("GET", f"/zones/{body.zone_id}")
        await set_setting("cloudflare_zone_name", zone["name"], db)
    except CFError:
        pass
    await db.commit()
    await audit(db, user, "cloudflare.config", body.base_domain, {"wildcard": body.wildcard_enabled}, request)
    from .proxy import apply_all

    return {"ok": True, "apply": await apply_all(db)}


class TunnelIn(BaseModel):
    name: str = "tekar-hosting"


@router.post("/tunnel")
async def setup_tunnel(body: TunnelIn, user: AdminUser, db: DB, request: Request):
    try:
        t = await cfs.setup_tunnel(db, body.name)
    except CFError as e:
        raise _err(e) from e
    await audit(db, user, "cloudflare.tunnel_setup", body.name, t, request)
    from .proxy import apply_all

    return {**t, "apply": await apply_all(db)}


@router.post("/tunnel/restart")
async def restart_connector(user: StaffUser, db: DB):
    token = await get_setting("cloudflare_tunnel_token_secret", db)
    if not token:
        raise HTTPException(400, "Chưa có tunnel")
    await cfs.run_connector(token)
    return {"ok": True}


@router.post("/tunnel/sync")
async def sync(user: StaffUser, db: DB):
    try:
        return await cfs.sync_tunnel(db)
    except CFError as e:
        raise _err(e) from e


@router.get("/status")
async def status(user: StaffUser, db: DB):
    cfg = {
        "account_id": await get_setting("cloudflare_account_id", db),
        "zone_id": await get_setting("cloudflare_zone_id", db),
        "base_domain": await get_setting("base_domain", db),
        "wildcard_enabled": await get_setting("wildcard_enabled", db),
        "acme_email": await get_setting("acme_email", db),
        "has_token": bool(await get_setting("cloudflare_api_token_secret", db)),
    }
    tunnel = await cfs.tunnel_status(db) if cfg["has_token"] else {}
    return {"config": cfg, "tunnel": tunnel}


@router.get("/dns")
async def dns(user: StaffUser, db: DB):
    try:
        async with await cfs.cf_from_settings(db) as cf:
            recs = await cf.dns_records()
    except CFError as e:
        raise _err(e) from e
    return [{"id": r["id"], "type": r["type"], "name": r["name"], "content": r["content"], "proxied": r.get("proxied"), "comment": r.get("comment")} for r in recs]


class DnsIn(BaseModel):
    name: str


@router.post("/dns/tunnel")
async def dns_to_tunnel(body: DnsIn, user: StaffUser, db: DB, request: Request):
    tid = await get_setting("cloudflare_tunnel_id", db)
    if not tid:
        raise HTTPException(400, "Chưa có tunnel")
    try:
        async with await cfs.cf_from_settings(db) as cf:
            r = await cf.upsert_cname(body.name, f"{tid}.cfargotunnel.com")
    except CFError as e:
        raise _err(e) from e
    await audit(db, user, "cloudflare.dns", body.name, request=request)
    return r


@router.delete("/dns/{record_id}")
async def dns_delete(record_id: str, user: AdminUser, db: DB, request: Request):
    try:
        async with await cfs.cf_from_settings(db) as cf:
            await cf.delete_record(record_id)
    except CFError as e:
        raise _err(e) from e
    await audit(db, user, "cloudflare.dns_delete", record_id, request=request)
    return {"ok": True}
