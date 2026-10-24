"""Đọc toàn bộ cấu hình Nginx Proxy Manager (database.sqlite) để chuyển sang Caddy:
proxy host (+ custom location, SSL, HSTS, block exploits, cache, websocket, access list),
redirection host, 404 host, stream, access list, chứng chỉ custom, default site."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config import settings


def _con(path: str | None):
    p = Path(path or settings.npm_sqlite)
    if not p.exists():
        raise FileNotFoundError(f"Không thấy {p}")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con, p


def _j(v: str | None, default: Any) -> Any:
    try:
        return json.loads(v) if v else default
    except ValueError:
        return default


def _upstream(scheme: str, host: str, port: int) -> str:
    return f"{scheme or 'http'}://{host}:{port}"


def read_npm(path: str | None = None) -> dict[str, Any]:
    con, p = _con(path)
    try:
        certs = {r["id"]: dict(r) for r in con.execute(
            "SELECT id, provider, nice_name, domain_names, expires_on, meta FROM certificate WHERE is_deleted = 0")}
        acls = []
        for a in con.execute("SELECT id, name, satisfy_any, pass_auth FROM access_list WHERE is_deleted = 0"):
            acls.append({
                "npm_id": a["id"], "name": a["name"], "satisfy_any": bool(a["satisfy_any"]), "pass_auth": bool(a["pass_auth"]),
                # mật khẩu NPM lưu dạng rõ → chỉ đọc khi import để băm bcrypt, không trả ra API
                "auth": [{"username": r["username"], "_password": r["password"]} for r in con.execute(
                    "SELECT username, password FROM access_list_auth WHERE access_list_id = ?", (a["id"],))],
                "clients": [{"address": r["address"], "directive": r["directive"]} for r in con.execute(
                    "SELECT address, directive FROM access_list_client WHERE access_list_id = ?", (a["id"],))],
            })

        def ssl(row) -> dict[str, Any]:
            cid = row["certificate_id"]
            cert = certs.get(cid) if cid else None
            return {
                "ssl": bool(cert),
                "cert_provider": cert["provider"] if cert else None,
                "cert_npm_id": cid if cert else None,
                "dns_challenge": bool(_j(cert["meta"], {}).get("dns_challenge")) if cert else False,
                "ssl_forced": bool(row["ssl_forced"]),
                "hsts": bool(row["hsts_enabled"]),
                "hsts_subdomains": bool(row["hsts_subdomains"]),
            }

        items: list[dict[str, Any]] = []
        for r in con.execute("SELECT * FROM proxy_host WHERE is_deleted = 0"):
            locs = [
                {"path": l.get("path") or "/", "upstream": _upstream(l.get("forward_scheme"), l.get("forward_host"), l.get("forward_port")),
                 "advanced_config": l.get("advanced_config") or ""}
                for l in _j(r["locations"], []) if l.get("forward_host")
            ]
            items.append({
                "key": f"proxy:{r['id']}", "type": "proxy", "npm_id": r["id"], "hosts": _j(r["domain_names"], []),
                "upstream": _upstream(r["forward_scheme"], r["forward_host"], r["forward_port"]),
                "websocket": bool(r["allow_websocket_upgrade"]), "block_exploits": bool(r["block_exploits"]),
                "cache_assets": bool(r["caching_enabled"]), "access_list_npm_id": r["access_list_id"] or None,
                "locations": locs, "advanced_config": r["advanced_config"] or "", "enabled": bool(r["enabled"]), **ssl(r),
            })
        for r in con.execute("SELECT * FROM redirection_host WHERE is_deleted = 0"):
            items.append({
                "key": f"redirect:{r['id']}", "type": "redirect", "npm_id": r["id"], "hosts": _j(r["domain_names"], []),
                "redirect_to": r["forward_domain_name"], "redirect_scheme": r["forward_scheme"] or "auto",
                "redirect_code": r["forward_http_code"] or 301, "preserve_path": bool(r["preserve_path"]),
                "block_exploits": bool(r["block_exploits"]), "advanced_config": r["advanced_config"] or "",
                "enabled": bool(r["enabled"]), **ssl(r),
            })
        for r in con.execute("SELECT * FROM dead_host WHERE is_deleted = 0"):
            items.append({
                "key": f"dead:{r['id']}", "type": "dead", "npm_id": r["id"], "hosts": _j(r["domain_names"], []),
                "advanced_config": r["advanced_config"] or "", "enabled": bool(r["enabled"]), **ssl(r),
            })
        streams = [
            {"key": f"stream:{r['id']}", "type": "stream", "npm_id": r["id"], "listen_port": r["incoming_port"],
             "upstream": f"{r['forwarding_host']}:{r['forwarding_port']}",
             "protocol": "both" if r["tcp_forwarding"] and r["udp_forwarding"] else ("udp" if r["udp_forwarding"] else "tcp"),
             "enabled": bool(r["enabled"])}
            for r in con.execute("SELECT * FROM stream WHERE is_deleted = 0")
        ]
        default_site = None
        try:
            row = con.execute("SELECT value, meta FROM setting WHERE id = 'default-site'").fetchone()
            if row:
                default_site = {"value": row["value"], "meta": _j(row["meta"], {})}
        except sqlite3.Error:
            pass
    finally:
        con.close()

    custom_certs = []
    for cid, c in certs.items():
        if c["provider"] != "other":
            continue
        d = p.parent / "custom_ssl" / f"npm-{cid}"
        custom_certs.append({"npm_id": cid, "name": c["nice_name"], "domains": _j(c["domain_names"], []),
                             "available": (d / "fullchain.pem").exists() and (d / "privkey.pem").exists(), "dir": str(d)})
    return {
        "items": items, "streams": streams, "access_lists": acls, "custom_certs": custom_certs, "default_site": default_site,
        "certificates": [{"npm_id": c["id"], "provider": c["provider"], "name": c["nice_name"],
                          "domains": _j(c["domain_names"], []), "expires_on": c["expires_on"]} for c in certs.values()],
    }


def public_view(data: dict[str, Any]) -> dict[str, Any]:
    """Bỏ mật khẩu access list trước khi trả về client."""
    out = dict(data)
    out["access_lists"] = [{**a, "auth": [{"username": u["username"]} for u in a["auth"]]} for a in data["access_lists"]]
    return out


# tương thích ngược
def read_npm_hosts(path: str | None = None) -> list[dict[str, Any]]:
    return read_npm(path)["items"]
