"""Sinh cấu hình JSON cho Caddy từ DB và đẩy qua Admin API (unix socket).

Luồng traffic:
  - Qua Cloudflare Tunnel: Internet → CF edge (TLS) → th-cloudflared → th-caddy:80 → upstream
  - Trực tiếp (thay NPM): Internet → th-caddy:80/443 (cert Let's Encrypt HTTP-01 / DNS-01 Cloudflare / tải lên) → upstream

Tính năng tương đương Nginx Proxy Manager: proxy host (+ custom location), redirection host, 404 host,
stream TCP/UDP (caddy-l4), access list, cert tự động / tải lên, force SSL, HSTS, block exploits,
cache assets, websocket, cấu hình nâng cao (Caddyfile snippet), default site, access log.
"""

import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.security import decrypt
from ..core.store import get_setting
from ..models import AccessList, Certificate, ProxyRoute, Stream

log = logging.getLogger("th.proxy")
PRIVATE_RANGES = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.1/32", "fd00::/8", "::1/128"]
ACCESS_LOG = "/data/logs/access.log"  # trong container caddy; trên host: <data>/caddy/data/logs/access.log

# Tương đương block-exploits.conf của NPM (RE2, không phân biệt hoa thường)
EXPLOIT_QUERY = (
    r"(?i)(union.*select.*\(|union.*all.*select|concat.*\(|(<|%3C).*script.*(>|%3E)|GLOBALS(=|\[|%[0-9A-Z]{0,2})"
    r"|_REQUEST(=|\[|%[0-9A-Z]{0,2})|proc/self/environ|mosConfig_[a-zA-Z_]{1,21}(=|%3D)|base64_(en|de)code\(.*\)"
    r"|[a-zA-Z0-9_]=http://|[a-zA-Z0-9_]=(\.\.//?)+|[a-zA-Z0-9_]=/([a-z0-9_.]//?)+"
    r"|\b(ultram|unicauca|valium|viagra|vicodin|xanax|ypxaieo)\b|(erections|hoodia|huronriveracres|impotence|levitra|libido)"
    r"|(cialis|cocaine|ejaculation|erectile|lipitor|phentermin|pro[sz]ac|sandyauer|tramadol|troyhamby))"
)
EXPLOIT_PATH = r"(?i)(\.\./|\.\.%2f|/etc/passwd|proc/self/environ|\.(htaccess|htpasswd)$)"
EXPLOIT_UA = r"(?i)(Indy Library|libwww-perl|GetRight|GetWeb!|Go!Zilla|Download Demon|Go-Ahead-Got-It|TurnitinBot|GrabNet|zmeu|sqlmap|nikto|masscan)"
ASSET_RE = r"(?i)\.(css|js|mjs|jpe?g|png|gif|ico|svg|webp|avif|woff2?|ttf|otf|eot|mp4|webm|mp3)$"

DEFAULT_PAGES = {
    "congrats": "<!doctype html><html><head><meta charset=utf-8><title>Tekar Hosting</title></head>"
    "<body style='font-family:system-ui;text-align:center;padding-top:18vh;color:#334155'>"
    "<h1>Tekar Hosting</h1><p>Server đang hoạt động. Domain này chưa được cấu hình.</p></body></html>",
}

_adapt_cache: dict[str, list[dict[str, Any]]] = {}


# ======================================================================= admin api
def _admin_client() -> httpx.AsyncClient:
    url = settings.caddy_admin
    if url.startswith("unix://"):
        return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=url[len("unix://"):]), base_url="http://caddy", timeout=30)
    return httpx.AsyncClient(base_url=url, timeout=30)


def admin_listen() -> str:
    url = settings.caddy_admin
    return "unix/" + url[len("unix://"):] if url.startswith("unix://") else "0.0.0.0:2019"


