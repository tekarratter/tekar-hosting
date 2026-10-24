import asyncio
import ipaddress
import re
from datetime import UTC
from typing import Any

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.audit import audit
from ..core.deps import DB, AdminUser, CurrentUser, StaffUser, check_app, is_staff, visible_app_ids
from ..core.jobs import enqueue
from ..core.security import encrypt
from ..core.store import get_setting, set_setting
from ..models import AccessList, App, Certificate, ProxyRoute, Stream, User
from ..services import docker_svc, proxy
from ..services.cloudflare import CFError, sync_tunnel

router = APIRouter(prefix="/api/proxy", tags=["proxy"])
HOST_RE = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
KINDS = ("proxy", "redirect", "dead")
# tuỳ chọn cho phép chèn cấu hình Caddy tuỳ ý / sửa header gửi lên upstream → chỉ admin/operator
STAFF_OPTIONS = ("custom_caddyfile", "headers_up", "custom_handlers")
# chuỗi người dùng nhập được Caddy nội suy placeholder ({file.…}, {env.…}) → escape với route của deployer
PLACEHOLDER_FIELDS = ("body", "maintenance_html", "redirect_to", "cors_origins", "host_header")


def _esc(v: Any) -> Any:
    return v.replace("{", "\\{") if isinstance(v, str) else v


