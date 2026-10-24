"""App loại "devbox": môi trường phát triển có user SSH + thư mục làm việc + thông tin dịch vụ dùng chung.

Một container sshd (image tekar-devbox, panel tự build từ thư mục devbox/) publish cổng SSH riêng trên host:
  <work_dir>/home/<user>/workspace  — thư mục làm việc (bind mount /home)
  <work_dir>/authorized_keys        — public key được phép đăng nhập (sửa xong restart app)
  .env: DEV_USER, DEV_PASSWORD, DEV_SUDO, DEV_UID, SSH_PORT + khối biến dịch vụ nền tảng (DATABASE_URL, REDIS_URL, S3_*…)
Container nằm trên mạng th-svc-<slug> như mọi app nên tới được postgres/redis/s3 dùng chung bằng tên container.
"""

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..core.jobs import JobContext
from ..core.security import decrypt
from ..core.store import get_setting
from ..models import App
from . import docker_svc

TEMPLATE_ID = "devbox"
IMAGE = "tekar-devbox:1"  # đổi tag khi sửa devbox/ để panel build lại
BUILD_DIR = Path(__file__).resolve().parent.parent / "devbox"
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
RESERVED = {"root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy", "www-data",
            "backup", "list", "irc", "gnats", "nobody", "sshd", "systemd-network", "messagebus", "_apt", "postgres", "redis"}
KEY_RE = re.compile(r"^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-nistp\d+|sk-[a-z0-9@.-]+) [A-Za-z0-9+/=]+( .*)?$")

COMPOSE = f"""services:
  dev:
    image: {IMAGE}
    pull_policy: never  # image build tại chỗ bởi panel
    restart: unless-stopped
    hostname: ${{APP_SLUG}}
    env_file: .env
    ports:
      - "${{SSH_PORT}}:22"
    volumes:
      - ./home:/home
      - ./authorized_keys:/etc/th/authorized_keys.src:ro
"""


def is_devbox(app: App) -> bool:
    return app.template == TEMPLATE_ID


def check_user(name: str) -> str:
    name = (name or "").strip()
    if not USER_RE.match(name) or name in RESERVED:
        raise ValueError("Tên user SSH: a-z, 0-9, '_' '-' (bắt đầu bằng chữ, tối đa 32 ký tự), không trùng user hệ thống")
    return name


def check_password(pw: str) -> str:
    if pw and (len(pw) < 8 or re.search(r"['\n\r\\]", pw)):
        raise ValueError("Mật khẩu SSH tối thiểu 8 ký tự, không chứa dấu nháy đơn, \\ hoặc xuống dòng")
    return pw


def clean_keys(text: str) -> str:
    keys = [ln.strip() for ln in (text or "").splitlines() if ln.strip() and not ln.strip().startswith("#")]
    bad = [k[:30] for k in keys if not KEY_RE.match(k)]
    if bad:
        raise ValueError(f"Public key không hợp lệ: {bad[0]}…")
    return "".join(k + "\n" for k in keys)


def env_get(text: str, key: str) -> str | None:
    m = re.search(rf"(?m)^{key}=(.*)$", text)
    if not m:
        return None
    v = m.group(1).strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v


def env_set(text: str, key: str, value: str) -> str:
    """Đặt biến trong phần .env người dùng (giá trị bọc nháy đơn để compose không nội suy $)."""
    line = f"{key}='{value}'" if value else f"{key}="
    text, n = re.subn(rf"(?m)^{key}=.*$", lambda _: line, text)
    return text if n else text.rstrip("\n") + f"\n{line}\n"


def build_env(user: str, password: str, sudo: bool, uid: int, port: int) -> str:
    env = ""
    for k, v in (("DEV_USER", user), ("DEV_PASSWORD", password), ("DEV_SUDO", "true" if sudo else "false"),
                 ("DEV_UID", str(uid)), ("SSH_PORT", str(port))):
        env = env_set(env, k, v)
    return env


async def _ports_range() -> range:
    raw = str(await get_setting("dev_ssh_ports") or "2201-2299")
    try:
        a, _, b = raw.partition("-")
        lo, hi = int(a), int(b or a)
    except ValueError:
        lo, hi = 2201, 2299
    return range(max(1, lo), min(65535, hi) + 1)


async def alloc_port(db, exclude_app: int | None = None) -> int:
    """Cổng SSH trống đầu tiên trong dải dev_ssh_ports: chưa app devbox nào giữ, không container/tiến trình host nào chiếm."""
    from .caddy_ports import _host_listening

    used: set[int] = set()
    for a in (await db.execute(select(App).where(App.template == TEMPLATE_ID))).scalars():
        if a.id != exclude_app:
            p = env_get(decrypt(a.env_enc), "SSH_PORT")
            if p and p.isdigit():
                used.add(int(p))
    for c in await docker_svc.list_containers():
        used.update(int(p["host_port"]) for p in c["ports"] if p.get("host_port"))
    used.update(p for (p, proto) in (await docker_svc.run(_host_listening)) if proto == "tcp")
    for p in await _ports_range():
        if p not in used:
            return p
    raise ValueError("Hết cổng SSH trống trong dải dev_ssh_ports (Cài đặt)")


def prepare_dir(app: App, keys: str | None = None) -> None:
    """Tạo sẵn home/ và authorized_keys (thiếu file thì docker tạo thư mục thay vào chỗ đó)."""
    d = Path(app.work_dir)
    (d / "home").mkdir(parents=True, exist_ok=True)
    kf = d / "authorized_keys"
    if keys is not None or not kf.exists():
        kf.write_text(keys or "")
        kf.chmod(0o644)


async def ensure_image(ctx: JobContext | None = None) -> None:
    def _has() -> bool:
        try:
            docker_svc.client().images.get(IMAGE)
            return True
        except Exception:  # noqa: BLE001
            return False

    if await docker_svc.run(_has):
        return
    if ctx:
        await ctx.log(f"Build image {IMAGE} (lần đầu, vài phút)…")

    def _build() -> None:
        docker_svc.client().images.build(path=str(BUILD_DIR), tag=IMAGE, rm=True, pull=True, labels={"th.devbox": "1"})

    await docker_svc.run(_build)
    if ctx:
        await ctx.log(f"Đã build {IMAGE}")


async def info(app: App) -> dict[str, Any]:
    env = decrypt(app.env_enc)
    kf = Path(app.work_dir) / "authorized_keys"
    try:
        keys = kf.read_text()
    except OSError:
        keys = ""
    user, port = env_get(env, "DEV_USER") or "dev", env_get(env, "SSH_PORT") or ""
    host = (await get_setting("dev_ssh_host") or "").strip()
    return {
        "user": user, "port": int(port) if port.isdigit() else None, "host": host or None,
        "password_auth": bool(env_get(env, "DEV_PASSWORD")), "sudo": env_get(env, "DEV_SUDO") == "true",
        "keys": keys, "workspace": f"/home/{user}/workspace", "host_dir": str(Path(app.work_dir) / "home" / user),
    }