async def adapt_caddyfile(snippet: str) -> list[dict[str, Any]]:
    """Biên dịch đoạn Caddyfile (các directive trong site block) thành danh sách route JSON."""
    key = hashlib.sha256(snippet.encode()).hexdigest()
    if key in _adapt_cache:
        return _adapt_cache[key]
    if len(_adapt_cache) > 256:
        _adapt_cache.clear()
    body = ":80 {\n" + snippet + "\n}\n"
    async with _admin_client() as c:
        r = await c.post("/adapt", content=body, headers={"Content-Type": "text/caddyfile"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", r.text)
        except ValueError:
            msg = r.text
        raise ValueError(f"Caddyfile không hợp lệ: {msg[:500]}")
    cfg = r.json().get("result", {})
    servers = (cfg.get("apps", {}).get("http", {}).get("servers") or {})
    routes: list[dict[str, Any]] = []
    for srv in servers.values():
        routes.extend(srv.get("routes") or [])
    _adapt_cache[key] = routes
    return routes


# ======================================================================= helpers
def parse_upstream(raw: str) -> tuple[str, bool]:
    """→ (dial, https)."""
    raw = raw.strip()
    if raw.startswith(("http://", "https://")):
        u = urlparse(raw)
        port = u.port or (443 if u.scheme == "https" else 80)
        host = "host.docker.internal" if u.hostname in ("host", "localhost", "127.0.0.1") else u.hostname
        return f"{host}:{port}", u.scheme == "https"
    host, _, port = raw.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"Upstream không hợp lệ: {raw} (dùng container:port, host:port hoặc URL)")
    if host in ("host", "localhost", "127.0.0.1"):
        host = "host.docker.internal"
    return f"{host}:{port}", False


def _static(status: int, body: str = "", headers: dict[str, list[str]] | None = None, close: bool = False) -> dict[str, Any]:
    h: dict[str, Any] = {"handler": "static_response", "status_code": status}
    if body:
        h["body"] = body
    if headers:
        h["headers"] = headers
    if close:
        h["close"] = True
    return h


def _redirect(location: str, code: int = 301) -> dict[str, Any]:
    return _static(code, headers={"Location": [location]})


def _proxy_handler(upstreams: list[str], opts: dict[str, Any], strip_auth: bool = False) -> dict[str, Any]:
    dials = [parse_upstream(u) for u in upstreams]
    https = any(h for _, h in dials)
    h: dict[str, Any] = {"handler": "reverse_proxy", "upstreams": [{"dial": d} for d, _ in dials]}
    transport: dict[str, Any] = {"protocol": "http"}
    if https:
        tls: dict[str, Any] = {}
        if opts.get("upstream_insecure"):
            tls["insecure_skip_verify"] = True
        if opts.get("upstream_sni"):
            tls["server_name"] = opts["upstream_sni"]
        transport["tls"] = tls
    if opts.get("timeout_s"):
        transport["read_timeout"] = f"{int(opts['timeout_s'])}s"
        transport["write_timeout"] = f"{int(opts['timeout_s'])}s"
    h["transport"] = transport
    if len(dials) > 1:
        h["load_balancing"] = {"selection_policy": {"policy": opts.get("lb_policy") or "round_robin"}}
    if opts.get("health_uri"):
        h["health_checks"] = {"active": {"uri": opts["health_uri"], "interval": "15s", "timeout": "5s"}}
    # NPM gửi X-Real-IP; Caddy mặc định chỉ có X-Forwarded-For → thêm để app cũ vẫn thấy IP khách
    req: dict[str, Any] = {"set": {"X-Real-IP": ["{http.vars.client_ip}"]}}
    if opts.get("host_header"):
        req["set"]["Host"] = ["{http.reverse_proxy.upstream.hostport}" if opts["host_header"] == "upstream" else opts["host_header"]]
    if opts.get("headers_up"):
        req.setdefault("set", {}).update({k: [v] for k, v in opts["headers_up"].items()})
    if strip_auth:
        req["delete"] = ["Authorization"]
    if req:
        h["headers"] = {"request": req}
    if opts.get("flush_interval") is not None:
        h["flush_interval"] = int(opts["flush_interval"])
    return h


def _ranges(address: str) -> list[str]:
    return ["0.0.0.0/0", "::/0"] if address.strip().lower() in ("all", "*", "") else [address.strip()]


def _access_list_handlers(acl: AccessList) -> list[dict[str, Any]]:
    """Tương đương access list NPM: rule IP theo thứ tự (khớp đầu tiên thắng, mặc định chặn),
    kết hợp Basic Auth theo satisfy any/all."""
    hs: list[dict[str, Any]] = []
    auth_handler = None
    if acl.auth:
        auth_handler = {
            "handler": "authentication",
            "providers": {"http_basic": {
                "accounts": [{"username": a["username"], "password": a["password_hash"]} for a in acl.auth],
                "hash": {"algorithm": "bcrypt"}, "realm": acl.name,
            }},
        }
    # tập matcher "IP không được phép"
    not_ok: list[dict[str, Any]] = []
    prev: list[str] = []
    for rule in acl.clients or []:
        rng = _ranges(rule.get("address", ""))
        if rule.get("directive") == "deny":
            m: dict[str, Any] = {"client_ip": {"ranges": rng}}
            if prev:
                m["not"] = [{"client_ip": {"ranges": prev}}]
            not_ok.append(m)
        prev = prev + rng
    if acl.clients:
        not_ok.append({"not": [{"client_ip": {"ranges": prev}}]})  # không khớp rule nào → chặn
    else:
        not_ok.append({})  # không có rule IP → coi như IP không đạt (phải qua auth)

    if acl.satisfy_any:
        if auth_handler:
            hs.append({"handler": "subroute", "routes": [{"match": not_ok, "handle": [auth_handler]}]})
        elif acl.clients:
            hs.append({"handler": "subroute", "routes": [{"match": not_ok, "handle": [_static(403, "Forbidden")], "terminal": True}]})
    else:
        if acl.clients:
            hs.append({"handler": "subroute", "routes": [{"match": not_ok, "handle": [_static(403, "Forbidden")], "terminal": True}]})
        if auth_handler:
            hs.append(auth_handler)
    return hs


def _block_exploits() -> dict[str, Any]:
    return {
        "handler": "subroute",
        "routes": [{
            "match": [
                {"vars_regexp": {"{http.request.uri.query}": {"pattern": EXPLOIT_QUERY}}},
                {"path_regexp": {"pattern": EXPLOIT_PATH}},
                {"header_regexp": {"User-Agent": {"pattern": EXPLOIT_UA}}},
            ],
            "handle": [_static(403, "Forbidden")],
            "terminal": True,
        }],
    }


async def _handlers(r: ProxyRoute, acls: dict[int, AccessList]) -> list[dict[str, Any]]:
    o = r.options or {}
    hs: list[dict[str, Any]] = []

    # force SSL khi đi qua tunnel: cloudflared gửi X-Forwarded-Proto của client
    if o.get("force_https") and r.cf_tunnel:
        hs.append({"handler": "subroute", "routes": [{
            "match": [{"header": {"X-Forwarded-Proto": ["http"]}}],
            "handle": [_redirect("https://{http.request.host}{http.request.uri}", 301)], "terminal": True,
        }]})
    if o.get("block_exploits"):
        hs.append(_block_exploits())

    acl = acls.get(r.access_list_id) if r.access_list_id else None
    if acl:
        hs.extend(_access_list_handlers(acl))
    if o.get("ip_allow"):
        hs.append({"handler": "subroute", "routes": [{
            "match": [{"not": [{"client_ip": {"ranges": o["ip_allow"]}}]}],
            "handle": [_static(403, "Forbidden")], "terminal": True,
        }]})
    if o.get("basic_auth"):
        hs.append({
            "handler": "authentication",
            "providers": {"http_basic": {
                "accounts": [{"username": a["username"], "password": a["password_hash"]} for a in o["basic_auth"]],
                "hash": {"algorithm": "bcrypt"}, "realm": "restricted",
            }},
        })

    # response headers (áp dụng cho mọi loại route)
    resp: dict[str, Any] = {}
    if o.get("security_headers", True):
        resp["set"] = {"X-Content-Type-Options": ["nosniff"], "Referrer-Policy": ["strict-origin-when-cross-origin"]}
    if o.get("hsts"):
        v = "max-age=63072000" + ("; includeSubDomains" if o.get("hsts_subdomains") else "") + ("; preload" if o.get("hsts_preload") else "")
        resp.setdefault("set", {})["Strict-Transport-Security"] = [v]
    if o.get("cors_origins"):
        resp.setdefault("set", {}).update({
            "Access-Control-Allow-Origin": [o["cors_origins"]],
            "Access-Control-Allow-Methods": ["GET, POST, PUT, PATCH, DELETE, OPTIONS"],
            "Access-Control-Allow-Headers": ["*"],
            "Access-Control-Allow-Credentials": ["true"],
        })
    if o.get("headers_down"):
        resp.setdefault("set", {}).update({k: [v] for k, v in o["headers_down"].items()})
    if o.get("hide_headers"):
        resp["delete"] = list(o["hide_headers"])
    if resp:
        hs.append({"handler": "headers", "response": {**resp, "deferred": True}})

    kind = r.kind or "proxy"
    if kind == "dead":
        hs.append(_static(int(o.get("status_code") or 404), o.get("body") or "Not Found"))
        return hs
    if o.get("maintenance"):
        hs.append(_static(503, o.get("maintenance_html") or
                          "<html><body style='font-family:sans-serif;text-align:center;padding-top:15vh'><h1>Đang bảo trì</h1>"
                          "<p>Vui lòng quay lại sau ít phút.</p></body></html>",
                          {"Content-Type": ["text/html; charset=utf-8"], "Retry-After": ["600"]}))
        return hs
    if kind == "redirect" or o.get("redirect_to"):
        target = (o.get("redirect_to") or "").rstrip("/")
        if o.get("redirect_scheme") in ("http", "https") and "://" not in target:
            target = f"{o['redirect_scheme']}://{target}"
        elif o.get("redirect_scheme") == "auto" and "://" not in target:
            target = "{http.request.scheme}://" + target
        keep = o.get("redirect_keep_path", True)
        hs.append(_redirect(target + ("{http.request.uri}" if keep else ""), int(o.get("redirect_code") or 301)))
        return hs

    # ---- proxy
    if o.get("max_body_mb"):
        hs.append({"handler": "request_body", "max_size": int(o["max_body_mb"]) * 1024 * 1024})
    if o.get("cache_assets"):
        hs.append({"handler": "subroute", "routes": [{
            "match": [{"path_regexp": {"pattern": ASSET_RE}}],
            "handle": [{"handler": "headers", "response": {
                "set": {"Cache-Control": [f"public, max-age={int(o.get('cache_max_age') or 2592000)}"]}, "deferred": True}}],
        }]})
    if o.get("gzip", True):
        hs.append({"handler": "encode", "encodings": {"gzip": {}, "zstd": {}}, "prefer": ["zstd", "gzip"]})
    # (custom_handlers — JSON handler thô — đã bỏ: cho phép chèn file_server / reverse_proxy tới admin socket)
    if (o.get("custom_caddyfile") or "").strip():
        hs.append({"handler": "subroute", "routes": await adapt_caddyfile(o["custom_caddyfile"])})

    strip_auth = bool(acl and acl.auth and not acl.pass_auth) or bool(o.get("basic_auth") and not o.get("pass_auth"))
    # custom location: định tuyến theo path tới upstream khác (vd /api/* → backend)
    sub_routes: list[dict[str, Any]] = []
    for pr in o.get("path_routes") or []:
        inner: list[dict[str, Any]] = []
        if pr.get("strip_prefix"):
            inner.append({"handler": "rewrite", "strip_path_prefix": pr["path"].rstrip("*").rstrip("/")})
        if (pr.get("custom_caddyfile") or "").strip():
            inner.append({"handler": "subroute", "routes": await adapt_caddyfile(pr["custom_caddyfile"])})
        inner.append(_proxy_handler(pr["upstreams"], {**o, **(pr.get("options") or {})}, strip_auth))
        path = pr["path"]
        paths = [path] if "*" in path else [path.rstrip("/") or "/", path.rstrip("/") + "/*"]
        sub_routes.append({"match": [{"path": paths}], "handle": inner, "terminal": True})

    main: list[dict[str, Any]] = []
    if r.path_prefix and o.get("strip_prefix"):
        main.append({"handler": "rewrite", "strip_path_prefix": r.path_prefix.rstrip("/")})
    main.append(_proxy_handler(r.upstreams, o, strip_auth) if r.upstreams else _static(502, "Chưa cấu hình upstream"))
    if sub_routes:
        hs.append({"handler": "subroute", "routes": sub_routes + [{"handle": main}]})
    else:
        hs.extend(main)
    return hs


def _match(route: ProxyRoute) -> list[dict[str, Any]]:
    m: dict[str, Any] = {"host": route.hosts}
    if route.path_prefix:
        p = route.path_prefix.rstrip("/")
        m["path"] = [p, p + "/*"]
    return [m]


def _sort_key(r: ProxyRoute) -> tuple:
    return (-r.priority, -len(r.path_prefix or ""), any(h.startswith("*") for h in r.hosts), r.id)


def _default_site(cfg: dict[str, Any]) -> dict[str, Any]:
    mode = (cfg or {}).get("mode") or "404"
    val = (cfg or {}).get("value") or ""
    if mode == "congrats":
        h = _static(200, DEFAULT_PAGES["congrats"], {"Content-Type": ["text/html; charset=utf-8"]})
    elif mode == "redirect" and val:
        h = _redirect(val, 302)
    elif mode == "html":
        h = _static(200, val, {"Content-Type": ["text/html; charset=utf-8"]})
    elif mode == "abort":
        h = {"handler": "static_response", "abort": True}
    else:
        h = _static(404, "Tekar Hosting: không có route cho host này")
    return {"handle": [h]}


async def trusted_proxies(db: AsyncSession) -> list[str]:
    """Nguồn được tin X-Forwarded-* / Cf-Connecting-Ip: chỉ container cloudflared của panel (khi dùng tunnel).
    Không tin cả dải private: container của app khác trên th-edge có thể giả IP để qua mặt ip_allow / access list."""
    if not await get_setting("cloudflare_tunnel_id", db):
        return []
    from . import docker_svc

    c = await docker_svc.get_container(settings.cloudflared_container)
    ips = []
    for n in ((c.attrs.get("NetworkSettings") or {}).get("Networks") or {}).values() if c is not None else []:
        if n.get("IPAddress"):
            ips.append(n["IPAddress"] + "/32")
    return ips or PRIVATE_RANGES  # chưa thấy container (đang tạo) → tạm tin dải private như trước


def _in_zone(host: str, zone: str) -> bool:
    h = host.lstrip("*.")
    return bool(zone) and (h == zone or h.endswith("." + zone))


def _stream_servers(streams: list[Stream]) -> dict[str, Any]:
    servers: dict[str, Any] = {}
    for s in streams:
        dial, _ = parse_upstream(s.upstream)
        protos = ["tcp", "udp"] if s.protocol == "both" else [s.protocol]
        for p in protos:
            servers[f"stream-{s.id}-{p}"] = {
                "listen": [f"{p}/:{s.listen_port}"],
                "routes": [{"handle": [{"handler": "proxy", "upstreams": [{"dial": [f"{p}/{dial}" if p == "udp" else dial]}]}]}],
            }
    return servers


# ======================================================================= build
async def build_config(db: AsyncSession) -> dict[str, Any]:
    routes = (await db.execute(select(ProxyRoute).where(ProxyRoute.enabled.is_(True)))).scalars().all()
    routes = sorted([r for r in routes if r.hosts], key=_sort_key)
    acls = {a.id: a for a in (await db.execute(select(AccessList))).scalars()}
    certs = {c.id: c for c in (await db.execute(select(Certificate))).scalars()}
    streams = (await db.execute(select(Stream).where(Stream.enabled.is_(True)))).scalars().all()

    cf_token = await get_setting("cloudflare_api_token_secret", db)
    zone = (await get_setting("cloudflare_zone_name", db) or "").strip().lower()
    base_domain = (await get_setting("base_domain", db) or "").strip().lower()
    wildcard = bool(await get_setting("wildcard_enabled", db)) and base_domain
    acme_email = await get_setting("acme_email", db) or None
    public = bool(await get_setting("caddy_public", db))  # Caddy đang giữ cổng 80/443 public
    default_site = await get_setting("proxy_default_site", db) or {"mode": "404"}

    http_routes, https_routes = [], []
    dns_hosts: set[str] = set()
    http01_hosts: set[str] = set()
    internal_hosts: set[str] = set()
    manual_hosts: set[str] = set()
    for r in routes:
        o = r.options or {}
        compiled = {"match": _match(r), "handle": [{"handler": "subroute", "routes": [{"handle": await _handlers(r, acls)}]}], "terminal": True}
        cert = certs.get(r.certificate_id) if r.certificate_id else None
        tls_on = r.tls_mode in ("auto", "internal") or cert is not None
        if tls_on:
            https_routes.append(compiled)
            if cert is not None:
                manual_hosts.update(r.hosts)
            elif r.tls_mode == "internal":
                internal_hosts.update(r.hosts)
            else:
                challenge = o.get("acme_challenge") or "auto"
                for h in r.hosts:
                    use_dns = challenge == "dns" or (challenge == "auto" and cf_token and _in_zone(h, zone)) or h.startswith("*.")
                    (dns_hosts if use_dns and cf_token else http01_hosts).add(h)
            if o.get("force_https") and not r.cf_tunnel:
                http_routes.append({"match": _match(r), "handle": [_redirect("https://{http.request.host}{http.request.uri}", 301)], "terminal": True})
                continue
        http_routes.append(compiled)

    fallback = _default_site(default_site)
    ranges = await trusted_proxies(db)
    common: dict[str, Any] = {"logs": {}}
    if ranges:
        common.update(trusted_proxies={"source": "static", "ranges": ranges}, client_ip_headers=["Cf-Connecting-Ip"])
    servers: dict[str, Any] = {
        "http": {"listen": [":80"], "routes": http_routes + [fallback], "automatic_https": {"disable": True}, **common},
    }
    skip_certs = sorted(manual_hosts | (http01_hosts if not public else set()))
    if https_routes:
        auto: dict[str, Any] = {"disable_redirects": True}
        if wildcard:
            auto["prefer_wildcard"] = True
        if skip_certs:
            auto["skip_certificates"] = skip_certs
        servers["https"] = {"listen": [":443"], "routes": https_routes + [fallback], "automatic_https": auto, **common}

    policies: list[dict[str, Any]] = []
    dns_issuer = {
        "module": "acme", **({"email": acme_email} if acme_email else {}),
        "challenges": {"dns": {"provider": {"name": "cloudflare", "api_token": cf_token}}},
    } if cf_token else None
    if wildcard and dns_issuer:
        policies.append({"subjects": [f"*.{base_domain}", base_domain], "issuers": [dns_issuer]})
    if dns_hosts and dns_issuer:
        policies.append({"subjects": sorted(dns_hosts), "issuers": [dns_issuer]})
    if http01_hosts and public:
        # Dự phòng ZeroSSL qua ACME (Caddy tự tạo EAB từ email); module "zerossl" từ Caddy 2.8 là API issuer cần api_key.
        issuers: list[dict[str, Any]] = [{"module": "acme", **({"email": acme_email} if acme_email else {})}]
        if acme_email:
            issuers.append({"module": "acme", "ca": "https://acme.zerossl.com/v2/DV90", "email": acme_email})
        policies.append({"subjects": sorted(http01_hosts), "issuers": issuers})
    if internal_hosts:
        policies.append({"subjects": sorted(internal_hosts), "issuers": [{"module": "internal"}]})

    apps: dict[str, Any] = {"http": {"servers": servers}}
    tls: dict[str, Any] = {}
    if policies:
        tls["automation"] = {"policies": policies}
    used_certs = [certs[r.certificate_id] for r in routes if r.certificate_id in certs]
    if used_certs or (wildcard and dns_issuer):
        tls["certificates"] = {}
        if used_certs:
            seen = set()
            tls["certificates"]["load_pem"] = [
                {"certificate": c.cert_pem, "key": decrypt(c.key_pem_enc), "tags": [f"th-cert-{c.id}"]}
                for c in used_certs if not (c.id in seen or seen.add(c.id))
            ]
        if wildcard and dns_issuer:
            tls["certificates"]["automate"] = [f"*.{base_domain}", base_domain]
    if tls:
        apps["tls"] = tls
    if streams:
        apps["layer4"] = {"servers": _stream_servers(streams)}

    return {
        "admin": {"listen": admin_listen()},
        "logging": {"logs": {
            "default": {"exclude": ["http.log.access"]},
            "access": {
                "include": ["http.log.access"],
                "writer": {"output": "file", "filename": ACCESS_LOG, "roll_size_mb": 50, "roll_keep": 5},
                "encoder": {"format": "json"},
            },
        }},
        "apps": apps,
    }


async def apply(db: AsyncSession) -> dict[str, Any]:
    cfg = await build_config(db)
    async with _admin_client() as c:
        r = await c.post("/load", content=json.dumps(cfg), headers={"Content-Type": "application/json"})
        if r.status_code >= 400:
            raise RuntimeError(f"Caddy từ chối cấu hình: {r.text[:1500]}")
    return cfg


async def caddy_status() -> dict[str, Any]:
    try:
        async with _admin_client() as c:
            r = await c.get("/config/")
            return {"online": r.status_code == 200, "config": r.json() if r.status_code == 200 else None}
    except httpx.HTTPError as e:
        return {"online": False, "error": str(e)}


async def caddy_certs() -> list[dict[str, Any]]:
    """Cert Caddy đang quản lý (ACME / internal) đọc từ thư mục data."""
    from datetime import UTC, datetime
    from pathlib import Path

    from cryptography import x509

    root = settings.data_dir / "caddy" / "data" / "caddy" / "certificates"
    out = []
    if not root.exists():
        return out
    for crt in Path(root).rglob("*.crt"):
        try:
            cert = x509.load_pem_x509_certificate(crt.read_bytes())
            out.append({
                "name": crt.stem,
                "issuer": crt.parent.parent.name,
                "not_after": cert.not_valid_after_utc.isoformat(),
                "days_left": (cert.not_valid_after_utc - datetime.now(UTC)).days,
            })
        except Exception:  # noqa: BLE001
            continue
    return sorted(out, key=lambda x: x["days_left"])


def access_log_path():
    return settings.data_dir / "caddy" / "data" / "logs" / "access.log"


def read_access_log(host: str | None = None, limit: int = 200, status_min: int | None = None, max_bytes: int = 8 * 1024 * 1024) -> list[dict[str, Any]]:
    """Đọc phần cuối access log (JSON lines), lọc theo host / status."""
    p = access_log_path()
    if not p.exists():
        return []
    size = p.stat().st_size
    with p.open("rb") as f:
        f.seek(max(0, size - max_bytes))
        lines = f.read().decode("utf-8", "replace").splitlines()
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        req = e.get("request") or {}
        h = req.get("host", "")
        st = e.get("status", 0)
        if host and h != host:
            continue
        if status_min and st < status_min:
            continue
        out.append({
            "ts": e.get("ts"), "host": h, "method": req.get("method"), "uri": req.get("uri"), "status": st,
            "duration_ms": round((e.get("duration") or 0) * 1000, 1), "size": e.get("size"),
            "ip": req.get("client_ip") or req.get("remote_ip"), "ua": ((req.get("headers") or {}).get("User-Agent") or [""])[0][:120],
            "proto": req.get("proto"), "tls": bool(req.get("tls")),
        })
        if len(out) >= limit:
            break
    return out


def access_stats(max_bytes: int = 16 * 1024 * 1024) -> dict[str, dict[str, Any]]:
    """Thống kê theo host từ phần cuối access log: tổng request, 4xx, 5xx, thời gian TB."""
    rows = read_access_log(limit=10**9, max_bytes=max_bytes)
    stats: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = stats.setdefault(r["host"], {"requests": 0, "4xx": 0, "5xx": 0, "total_ms": 0.0, "last": None})
        s["requests"] += 1
        if 400 <= r["status"] < 500:
            s["4xx"] += 1
        elif r["status"] >= 500:
            s["5xx"] += 1
        s["total_ms"] += r["duration_ms"]
        s["last"] = max(s["last"] or 0, r["ts"] or 0)
    for s in stats.values():
        s["avg_ms"] = round(s.pop("total_ms") / s["requests"], 1) if s["requests"] else 0
    return stats