def _hosts_overlap(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.startswith("*.") and (b == a[2:] or b.endswith(a[1:])):
        return True
    return b.startswith("*.") and (a == b[2:] or a.endswith(b[1:]))


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def route_out(r: ProxyRoute) -> dict[str, Any]:
    o = dict(r.options or {})
    if o.get("basic_auth"):
        o["basic_auth"] = [{"username": a["username"], "password_hash": "***"} for a in o["basic_auth"]]
    return {
        "id": r.id, "app_id": r.app_id, "kind": r.kind or "proxy", "hosts": r.hosts, "path_prefix": r.path_prefix,
        "upstreams": r.upstreams, "tls_mode": r.tls_mode, "cf_tunnel": r.cf_tunnel, "enabled": r.enabled, "priority": r.priority,
        "access_list_id": r.access_list_id, "certificate_id": r.certificate_id, "options": o,
        "health_path": r.health_path, "health_status": r.health_status, "health_checked_at": r.health_checked_at,
        "note": r.note, "source": r.source, "created_at": r.created_at,
    }


class RouteIn(BaseModel):
    app_id: int | None = None
    kind: str = "proxy"
    hosts: list[str]
    path_prefix: str = ""
    upstreams: list[str] = Field(default_factory=list)
    tls_mode: str = "tunnel"
    cf_tunnel: bool = True
    enabled: bool = True
    priority: int = 0
    access_list_id: int | None = None
    certificate_id: int | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    health_path: str | None = None
    note: str = ""


async def _can_edit(user: User, app_id: int | None, db: AsyncSession) -> None:
    if app_id is None:
        if not is_staff(user):
            raise HTTPException(403, "Route không gắn app chỉ admin/operator tạo được")
    else:
        await check_app(user, app_id, db, "deployer")


def _restrict_options(o: dict[str, Any], staff: bool) -> None:
    o.pop("custom_handlers", None)
    if staff:
        return
    for k in STAFF_OPTIONS:
        if o.get(k):
            raise HTTPException(403, f"Tuỳ chọn '{k}' chỉ admin/operator dùng được")
    for pr in o.get("path_routes") or []:
        if (pr.get("custom_caddyfile") or "").strip() or (pr.get("options") or {}).get("headers_up"):
            raise HTTPException(403, "Caddyfile nâng cao / header_up trong custom location chỉ admin/operator dùng được")
    for k in PLACEHOLDER_FIELDS:
        if k in o:
            o[k] = _esc(o[k])
    if isinstance(o.get("headers_down"), dict):
        o["headers_down"] = {str(k)[:100]: _esc(v) for k, v in o["headers_down"].items()}


def _validate(body: RouteIn, existing: ProxyRoute | None, staff: bool = True) -> None:
    _restrict_options(body.options, staff)
    body.hosts = [h.strip().lower() for h in body.hosts if h.strip()]
    if not body.hosts:
        raise HTTPException(400, "Cần ít nhất 1 domain")
    for h in body.hosts:
        if not HOST_RE.match(h):
            raise HTTPException(400, f"Domain không hợp lệ: {h}")
    if body.kind not in KINDS:
        raise HTTPException(400, "kind phải là proxy | redirect | dead")
    if body.tls_mode not in ("tunnel", "auto", "internal", "off"):
        raise HTTPException(400, "tls_mode không hợp lệ")
    if body.path_prefix and not body.path_prefix.startswith("/"):
        raise HTTPException(400, "path_prefix phải bắt đầu bằng /")
    o = body.options
    if body.kind == "redirect" and not o.get("redirect_to"):
        raise HTTPException(400, "Redirect cần địa chỉ đích")
    if body.kind == "proxy" and not body.upstreams and not o.get("redirect_to") and not o.get("maintenance"):
        raise HTTPException(400, "Cần upstream")
    if body.kind != "proxy":
        body.upstreams = []
    for u in body.upstreams + [u for pr in o.get("path_routes") or [] for u in pr.get("upstreams", [])]:
        try:
            proxy.parse_upstream(u)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    for pr in o.get("path_routes") or []:
        if not str(pr.get("path", "")).startswith("/") or not pr.get("upstreams"):
            raise HTTPException(400, "Custom location cần path bắt đầu bằng / và ít nhất 1 upstream")
    for cidr in o.get("ip_allow") or []:
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError as e:
            raise HTTPException(400, f"CIDR không hợp lệ: {cidr}") from e
    if o.get("basic_auth"):
        old = {a["username"]: a["password_hash"] for a in ((existing.options or {}).get("basic_auth") or [])} if existing else {}
        accounts = []
        for a in o["basic_auth"]:
            if a.get("password"):
                accounts.append({"username": a["username"], "password_hash": _hash(a["password"])})
            elif a["username"] in old:
                accounts.append({"username": a["username"], "password_hash": old[a["username"]]})
        o["basic_auth"] = accounts


async def _validate_caddyfile(body: RouteIn) -> None:
    snippets = [body.options.get("custom_caddyfile")] + [pr.get("custom_caddyfile") for pr in body.options.get("path_routes") or []]
    for snip in snippets:
        if snip and snip.strip():
            try:
                await proxy.adapt_caddyfile(snip)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e


async def _conflicts(db: AsyncSession, body: RouteIn, route_id: int | None, user: User | None = None) -> None:
    staff = user is None or is_staff(user)
    for r in (await db.execute(select(ProxyRoute))).scalars():
        if r.id == route_id:
            continue
        if set(r.hosts) & set(body.hosts) and (r.path_prefix or "") == (body.path_prefix or ""):
            raise HTTPException(400, f"Trùng với route #{r.id} ({', '.join(r.hosts)}{r.path_prefix})")
        # deployer không được chen path / wildcard vào domain của app khác (hoặc route hệ thống)
        if not staff and r.app_id != body.app_id and any(_hosts_overlap(h, x) for h in body.hosts for x in r.hosts):
            raise HTTPException(403, f"Domain đang được route #{r.id} của app khác sử dụng")


async def _attach_upstreams(body: RouteIn, user: User | None = None, db: AsyncSession | None = None) -> list[str]:
    """Upstream là tên container chưa ở mạng edge → tự gắn vào. Deployer chỉ được trỏ tới container của app mình."""
    staff = user is None or is_staff(user)
    allowed: set[str] | None = None
    if not staff and db is not None:
        ids = await visible_app_ids(user, db) or set()
        allowed = set((await db.execute(select(App.compose_project).where(App.id.in_(ids or [-1])))).scalars())
    attached = []
    targets = body.upstreams + [u for pr in body.options.get("path_routes") or [] for u in pr.get("upstreams", [])]
    for u in targets:
        dial, _ = proxy.parse_upstream(u)
        host = dial.rsplit(":", 1)[0]
        if host == "host.docker.internal" or re.match(r"^[\d.]+$", host) or ":" in host or "." in host:
            if not staff:
                raise HTTPException(403, "Chỉ admin/operator mới trỏ route tới IP/host ngoài; hãy chọn container của app")
            continue
        c = await docker_svc.get_container(host)
        if c is None:
            raise HTTPException(400, f"Không thấy container '{host}'")
        if allowed is not None and (c.labels or {}).get(docker_svc.COMPOSE_PROJECT) not in allowed:
            raise HTTPException(403, f"Container '{host}' không thuộc app của bạn")
        if await docker_svc.connect_network(c.name, settings.edge_network):
            attached.append(c.name)
    return attached


async def _apply_or_revert(db: AsyncSession, revert) -> dict[str, Any]:
    """Áp dụng cấu hình; Caddy từ chối → hoàn tác thay đổi trong DB và áp lại bản cũ, trả lỗi 400
    (nếu không, một route hỏng sẽ làm mọi lần apply sau thất bại âm thầm)."""
    result = await apply_all(db)
    err = result.get("caddy")
    if isinstance(err, str) and err.startswith("lỗi"):
        await revert()
        await db.commit()
        await apply_all(db)
        raise HTTPException(400, f"Caddy từ chối cấu hình, đã hoàn tác: {err[5:].strip()}")
    return result


async def apply_all(db: AsyncSession) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        await proxy.apply(db)
        result["caddy"] = "ok"
    except Exception as e:  # noqa: BLE001
        result["caddy"] = f"lỗi: {e}"
    if await get_setting("cloudflare_tunnel_id", db):
        try:
            r = await sync_tunnel(db)
            result.update(tunnel="ok", dns=r["dns"], dns_errors=r["dns_errors"])
        except (CFError, Exception) as e:  # noqa: BLE001
            result["tunnel"] = f"lỗi: {e}"
    return result


# ======================================================================= routes
@router.get("/routes")
async def list_routes(user: CurrentUser, db: DB, app_id: int | None = None):
    q = select(ProxyRoute).order_by(ProxyRoute.id)
    if app_id is not None:
        await check_app(user, app_id, db)
        q = q.where(ProxyRoute.app_id == app_id)
    else:
        ids = await visible_app_ids(user, db)
        if ids is not None:
            q = q.where(ProxyRoute.app_id.in_(ids or [-1]))
    rows = (await db.execute(q)).scalars().all()
    apps = {a.id: a.name for a in (await db.execute(select(App))).scalars()}
    return [{**route_out(r), "app_name": apps.get(r.app_id)} for r in rows]


@router.post("/routes")
async def create_route(body: RouteIn, user: CurrentUser, db: DB, request: Request):
    await _can_edit(user, body.app_id, db)
    _validate(body, None, is_staff(user))
    await _validate_caddyfile(body)
    await _conflicts(db, body, None, user)
    attached = await _attach_upstreams(body, user, db)
    r = ProxyRoute(**body.model_dump())
    db.add(r)
    await db.commit()
    await audit(db, user, "proxy.create", ",".join(r.hosts), {"kind": r.kind, "upstreams": r.upstreams}, request)

    async def revert():
        await db.delete(r)

    return {**route_out(r), "attached": attached, "apply": await _apply_or_revert(db, revert)}


@router.put("/routes/{rid}")
async def update_route(rid: int, body: RouteIn, user: CurrentUser, db: DB, request: Request):
    r = await db.get(ProxyRoute, rid)
    if r is None:
        raise HTTPException(404)
    await _can_edit(user, r.app_id, db)
    if body.app_id != r.app_id:
        await _can_edit(user, body.app_id, db)
    _validate(body, r, is_staff(user))
    await _validate_caddyfile(body)
    await _conflicts(db, body, rid, user)
    attached = await _attach_upstreams(body, user, db)
    fields = body.model_dump()
    before = {k: getattr(r, k) for k in fields}
    for k, v in fields.items():
        setattr(r, k, v)
    await db.commit()
    await audit(db, user, "proxy.update", ",".join(r.hosts), request=request)

    async def revert():
        for k, v in before.items():
            setattr(r, k, v)

    return {**route_out(r), "attached": attached, "apply": await _apply_or_revert(db, revert)}


class ToggleIn(BaseModel):
    enabled: bool


@router.post("/routes/{rid}/toggle")
async def toggle_route(rid: int, body: ToggleIn, user: CurrentUser, db: DB, request: Request):
    r = await db.get(ProxyRoute, rid)
    if r is None:
        raise HTTPException(404)
    await _can_edit(user, r.app_id, db)
    r.enabled = body.enabled
    await db.commit()
    await audit(db, user, "proxy.toggle", ",".join(r.hosts), {"enabled": body.enabled}, request)

    async def revert():
        r.enabled = not body.enabled

    return {"ok": True, "apply": await _apply_or_revert(db, revert)}


@router.delete("/routes/{rid}")
async def delete_route(rid: int, user: CurrentUser, db: DB, request: Request):
    r = await db.get(ProxyRoute, rid)
    if r is None:
        raise HTTPException(404)
    await _can_edit(user, r.app_id, db)
    await db.delete(r)
    await db.commit()
    await audit(db, user, "proxy.delete", ",".join(r.hosts), request=request)
    return {"ok": True, "apply": await apply_all(db)}


class AdaptIn(BaseModel):
    caddyfile: str


@router.post("/adapt")
async def adapt(body: AdaptIn, user: StaffUser):
    """Kiểm tra đoạn Caddyfile (cấu hình nâng cao) và xem JSON tương ứng."""
    try:
        return {"ok": True, "routes": await proxy.adapt_caddyfile(body.caddyfile)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.post("/apply")
async def apply_now(user: StaffUser, db: DB):
    return await apply_all(db)


@router.get("/preview")
async def preview(user: StaffUser, db: DB):
    cfg = await proxy.build_config(db)
    for p in cfg.get("apps", {}).get("tls", {}).get("automation", {}).get("policies", []):
        for iss in p.get("issuers", []):
            prov = iss.get("challenges", {}).get("dns", {}).get("provider")
            if prov:
                prov["api_token"] = "***"
    for c in cfg.get("apps", {}).get("tls", {}).get("certificates", {}).get("load_pem", []):
        c["key"] = "***"
    return cfg


@router.get("/status")
async def status(user: CurrentUser, db: DB):
    from ..services.caddy_ports import current_ports, npm_containers

    st = await proxy.caddy_status()
    st.pop("config", None)
    st["certs"] = await proxy.caddy_certs()
    http, https = await current_ports(db)
    st["ports"] = {"http": http, "https": https}
    st["public"] = bool(await get_setting("caddy_public", db))
    st["npm"] = [{"name": c.name, "state": c.status} for c in await npm_containers()] if is_staff(user) else []
    return st


@router.get("/upstream-candidates")
async def candidates(user: CurrentUser, db: DB):
    ids = await visible_app_ids(user, db)
    allowed = None
    if ids is not None:
        allowed = set((await db.execute(select(App.compose_project).where(App.id.in_(ids or [-1])))).scalars())

    def _list():
        out = []
        for c in docker_svc.client().containers.list():
            p = (c.labels or {}).get(docker_svc.COMPOSE_PROJECT)
            if allowed is not None and p not in allowed:
                continue
            out.append({"container": c.name, "project": p, "ports": docker_svc.edge_upstream_ports(c),
                        "on_edge": settings.edge_network in (c.attrs["NetworkSettings"].get("Networks") or {})})
        return sorted(out, key=lambda x: x["container"])

    return await docker_svc.run(_list)


# ======================================================================= access lists
def acl_out(a: AccessList, used: int = 0) -> dict[str, Any]:
    return {"id": a.id, "name": a.name, "satisfy_any": a.satisfy_any, "pass_auth": a.pass_auth,
            "auth": [{"username": u["username"]} for u in a.auth or []], "clients": a.clients or [], "used_by": used,
            "created_at": a.created_at}


class AclIn(BaseModel):
    name: str
    satisfy_any: bool = False
    pass_auth: bool = False
    auth: list[dict[str, str]] = Field(default_factory=list)  # [{"username","password"?}] — bỏ trống password = giữ cũ
    clients: list[dict[str, str]] = Field(default_factory=list)


def _acl_apply(a: AccessList, body: AclIn) -> None:
    old = {u["username"]: u["password_hash"] for u in a.auth or []}
    auth = []
    for u in body.auth:
        if not u.get("username"):
            continue
        if u.get("password"):
            auth.append({"username": u["username"], "password_hash": _hash(u["password"])})
        elif u["username"] in old:
            auth.append({"username": u["username"], "password_hash": old[u["username"]]})
        else:
            raise HTTPException(400, f"Cần mật khẩu cho {u['username']}")
    clients = []
    for c in body.clients:
        addr = (c.get("address") or "").strip()
        if not addr:
            continue
        if addr.lower() != "all":
            try:
                ipaddress.ip_network(addr, strict=False)
            except ValueError as e:
                raise HTTPException(400, f"Địa chỉ không hợp lệ: {addr}") from e
        if c.get("directive") not in ("allow", "deny"):
            raise HTTPException(400, "directive phải là allow | deny")
        clients.append({"address": addr, "directive": c["directive"]})
    a.name, a.satisfy_any, a.pass_auth, a.auth, a.clients = body.name, body.satisfy_any, body.pass_auth, auth, clients


@router.get("/access-lists")
async def list_acls(user: CurrentUser, db: DB):
    rows = (await db.execute(select(AccessList).order_by(AccessList.name))).scalars().all()
    used: dict[int, int] = {}
    for r in (await db.execute(select(ProxyRoute.access_list_id).where(ProxyRoute.access_list_id.is_not(None)))).scalars():
        used[r] = used.get(r, 0) + 1
    return [acl_out(a, used.get(a.id, 0)) for a in rows]


@router.post("/access-lists")
async def create_acl(body: AclIn, user: StaffUser, db: DB, request: Request):
    a = AccessList()
    _acl_apply(a, body)
    db.add(a)
    await db.commit()
    await audit(db, user, "proxy.acl_create", a.name, request=request)
    return acl_out(a)


@router.put("/access-lists/{aid}")
async def update_acl(aid: int, body: AclIn, user: StaffUser, db: DB, request: Request):
    a = await db.get(AccessList, aid)
    if a is None:
        raise HTTPException(404)
    _acl_apply(a, body)
    await db.commit()
    await audit(db, user, "proxy.acl_update", a.name, request=request)
    return {**acl_out(a), "apply": await apply_all(db)}


@router.delete("/access-lists/{aid}")
async def delete_acl(aid: int, user: StaffUser, db: DB, request: Request):
    a = await db.get(AccessList, aid)
    if a is None:
        raise HTTPException(404)
    await db.delete(a)
    await db.commit()
    await audit(db, user, "proxy.acl_delete", a.name, request=request)
    return {"ok": True, "apply": await apply_all(db)}


# ======================================================================= certificates
def _parse_cert(cert_pem: str, key_pem: str) -> dict[str, Any]:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import ExtensionOID, NameOID

    try:
        certs = x509.load_pem_x509_certificates(cert_pem.encode())
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"PEM không hợp lệ: {e}") from e
    leaf = certs[0]
    pub = lambda k: k.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)  # noqa: E731
    if pub(leaf.public_key()) != pub(key.public_key()):
        raise HTTPException(400, "Private key không khớp với chứng chỉ")
    domains: list[str] = []
    try:
        domains = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        cn = leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        domains = [cn[0].value] if cn else []
    issuer = leaf.issuer.get_attributes_for_oid(NameOID.ORGANIZATION_NAME) or leaf.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    return {"domains": list(domains), "not_after": leaf.not_valid_after_utc, "issuer": issuer[0].value if issuer else ""}


