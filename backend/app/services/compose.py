"""Vận hành dự án docker compose cho App (managed + adopted).

Vendor-free: app managed là một thư mục compose bình thường, ổ đĩa là nguồn sự thật:
  <work_dir>/docker-compose.yml           — người dùng sửa (panel hoặc trực tiếp)
  <work_dir>/.env                         — biến của người dùng + khối biến dịch vụ nền tảng (tự sinh)
  <work_dir>/docker-compose.override.yml  — panel sinh: label, giới hạn tài nguyên, mạng edge, volume dạng thư mục
  <work_dir>/README.tekar.md              — cách tự vận hành không cần panel
`docker compose up -d` trong thư mục là chạy được, không phụ thuộc panel.
App adopted: file override nằm trong thư mục riêng của panel để không đụng vào dự án gốc."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import yaml

from ..config import settings
from ..core.jobs import JobContext
from ..core.procenv import subprocess_env
from ..core.security import decrypt
from ..models import App

LEGACY_OVERRIDE = "compose.th.yml"
ENV_MARKER = "# --- Tekar Hosting: dịch vụ nền tảng (tự sinh) ---"


def app_dir(app: App) -> Path:
    """Managed: thư mục của app. Adopted: thư mục phụ của panel (chỉ chứa override)."""
    if app.kind == "managed":
        return Path(app.work_dir)
    return settings.apps_dir / app.slug


def override_path(app: App) -> Path:
    if app.kind == "managed":
        return Path(app.work_dir) / "docker-compose.override.yml"
    return settings.apps_dir / app.slug / LEGACY_OVERRIDE


def compose_files(app: App) -> list[str]:
    if app.kind == "managed":
        files = [str(Path(app.work_dir) / "docker-compose.yml")]
    else:
        files = list(app.compose_files)
    ov = override_path(app)
    if ov.exists():
        files.append(str(ov))
    return files


def base_cmd(app: App, with_override: bool = True) -> list[str]:
    cmd = ["docker", "compose", "-p", app.compose_project, "--project-directory", app.work_dir]
    ov = str(override_path(app))
    for f in compose_files(app):
        if not with_override and f == ov:
            continue
        cmd += ["-f", f]
    return cmd


def split_env(text: str) -> str:
    """Bỏ khối biến tự sinh, trả về phần người dùng viết."""
    return text.split(ENV_MARKER, 1)[0].rstrip("\n") + "\n" if text.strip() else ""


def sync_from_disk(app: App) -> bool:
    """Đọc compose/.env trên đĩa vào bản sao trong DB (người dùng có thể sửa file trực tiếp). True nếu có thay đổi."""
    from ..core.security import encrypt

    if app.kind != "managed":
        return False
    d = Path(app.work_dir)
    changed = False
    cf = d / "docker-compose.yml"
    if cf.is_file():
        text = cf.read_text()
        if text != app.compose_yaml:
            app.compose_yaml = text
            changed = True
    ef = d / ".env"
    if ef.is_file():
        user_env = split_env(ef.read_text())
        if user_env != decrypt(app.env_enc):
            app.env_enc = encrypt(user_env)
            changed = True
    return changed


README = """# {name}

Ứng dụng được tạo bởi Tekar Hosting nhưng là một dự án Docker Compose bình thường —
có thể tự vận hành không cần panel.

```bash
cd {dir}
docker compose -p {project} up -d        # chạy (tự nạp docker-compose.override.yml)
docker compose -p {project} logs -f      # xem log
docker compose -p {project} down         # dừng
```

