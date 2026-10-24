"""Sao lưu chính panel: DB Postgres của panel (pg_dump -Fc trong container th-db), thư mục cert/cấu hình Caddy
và .env (chứa TH_SECRET_KEY — thiếu nó không giải mã được secret trong DB khi khôi phục).

Kết quả: <backups>/_panel/panel-<stamp>.tar.gz (chmod 600), tuỳ chọn đẩy lên một backup target.
Khôi phục (thủ công, trên panel mới cài cùng phiên bản):
  tar xzf panel-<stamp>.tar.gz
  docker exec -i th-db pg_restore -U th -d th --clean --if-exists < th-db.dump
  cp -a caddy/. <TH_DATA_DIR>/caddy/   &&   cp env <project>/.env   &&   docker compose up -d --force-recreate
"""

import asyncio
import os
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from ..config import settings
from ..core.jobs import JobContext, job_handler
from ..core.store import get_setting
from ..db import SessionLocal
from ..models import BackupTarget
from . import backup_targets, docker_svc

DIR = "_panel"
PREFIX = "panel-"


def local_dir() -> Path:
    return settings.backups_dir / DIR


def list_local() -> list[dict[str, Any]]:
    d = local_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob(f"{PREFIX}*.tar.gz"), reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "mtime": datetime.fromtimestamp(st.st_mtime, UTC).isoformat()})
    return out


def _pg_dump(container, user: str, dbname: str, out_path: Path) -> int:
    exec_id = container.client.api.exec_create(container.id, ["pg_dump", "-U", user, "-Fc", dbname], stdout=True, stderr=False)["Id"]
    with out_path.open("wb") as f:
        for out, _err in container.client.api.exec_start(exec_id, stream=True, demux=True):
            if out:
                f.write(out)
    return container.client.api.exec_inspect(exec_id).get("ExitCode") or 0


def _project_env() -> Path | None:
    """.env của panel: cạnh docker-compose.yml trong thư mục dự án (cùng đường dẫn tuyệt đối trong container)."""
    for cand in (settings.data_dir.parent / ".env", Path.cwd() / ".env"):
        if cand.is_file():
            return cand
    return None


def _pack(work: Path, caddy_dir: Path, env_file: Path | None, archive: Path) -> None:
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(work / "th-db.dump", arcname="th-db.dump")
        if caddy_dir.is_dir():
            tf.add(caddy_dir, arcname="caddy", filter=lambda ti: None if ti.name.endswith(("/logs", "/logs/access.log")) or "/logs/" in ti.name else ti)
        if env_file:
            tf.add(env_file, arcname="env")
    os.chmod(archive, 0o600)


@job_handler("panel_backup")
async def job_panel_backup(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    u = urlparse(settings.database_url)
    db_container, db_user, db_name = u.hostname or "th-db", u.username or "th", (u.path or "/th").lstrip("/")
    c = await docker_svc.get_container(db_container)
    if c is None:
        raise RuntimeError(f"Không thấy container DB của panel ({db_container})")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dest = local_dir()
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    archive = dest / f"{PREFIX}{stamp}.tar.gz"
    work = Path(tempfile.mkdtemp(prefix="panel-backup-", dir=dest))
    try:
        await ctx.log(f"pg_dump {db_name}@{db_container}")
        code = await docker_svc.run(_pg_dump, c, db_user, db_name, work / "th-db.dump")
        if code or (work / "th-db.dump").stat().st_size == 0:
            raise RuntimeError(f"pg_dump thất bại (exit {code})")
        caddy_dir = settings.data_dir / "caddy"
        env_file = _project_env()
        await ctx.log(f"Đóng gói: th-db.dump, {caddy_dir}{', .env' if env_file else ''} → {archive.name}")
        await asyncio.to_thread(_pack, work, caddy_dir, env_file, archive)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    size = archive.stat().st_size
    result: dict[str, Any] = {"file": str(archive), "size": size}

    async with SessionLocal() as s:
        keep = int(await get_setting("panel_backup_keep", s) or 7)
        tid = await get_setting("panel_backup_target_id", s)
        target = await s.get(BackupTarget, int(tid)) if tid else None
    if target is not None:
        rel = f"{DIR}/{archive.name}"
        await ctx.log(f"Đẩy lên đích '{target.name}': {rel}")
        await asyncio.to_thread(backup_targets.upload, target, str(archive), rel)
        result["remote"] = rel

    old = sorted(dest.glob(f"{PREFIX}*.tar.gz"))[:-keep] if keep > 0 else []
    for p in old:
        p.unlink(missing_ok=True)
        if target is not None:
            try:
                await asyncio.to_thread(backup_targets.delete, target, f"{DIR}/{p.name}")
            except Exception as e:  # noqa: BLE001
                await ctx.log(f"⚠ không xoá được bản cũ trên đích: {p.name}: {e}")
    if old:
        await ctx.log(f"Xoá {len(old)} bản cũ, giữ {keep} bản gần nhất")
    await ctx.log(f"Xong: {archive.name} ({size / 1048576:.1f} MB)")
    return result


async def targets_for_ui() -> list[dict[str, Any]]:
    async with SessionLocal() as s:
        return [{"id": t.id, "name": t.name, "kind": t.kind} for t in (await s.execute(select(BackupTarget))).scalars()]