def cert_out(c: Certificate, used: int = 0) -> dict[str, Any]:
    from datetime import datetime

    days = (c.not_after - datetime.now(UTC)).days if c.not_after else None
    return {"id": c.id, "name": c.name, "domains": c.domains, "issuer": c.issuer, "not_after": c.not_after, "days_left": days,
            "used_by": used, "created_at": c.created_at}


@router.get("/certificates")
async def list_certs(user: CurrentUser, db: DB):
    used: dict[int, int] = {}
    for r in (await db.execute(select(ProxyRoute.certificate_id).where(ProxyRoute.certificate_id.is_not(None)))).scalars():
        used[r] = used.get(r, 0) + 1
    custom = [cert_out(c, used.get(c.id, 0)) for c in (await db.execute(select(Certificate).order_by(Certificate.id))).scalars()]
    return {"custom": custom, "managed": await proxy.caddy_certs()}


class CertIn(BaseModel):
    name: str
    cert_pem: str
    key_pem: str


@router.post("/certificates")
async def upload_cert(body: CertIn, user: StaffUser, db: DB, request: Request):
    info = _parse_cert(body.cert_pem.strip() + "\n", body.key_pem.strip() + "\n")
    c = Certificate(name=body.name, cert_pem=body.cert_pem.strip() + "\n", key_pem_enc=encrypt(body.key_pem.strip() + "\n"), **info)
    db.add(c)
    await db.commit()
    await audit(db, user, "proxy.cert_upload", body.name, {"domains": info["domains"]}, request)
    return cert_out(c)