| File | Nội dung |
|---|---|
| `docker-compose.yml` | Định nghĩa dịch vụ (sửa tự do) |
| `.env` | Biến môi trường. Khối dưới dòng "Tekar Hosting: dịch vụ nền tảng" do panel sinh lại mỗi lần deploy |
| `docker-compose.override.yml` | Panel sinh: label, giới hạn CPU/RAM, mạng `{edge}`, volume dạng thư mục |
{extra}
Mang sang máy khác: chép cả thư mục{data_note}, tạo mạng `docker network create {edge}`
(hoặc xoá phần networks trong override), rồi `docker compose up -d`.
"""


def write_readme(app: App) -> None:
    extra = ""
    if app.volumes_dir:
        extra += f"\nDữ liệu volume lưu dạng thư mục tại `{app.volumes_dir}`.\n"
    if app.data_dir:
        extra += f"\nThư mục dữ liệu mount: `{app.data_dir}` (biến `APP_DATA_DIR` trong .env).\n"
    note = " và các thư mục dữ liệu ở trên" if (app.volumes_dir or app.data_dir) else ""
    (Path(app.work_dir) / "README.tekar.md").write_text(
        README.format(name=app.name, dir=app.work_dir, project=app.compose_project, edge=settings.edge_network, extra=extra, data_note=note))


async def run_cmd(cmd: list[str], ctx: JobContext | None = None, cwd: str | None = None, check: bool = True) -> tuple[int, str]:
    if ctx:
        await ctx.log("$ " + " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=subprocess_env(COMPOSE_ANSI="never")
    )
    out: list[str] = []
    assert proc.stdout
    async for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        out.append(line)
        if ctx:
            await ctx.log(line)
    code = await proc.wait()
    text = "\n".join(out)
    if check and code != 0:
        raise RuntimeError(f"Lệnh thất bại (exit {code}): {' '.join(cmd[:6])}…\n{text[-1500:]}")
    return code, text


def fix_owner(app: App) -> None:
    """File do panel (root) ghi → đổi owner theo thư mục cha của thư mục app, để người dùng trên host sửa được."""
    if app.kind != "managed":
        return
    d = Path(app.work_dir)
    owner = storage_owner(d)
    if owner is None:
        return
    uid, gid = owner
    paths = [d.parent, d, *d.iterdir()] if d.exists() else []
    for p in paths:
        try:
            if p.is_symlink():
                continue
            st = p.stat()
            if st.st_uid == 0 and (st.st_uid, st.st_gid) != (uid, gid):
                os.chown(p, uid, gid)
        except OSError:
            pass


def storage_owner(start: Path) -> tuple[int, int] | None:
    """uid:gid cho file của app: biến TH_STORAGE_OWNER ("1002:1003") hoặc owner của thư mục tổ tiên gần nhất không phải root."""
    raw = os.environ.get("TH_STORAGE_OWNER", "").strip()
    if raw:
        u, _, g = raw.partition(":")
        return int(u), int(g or u)
    for p in [start, *start.parents]:
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_uid != 0:
            return st.st_uid, st.st_gid
    return None


def platform_network(app: App) -> str:
    """Mạng riêng của app để tới dịch vụ nền tảng: chỉ service của app này + container postgres/redis/s3 dùng chung
    (không dùng mạng chung vì alias tên service như `db`, `redis` sẽ trỏ nhầm sang app khác)."""
    return f"th-svc-{app.slug}"


def write_compose(app: App) -> None:
    d = Path(app.work_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "docker-compose.yml").write_text(app.compose_yaml)


def write_env(app: App, service_env: dict[str, str] | None = None) -> None:
    d = Path(app.work_dir)
    d.mkdir(parents=True, exist_ok=True)
    env_text = decrypt(app.env_enc).rstrip("\n") + "\n"
    gen = dict(service_env or {})
    # stack name: `docker compose up -d` chạy tay trong thư mục dùng đúng project như panel (không sinh stack trùng)
    gen["COMPOSE_PROJECT_NAME"] = app.compose_project
    gen["APP_SLUG"] = app.slug
    if app.data_dir:
        gen["APP_DATA_DIR"] = app.data_dir
    if gen:
        env_text += f"\n{ENV_MARKER}\n" + "".join(f"{k}={v}\n" for k, v in gen.items())
    env_path = d / ".env"
    env_path.write_text(env_text.lstrip("\n"))
    env_path.chmod(0o600)


def write_managed_files(app: App, service_env: dict[str, str] | None = None) -> None:
    write_compose(app)
    write_env(app, service_env)
    write_readme(app)
    fix_owner(app)


def validate_yaml(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML không hợp lệ: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict) or not data["services"]:
        raise ValueError("Compose phải có khối 'services'")
    return data


async def normalized_config(app: App) -> dict[str, Any]:
    _, out = await run_cmd(base_cmd(app, with_override=False) + ["config", "--format", "json"])
    return json.loads(out[out.index("{"):])


async def write_override(app: App, svc_network: str | None = None) -> None:
    """Sinh compose.th.yml: label th.app, giới hạn tài nguyên, gắn mạng edge + mạng dịch vụ nền tảng."""
    cfg = await normalized_config(app)
    services: dict[str, Any] = {}
    for name, svc in (cfg.get("services") or {}).items():
        o: dict[str, Any] = {"labels": {"th.app": app.slug}}
        lim = (app.service_limits or {}).get(name) or {}
        cpus = lim.get("cpus") or None
        mem = lim.get("memory_mb") or None
        if cpus or mem:
            limits: dict[str, Any] = {}
            if cpus:
                limits["cpus"] = str(cpus)
            if mem:
                limits["memory"] = f"{int(mem)}M"
            o["deploy"] = {"resources": {"limits": limits}}
        if not svc.get("network_mode"):
            extra = ([settings.edge_network] if name in (app.edge_services or []) else []) + ([svc_network] if svc_network else [])
            if extra:
                nets = svc.get("networks") or {"default": None}
                o["networks"] = {**{k: v for k, v in nets.items()}, **{n: {} for n in extra}}
        services[name] = o
    doc: dict[str, Any] = {"services": services}
    used = {n for s in services.values() for n in (s.get("networks") or {})}
    ext = [n for n in (settings.edge_network, svc_network) if n and n in used]
    if ext:
        doc["networks"] = {n: {"external": True, "name": n} for n in ext}
    # volume dạng thư mục (vendor-free): dữ liệu nằm ở <volumes_dir>/<tên volume>, xem/chép trực tiếp được
    if app.volumes_dir:
        vols: dict[str, Any] = {}
        for vname, vdef in (cfg.get("volumes") or {}).items():
            if (vdef or {}).get("external") or (vdef or {}).get("driver") not in (None, "local") or (vdef or {}).get("driver_opts"):
                continue
            path = Path(app.volumes_dir) / vname
            path.mkdir(parents=True, exist_ok=True)
            vols[vname] = {"driver": "local", "driver_opts": {"type": "none", "o": "bind", "device": str(path)}}
        if vols:
            doc["volumes"] = vols
    if app.data_dir:
        Path(app.data_dir).mkdir(parents=True, exist_ok=True)
    ov = override_path(app)
    ov.parent.mkdir(parents=True, exist_ok=True)
    ov.write_text("# Sinh tự động bởi Tekar Hosting mỗi lần deploy — chỉnh trong panel (tab Tài nguyên/Domain)\n"
                  + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    if app.kind == "managed":
        (Path(app.work_dir) / LEGACY_OVERRIDE).unlink(missing_ok=True)
        fix_owner(app)


async def deploy(app: App, ctx: JobContext, pull: bool = True, service_env: dict[str, str] | None = None, build: bool = False,
                 svc_network: str | None = None) -> None:
    if app.kind == "adopted" and not files_accessible(app):
        raise RuntimeError("Không truy cập được file compose (app do Dockhand/Portainer quản lý) — hãy deploy từ công cụ đó")
    if app.kind == "managed":
        if not (Path(app.work_dir) / "docker-compose.yml").is_file():
            write_compose(app)
        write_env(app, service_env)
        write_readme(app)
    await write_override(app, svc_network)
    if pull:
        await run_cmd(base_cmd(app) + ["pull", "--ignore-buildable", "--quiet"], ctx, check=False)
    await run_cmd(base_cmd(app) + ["up", "-d", "--remove-orphans"] + (["--build"] if build else []), ctx)


def files_accessible(app: App) -> bool:
    if app.kind == "managed":
        return True
    # Dockhand chạy compose qua stdin ("-") → không biết file gốc, coi như không truy cập được
    files = [f for f in app.compose_files if f]
    if not files or "-" in files:
        return False
    return all(os.path.isfile(f) for f in compose_files(app)) and os.path.isdir(app.work_dir)


async def _container_fallback(app: App, verb: str, ctx: JobContext | None) -> str:
    """Không đọc được file compose (stack của Dockhand/Portainer) → thao tác trực tiếp qua Docker API."""
    from . import docker_svc

    if verb not in ("start", "stop", "restart"):
        raise RuntimeError("Không truy cập được file compose của app này (được quản lý bởi công cụ khác) — chỉ hỗ trợ start/stop/restart")
    cs = await docker_svc.project_containers(app.compose_project)
    for c in cs:
        if ctx:
            await ctx.log(f"docker {verb} {c.name}")
        await docker_svc.run(getattr(c, verb))
    return f"{verb} {len(cs)} container"


async def action(app: App, verb: str, ctx: JobContext | None = None) -> str:
    if app.kind == "adopted" and not files_accessible(app):
        return await _container_fallback(app, verb, ctx)
    args = {
        "start": ["start"],
        "stop": ["stop"],
        "restart": ["restart"],
        "down": ["down", "--remove-orphans"],
        "pull": ["pull", "--ignore-buildable"],
        "ps": ["ps", "--all", "--format", "json"],
    }[verb]
    _, out = await run_cmd(base_cmd(app) + args, ctx)
    return out