@router.put("/certificates/{cid}")
async def replace_cert(cid: int, body: CertIn, user: StaffUser, db: DB, request: Request):
    c = await db.get(Certificate, cid)
    if c is None:
        raise HTTPException(404)
    info = _parse_cert(body.cert_pem.strip() + "\n", body.key_pem.strip() + "\n")
    c.name, c.cert_pem, c.key_pem_enc = body.name, body.cert_pem.strip() + "\n", encrypt(body.key_pem.strip() + "\n")
    c.domains, c.not_after, c.issuer = info["domains"], info["not_after"], info["issuer"]
    await db.commit()
    await audit(db, user, "proxy.cert_replace", body.name, request=request)
    return {**cert_out(c), "apply": await apply_all(db)}


@router.delete("/certificates/{cid}")
async def delete_cert(cid: int, user: StaffUser, db: DB, request: Request):
    c = await db.get(Certificate, cid)
    if c is None:
        raise HTTPException(404)
    await db.delete(c)
    await db.commit()
    await audit(db, user, "proxy.cert_delete", c.name, request=request)
    return {"ok": True, "apply": await apply_all(db)}


# ======================================================================= streams (TCP/UDP)
def stream_out(s: Stream) -> dict[str, Any]:
    return {"id": s.id, "listen_port": s.listen_port, "protocol": s.protocol, "upstream": s.upstream, "enabled": s.enabled,
            "app_id": s.app_id, "note": s.note, "created_at": s.created_at}


class StreamIn(BaseModel):
    listen_port: int
    protocol: str = "tcp"
    upstream: str
    enabled: bool = True
    app_id: int | None = None
    note: str = ""


async def _stream_validate(body: StreamIn, db: AsyncSession, sid: int | None) -> None:
    if not (1 <= body.listen_port <= 65535):
        raise HTTPException(400, "Cổng không hợp lệ")
    if body.protocol not in ("tcp", "udp", "both"):
        raise HTTPException(400, "protocol phải là tcp | udp | both")
    http, https = await _ports(db)
    if body.listen_port in (http, https, 80, 443):
        raise HTTPException(400, "Cổng trùng cổng HTTP/HTTPS của proxy")
    try:
        proxy.parse_upstream(body.upstream)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    dup = (await db.execute(select(Stream).where(Stream.listen_port == body.listen_port))).scalar_one_or_none()
    if dup and dup.id != sid:
        raise HTTPException(400, f"Cổng {body.listen_port} đã có stream #{dup.id}")
    from ..services.caddy_ports import port_conflicts

    protos = ["tcp", "udp"] if body.protocol == "both" else [body.protocol]
    issues = await port_conflicts([f"{body.listen_port}:{body.listen_port}/{p}" for p in protos])
    if issues:
        raise HTTPException(400, "; ".join(issues))


async def _ports(db: AsyncSession) -> tuple[int, int]:
    from ..services.caddy_ports import current_ports

    return await current_ports(db)


@router.get("/streams")
async def list_streams(user: StaffUser, db: DB):
    return [stream_out(s) for s in (await db.execute(select(Stream).order_by(Stream.listen_port))).scalars()]


@router.post("/streams")
async def create_stream(body: StreamIn, user: StaffUser, db: DB, request: Request):
    await _stream_validate(body, db, None)
    s = Stream(**body.model_dump())
    db.add(s)
    await db.commit()
    host = proxy.parse_upstream(body.upstream)[0].rsplit(":", 1)[0]
    if not re.match(r"^[\d.]+$", host) and "." not in host and host != "host.docker.internal":
        if await docker_svc.get_container(host):
            await docker_svc.connect_network(host, settings.edge_network)
    job = await enqueue(db, "caddy_ports", {}, None, user.id)
    await audit(db, user, "proxy.stream_create", str(s.listen_port), body.model_dump(), request)
    return {**stream_out(s), "job_id": job.id}


@router.put("/streams/{sid}")
async def update_stream(sid: int, body: StreamIn, user: StaffUser, db: DB, request: Request):
    s = await db.get(Stream, sid)
    if s is None:
        raise HTTPException(404)
    port_changed = s.listen_port != body.listen_port or s.protocol != body.protocol or s.enabled != body.enabled
    if port_changed:
        await _stream_validate(body, db, sid)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    await db.commit()
    await audit(db, user, "proxy.stream_update", str(s.listen_port), request=request)
    if port_changed:
        job = await enqueue(db, "caddy_ports", {}, None, user.id)
        return {**stream_out(s), "job_id": job.id}
    return {**stream_out(s), "apply": await apply_all(db)}


@router.delete("/streams/{sid}")
async def delete_stream(sid: int, user: StaffUser, db: DB, request: Request):
    s = await db.get(Stream, sid)
    if s is None:
        raise HTTPException(404)
    await db.delete(s)
    await db.commit()
    job = await enqueue(db, "caddy_ports", {}, None, user.id)
    await audit(db, user, "proxy.stream_delete", str(s.listen_port), request=request)
    return {"ok": True, "job_id": job.id}


# ======================================================================= logs
@router.get("/logs")
async def access_logs(user: CurrentUser, db: DB, host: str | None = None, limit: int = 200, status_min: int | None = None):
    if not is_staff(user):
        if not host:
            raise HTTPException(403, "Chọn domain thuộc app của bạn")
        ids = await visible_app_ids(user, db) or set()
        mine = {h for r in (await db.execute(select(ProxyRoute).where(ProxyRoute.app_id.in_(ids or [-1])))).scalars() for h in r.hosts}
        if host not in mine:
            raise HTTPException(403)
    return await docker_svc.run(proxy.read_access_log, host, min(limit, 2000), status_min)


@router.get("/stats")
async def access_stats(user: StaffUser):
    return await docker_svc.run(proxy.access_stats)


# ======================================================================= settings: default site, cổng, chuyển đổi NPM
class DefaultSiteIn(BaseModel):
    mode: str  # 404 | congrats | redirect | html | abort
    value: str = ""


@router.put("/default-site")
async def set_default_site(body: DefaultSiteIn, user: AdminUser, db: DB, request: Request):
    if body.mode not in ("404", "congrats", "redirect", "html", "abort"):
        raise HTTPException(400, "mode không hợp lệ")
    await set_setting("proxy_default_site", body.model_dump(), db)
    await db.commit()
    await audit(db, user, "proxy.default_site", body.mode, request=request)
    return {"ok": True, "apply": await apply_all(db)}


@router.get("/settings")
async def proxy_settings(user: StaffUser, db: DB):
    from ..services.caddy_ports import desired_ports, npm_containers, port_conflicts

    http, https = await _ports(db)
    ports = await desired_ports(db)
    return {
        "default_site": await get_setting("proxy_default_site", db),
        "ports": {"http": http, "https": https},
        "public": bool(await get_setting("caddy_public", db)),
        "acme_email": await get_setting("acme_email", db),
        "published": ports,
        "conflicts": await port_conflicts(ports),
        "npm": [{"name": c.name, "state": c.status, "image": c.attrs["Config"].get("Image")} for c in await npm_containers()],
        "npm_stopped": await get_setting("npm_stopped_containers", db) or [],
    }


class AcmeIn(BaseModel):
    acme_email: str


@router.put("/acme")
async def set_acme(body: AcmeIn, user: AdminUser, db: DB):
    await set_setting("acme_email", body.acme_email.strip(), db)
    await db.commit()
    return {"ok": True, "apply": await apply_all(db)}


@router.get("/cutover/check")
async def cutover_check(user: AdminUser, db: DB):
    """Kiểm tra trước khi chuyển 80/443 từ NPM sang Caddy."""
    from ..services.caddy_ports import npm_containers, port_conflicts

    routes = (await db.execute(select(ProxyRoute).where(ProxyRoute.enabled.is_(True)))).scalars().all()
    direct = [r for r in routes if not r.cf_tunnel]
    npm_names = {c.name for c in await npm_containers()}
    issues = [i for i in await port_conflicts(["80:80/tcp", "443:443/tcp"]) if not any(n in i for n in npm_names)]
    warnings = []
    if not await get_setting("acme_email", db):
        warnings.append("Chưa đặt email ACME (khuyến nghị để nhận thông báo hết hạn cert)")
    no_tls = [", ".join(r.hosts) for r in direct if r.tls_mode in ("tunnel", "off") and not r.certificate_id and r.kind != "dead"]
    if no_tls:
        warnings.append(f"{len(no_tls)} route truy cập trực tiếp nhưng chưa bật HTTPS: {'; '.join(no_tls[:5])}")
    try:
        from ..services.npm_import import read_npm

        npm_hosts = {h for it in read_npm()["items"] if it["enabled"] for h in it["hosts"]}
        mine = {h for r in routes for h in r.hosts}
        missing = sorted(npm_hosts - mine)
        if missing:
            warnings.append(f"Host trên NPM chưa có trong panel: {', '.join(missing)}")
    except Exception:  # noqa: BLE001
        pass
    # thử chạy khô: gọi qua Caddy (cổng phụ) với Host của từng route trực tiếp, và đối chiếu DNS với IP public của server
    import socket

    import httpx

    public_ip = None
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            public_ip = (await http.get("https://api.ipify.org")).text.strip()
    except httpx.HTTPError:
        pass
    checks = []
    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as http:
        for r in direct:
            for h in r.hosts:
                if h.startswith("*"):
                    continue
                item: dict[str, Any] = {"host": h, "kind": r.kind}
                try:
                    ips = sorted({ai[4][0] for ai in await asyncio.get_running_loop().getaddrinfo(h, 80, type=socket.SOCK_STREAM)})
                    item["dns"] = ips
                    item["dns_ok"] = public_ip in ips if public_ip else None
                except OSError:
                    item["dns"], item["dns_ok"] = [], False
                try:
                    resp = await http.get(f"http://{settings.caddy_container}:80{r.path_prefix or '/'}", headers={"Host": h, "X-Forwarded-Proto": "https"})
                    item["via_caddy"] = resp.status_code
                    item["ok"] = resp.status_code < 500
                except httpx.HTTPError as e:
                    item["via_caddy"], item["ok"] = str(e)[:80], False
                checks.append(item)
    bad_dns = [c["host"] for c in checks if c.get("dns_ok") is False]
    if bad_dns:
        warnings.append(f"DNS không trỏ về IP server ({public_ip}): {', '.join(bad_dns)} — Let's Encrypt HTTP-01 sẽ thất bại cho các domain này")
    bad_up = [c["host"] for c in checks if not c["ok"]]
    if bad_up:
        warnings.append(f"Upstream không phản hồi khi thử qua Caddy: {', '.join(bad_up)}")
    return {"blocking": issues, "warnings": warnings, "npm_containers": sorted(npm_names), "direct_routes": len(direct),
            "public_ip": public_ip, "checks": checks}


class CutoverIn(BaseModel):
    confirm: str
    http_port: int = 80
    https_port: int = 443


@router.post("/cutover")
async def cutover(body: CutoverIn, user: AdminUser, db: DB, request: Request):
    if body.confirm != "CHUYEN":
        raise HTTPException(400, "Nhập CHUYEN để xác nhận")
    job = await enqueue(db, "proxy_cutover", {"http_port": body.http_port, "https_port": body.https_port}, None, user.id)
    await audit(db, user, "proxy.cutover", f"{body.http_port}/{body.https_port}", request=request)
    return {"job_id": job.id}


@router.post("/rollback")
async def rollback(user: AdminUser, db: DB, request: Request):
    job = await enqueue(db, "proxy_rollback", {}, None, user.id)
    await audit(db, user, "proxy.rollback", "", request=request)
    return {"job_id": job.id}


# ======================================================================= NPM import
@router.get("/npm-import")
async def npm_preview(user: StaffUser, db: DB):
    from ..services.npm_import import public_view, read_npm

    try:
        data = public_view(read_npm())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    existing = {h for r in (await db.execute(select(ProxyRoute))).scalars() for h in r.hosts}
    ports = {s.listen_port for s in (await db.execute(select(Stream))).scalars()}
    acl_names = set((await db.execute(select(AccessList.name))).scalars())
    for it in data["items"]:
        it["already"] = bool(set(it["hosts"]) & existing)
    for s in data["streams"]:
        s["already"] = s["listen_port"] in ports
    for a in data["access_lists"]:
        a["already"] = a["name"] in acl_names
    return data


class NpmImportIn(BaseModel):
    keys: list[str] = Field(default_factory=list)  # "proxy:12" | "redirect:3" | "dead:1" | "stream:2"
    npm_ids: list[str] = Field(default_factory=list)  # tương thích bản cũ
    mode: str = "direct"  # direct: thay NPM trên 80/443 (HTTPS Let's Encrypt) | tunnel: qua Cloudflare Tunnel
    import_access_lists: bool = True
    import_default_site: bool = True


def _translate_upstream(u: str) -> tuple[str, str | None]:
    """Upstream của NPM trỏ tới container trong mạng NPM → giữ tên; IP gateway docker → host."""
    note = None
    m = re.match(r"^(https?)://([^:/]+):(\d+)$", u)
    if m and m.group(2) in ("172.17.0.1", "host.docker.internal"):
        return f"{m.group(1)}://host:{m.group(3)}", note
    return u, note


@router.post("/npm-import")
async def npm_import(body: NpmImportIn, user: StaffUser, db: DB, request: Request):
    from ..services.npm_import import read_npm

    data = read_npm()
    wanted = set(body.keys or body.npm_ids)
    direct = body.mode == "direct"
    created: list[Any] = []
    notes: list[str] = []
    acl_map: dict[int, int] = {}

    if body.import_access_lists:
        existing = {a.name: a for a in (await db.execute(select(AccessList))).scalars()}
        for a in data["access_lists"]:
            row = existing.get(a["name"])
            if row is None:
                row = AccessList(name=a["name"], satisfy_any=a["satisfy_any"], pass_auth=a["pass_auth"],
                                 auth=[{"username": u["username"], "password_hash": _hash(u["_password"])} for u in a["auth"] if u.get("_password")],
                                 clients=[c for c in a["clients"] if c["directive"] in ("allow", "deny")])
                db.add(row)
                await db.flush()
                created.append(f"access list {a['name']}")
            acl_map[a["npm_id"]] = row.id

    existing_hosts = {h for r in (await db.execute(select(ProxyRoute))).scalars() for h in r.hosts}
    for it in data["items"]:
        if it["key"] not in wanted:
            continue
        if set(it["hosts"]) & existing_hosts:
            notes.append(f"{it['hosts'][0]}: đã có trong panel — bỏ qua")
            continue
        opts: dict[str, Any] = {}
        if it.get("block_exploits"):
            opts["block_exploits"] = True
        if it.get("ssl_forced"):
            opts["force_https"] = True
        if it.get("hsts"):
            opts["hsts"] = True
            opts["hsts_subdomains"] = it.get("hsts_subdomains", False)
        tls_mode = ("auto" if it.get("ssl") else "off") if direct else "tunnel"
        if it.get("dns_challenge"):
            opts["acme_challenge"] = "dns"
        ups: list[str] = []
        kind = it["type"]
        if kind == "proxy":
            ups = [_translate_upstream(it["upstream"])[0]]
            if it.get("cache_assets"):
                opts["cache_assets"] = True
            if it["locations"]:
                opts["path_routes"] = [{"path": l["path"], "upstreams": [_translate_upstream(l["upstream"])[0]], "strip_prefix": False}
                                       for l in it["locations"]]
                if any(l["advanced_config"].strip() for l in it["locations"]):
                    notes.append(f"{it['hosts'][0]}: custom location có advanced_config nginx — cần chuyển tay sang Caddyfile")
        elif kind == "redirect":
            opts.update(redirect_to=it["redirect_to"], redirect_scheme=it["redirect_scheme"], redirect_code=it["redirect_code"],
                        redirect_keep_path=it["preserve_path"])
        if it.get("advanced_config", "").strip():
            notes.append(f"{it['hosts'][0]}: có advanced_config nginx — xem lại và viết tương đương ở ô 'Caddyfile nâng cao'")
            opts["npm_advanced_config"] = it["advanced_config"][:4000]
        acl_id = acl_map.get(it.get("access_list_npm_id") or -1)
        if it.get("access_list_npm_id") and not acl_id:
            notes.append(f"{it['hosts'][0]}: access list NPM #{it['access_list_npm_id']} chưa được import")
        r = ProxyRoute(kind=kind, hosts=it["hosts"], upstreams=ups, options=opts, cf_tunnel=not direct, tls_mode=tls_mode,
                       enabled=it["enabled"], access_list_id=acl_id, source="npm-import", note=f"Import từ NPM {it['key']}")
        try:
            await _attach_upstreams(RouteIn(hosts=it["hosts"], upstreams=ups, options=opts), user, db)
        except HTTPException as e:
            notes.append(f"{it['hosts'][0]}: {e.detail}")
        db.add(r)
        existing_hosts.update(it["hosts"])
        created.append(it["hosts"])

    stream_job = None
    have_ports = {s.listen_port for s in (await db.execute(select(Stream))).scalars()}
    new_streams = 0
    for s in data["streams"]:
        if s["key"] not in wanted or s["listen_port"] in have_ports:
            continue
        db.add(Stream(listen_port=s["listen_port"], protocol=s["protocol"], upstream=_translate_upstream("http://" + s["upstream"])[0].split("://", 1)[1],
                      enabled=s["enabled"], note=f"Import từ NPM {s['key']}"))
        new_streams += 1
        created.append(f"stream :{s['listen_port']}")
    if body.import_default_site and data.get("default_site"):
        v = data["default_site"]["value"]
        mapping = {"congratulations": {"mode": "congrats"}, "404": {"mode": "404"}, "444": {"mode": "abort"},
                   "redirect": {"mode": "redirect", "value": data["default_site"]["meta"].get("redirect", "")},
                   "html": {"mode": "html", "value": data["default_site"]["meta"].get("html", "")}}
        if v in mapping:
            await set_setting("proxy_default_site", {"value": "", **mapping[v]}, db)
    for c in data["custom_certs"]:
        notes.append(f"Chứng chỉ custom NPM '{c['name']}' ({', '.join(c['domains'])}): tải lên lại trong tab Chứng chỉ"
                     + (f" — file tại {c['dir']}" if c["available"] else ""))
    await db.commit()
    if new_streams:
        stream_job = (await enqueue(db, "caddy_ports", {}, None, user.id)).id
        notes.append("Stream cần cổng riêng: đang tạo lại th-caddy để publish cổng (nếu cổng còn bị NPM giữ, chạy lại sau khi chuyển đổi)")
    await audit(db, user, "proxy.npm_import", "", {"count": len(created), "mode": body.mode}, request)
    return {"created": created, "notes": notes, "stream_job_id": stream_job, "apply": await apply_all(db)}
