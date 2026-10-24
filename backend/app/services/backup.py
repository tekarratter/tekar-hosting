"""Backup / restore ứng dụng.

Một bản backup gồm:
  - vol-<name>.tar.gz     : named/anonymous volume của các container trong project
  - bind-<n>.tar.gz       : thư mục bind mount (tuỳ chọn)
  - sql-<container>.sql.gz: dump logic của DB nằm trong app (postgres/mysql) — tham khảo/khôi phục thủ công
  - pg-<db>.dump          : database trên Postgres dùng chung (pg_dump -Fc)
  - redis-<prefix>.jsonl  : key Redis dùng chung theo prefix
  - s3-<bucket>/          : object trong bucket dùng chung
  - config/               : compose + .env
  - manifest.json
"""

import asyncio
import base64
import gzip
import json
import os
import shutil
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import settings
from ..core.procenv import subprocess_env
from ..core.jobs import JobContext, job_handler
from ..core.security import decrypt
from ..db import SessionLocal
from ..core.store import storage_path
from ..models import App, Backup, BackupTarget, PlatformService, ServiceBinding
from . import backup_targets, compose, docker_svc, platform

HELPER = "alpine:3.20"
SKIP_BIND_PREFIX = ("/var/run", "/run", "/proc", "/sys", "/dev", "/etc", "/usr", "/lib", "/bin", "/sbin", "/boot")


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:120]


async def _tar_into(mount_src: str, is_volume: bool, out_dir: Path, filename: str) -> None:
    vols = {
        mount_src: {"bind": "/src", "mode": "ro"},
        str(out_dir): {"bind": "/backup", "mode": "rw"},
    }
    await docker_svc.run(docker_svc.helper_run, HELPER, ["tar", "czf", f"/backup/{filename}", "-C", "/src", "."], vols)


async def _untar_from(mount_src: str, in_dir: Path, filename: str) -> None:
    vols = {mount_src: {"bind": "/dst", "mode": "rw"}, str(in_dir): {"bind": "/backup", "mode": "ro"}}
    await docker_svc.run(
        docker_svc.helper_run, HELPER, ["sh", "-c", f"find /dst -mindepth 1 -delete; tar xzf /backup/{filename} -C /dst"], vols
    )


def _exec_dump(container, cmd: str, out_path: Path) -> int:
    exec_id = container.client.api.exec_create(container.id, ["sh", "-c", cmd], stdout=True, stderr=False)["Id"]
    stream = container.client.api.exec_start(exec_id, stream=True, demux=True)
    with gzip.open(out_path, "wb") as f:
        for out, _err in stream:
            if out:
                f.write(out)
    code = container.client.api.exec_inspect(exec_id).get("ExitCode")
    return code or 0


async def _dump_inline_dbs(containers, out: Path, ctx: JobContext, items: list) -> None:
    for c in containers:
        if c.status != "running":
            continue
        image = (c.attrs["Config"].get("Image") or "").lower()
        if "postgres" in image or "postgis" in image or "timescale" in image:
            cmd = 'pg_dumpall -U "${POSTGRES_USER:-postgres}"'
        elif "mysql" in image or "mariadb" in image:
            cmd = 'mysqldump --all-databases --single-transaction -uroot -p"${MYSQL_ROOT_PASSWORD:-$MARIADB_ROOT_PASSWORD}" 2>/dev/null || mariadb-dump --all-databases -uroot -p"$MARIADB_ROOT_PASSWORD"'
        else:
            continue
        fname = f"sql-{_safe(c.name)}.sql.gz"
        await ctx.log(f"Dump logic DB trong container {c.name}")
        code = await docker_svc.run(_exec_dump, c, cmd, out / fname)
        if code == 0:
            items.append({"type": "sql", "container": c.name, "file": fname})
        else:
            await ctx.log(f"  ⚠ dump {c.name} exit {code} (bỏ qua, volume vẫn được backup)")


async def _pg_dump(b: ServiceBinding, path: Path, ctx: JobContext) -> None:
    svc = b.service
    env = subprocess_env(PGPASSWORD=decrypt(b.password_enc))
    cmd = ["pg_dump", "-Fc", "-h", svc.host, "-p", str(svc.port), "-U", b.username, "-d", b.resource, "-f", str(path)]
    proc = await asyncio.create_subprocess_exec(*cmd, env=env, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump {b.resource}: {err.decode()[-800:]}")


async def _redis_dump(b: ServiceBinding, path: Path) -> int:
    r = platform._redis(b.service)
    n = 0
    try:
        with path.open("w") as f:
            async for key in r.scan_iter(match=f"{b.resource}:*", count=1000):
                data = await r.dump(key)
                if data is None:
                    continue
                ttl = await r.pttl(key)
                f.write(json.dumps({"k": base64.b64encode(key).decode(), "v": base64.b64encode(data).decode(), "ttl": max(ttl, 0)}) + "\n")
                n += 1
    finally:
        await r.aclose()
    return n


async def _redis_restore(b: ServiceBinding, path: Path) -> int:
    r = platform._redis(b.service)
    n = 0
    try:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            await r.restore(base64.b64decode(row["k"]), row["ttl"], base64.b64decode(row["v"]), replace=True)
            n += 1
    finally:
        await r.aclose()
    return n


def _s3_mirror_down(svc: PlatformService, bucket: str, dest: Path) -> int:
    mc = platform._minio(svc)
    n = 0
    for o in mc.list_objects(bucket, recursive=True):
        if o.is_dir:
            continue
        target = dest / o.object_name
        target.parent.mkdir(parents=True, exist_ok=True)
        mc.fget_object(bucket, o.object_name, str(target))
        n += 1
    return n


def _s3_mirror_up(svc: PlatformService, bucket: str, src: Path) -> int:
    mc = platform._minio(svc)
    n = 0
    for p in src.rglob("*"):
        if p.is_file():
            mc.fput_object(bucket, str(p.relative_to(src)), str(p))
            n += 1
    return n


def _dir_bytes(d: Path) -> int:
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())


@job_handler("backup")
async def run_backup(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as s:
        app = await s.get(App, params["app_id"])
        if app is None:
            raise RuntimeError("App không tồn tại")
        bindings = (await s.execute(select(ServiceBinding).where(ServiceBinding.app_id == app.id))).scalars().all()
        target = await s.get(PlatformService, app.backup_target_id) if app.backup_target_id else None
        dest = await s.get(BackupTarget, app.backup_destination_id) if app.backup_destination_id else None
        backups_root = await storage_path("backups", s)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = backups_root / app.slug / stamp
        bk = Backup(app_id=app.id, trigger=params.get("trigger", "manual"), path=str(out), status="running")
        s.add(bk)
        await s.commit()
        await s.refresh(bk)

    out.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    stopped = False
    try:
        containers = await docker_svc.project_containers(app.compose_project)
        await ctx.log(f"Backup {app.slug}: {len(containers)} container → {out}")

        await _dump_inline_dbs(containers, out, ctx, items)

        if app.backup_stop_app and containers:
            await ctx.log("Tạm dừng app để backup nhất quán")
            await compose.action(app, "stop", ctx)
            stopped = True

        seen: set[str] = set()
        bind_idx = 0
        for c in containers:
            for m in c.attrs.get("Mounts", []):
                if m.get("Type") == "volume":
                    name = m["Name"]
                    if name in seen:
                        continue
                    seen.add(name)
                    fname = f"vol-{_safe(name)}.tar.gz"
                    await ctx.log(f"Volume {name} ({m['Destination']})")
                    await _tar_into(name, True, out, fname)
                    items.append({"type": "volume", "name": name, "destination": m["Destination"], "container": c.name, "file": fname})
                elif m.get("Type") == "bind" and app.backup_include_binds:
                    src = m.get("Source") or ""
                    if src in seen or src.startswith(SKIP_BIND_PREFIX) or src.startswith((str(backups_root), str(settings.data_dir / "db"))):
                        continue
                    seen.add(src)
                    if not os.path.isdir(src):
                        continue  # chỉ backup thư mục; file đơn lẻ nằm trong config
                    bind_idx += 1
                    fname = f"bind-{bind_idx}-{_safe(Path(src).name)}.tar.gz"
                    await ctx.log(f"Bind {src} ({m['Destination']})")
                    await _tar_into(src, False, out, fname)
                    items.append({"type": "bind", "source": src, "destination": m["Destination"], "container": c.name, "file": fname})

        cfg = out / "config"
        cfg.mkdir(exist_ok=True)
        for f in compose.compose_files(app):
            if os.path.isfile(f):
                shutil.copy2(f, cfg / Path(f).name)
        env_file = Path(app.work_dir) / ".env"
        if env_file.is_file():
            shutil.copy2(env_file, cfg / ".env")
        items.append({"type": "config", "file": "config"})

        for b in bindings:
            if not b.include_in_backup:
                continue
            kind = b.service.kind
            if kind == "postgres":
                fname = f"pg-{b.resource}.dump"
                await ctx.log(f"pg_dump {b.resource}@{b.service.name}")
                await _pg_dump(b, out / fname, ctx)
                items.append({"type": "postgres", "binding_id": b.id, "resource": b.resource, "file": fname})
            elif kind == "redis":
                fname = f"redis-{b.resource}.jsonl"
                n = await _redis_dump(b, out / fname)
                await ctx.log(f"Redis {b.resource}:* → {n} key")
                items.append({"type": "redis", "binding_id": b.id, "resource": b.resource, "file": fname, "keys": n})
            elif kind == "s3":
                d = out / f"s3-{b.resource}"
                d.mkdir(exist_ok=True)
                n = await docker_svc.run(_s3_mirror_down, b.service, b.resource, d)
                await ctx.log(f"S3 bucket {b.resource} → {n} object")
                items.append({"type": "s3", "binding_id": b.id, "resource": b.resource, "file": d.name, "objects": n})

        (out / "manifest.json").write_text(
            json.dumps({"app": app.slug, "created": stamp, "project": app.compose_project, "items": items}, indent=2, ensure_ascii=False)
        )
        size = await asyncio.to_thread(_dir_bytes, out)

        remote_key = None
        if dest is not None:
            rel = f"{app.slug}/{stamp}.tar.gz"
            await ctx.log(f"Nén và đẩy lên đích '{dest.name}' ({dest.kind}): {rel}")
            archive = out.parent / f"{stamp}.tar.gz"
            await asyncio.to_thread(shutil.make_archive, str(archive)[:-7], "gztar", out)
            try:
                await asyncio.to_thread(backup_targets.upload, dest, str(archive), rel)
            finally:
                archive.unlink(missing_ok=True)
            remote_key = f"target:{dest.id}:{rel}"
            if not app.backup_keep_local:
                await ctx.log("Không giữ bản cục bộ (chỉ lưu ở đích)")
                await asyncio.to_thread(shutil.rmtree, out, True)
        elif target is not None:
            bucket = (target.extra or {}).get("backup_bucket") or "th-backups"
            prefix = f"{app.slug}/{stamp}"
            await ctx.log(f"Đẩy lên {target.name}:{bucket}/{prefix}")
            archive = out.parent / f"{stamp}.tar.gz"
            await asyncio.to_thread(shutil.make_archive, str(archive)[:-7], "gztar", out)
            await docker_svc.run(platform.s3_upload, target, bucket, f"{prefix}.tar.gz", str(archive))
            archive.unlink(missing_ok=True)
            remote_key = f"{bucket}/{prefix}.tar.gz"

        async with SessionLocal() as s:
            row = await s.get(Backup, bk.id)
            row.status, row.items, row.size_bytes, row.remote_key = "success", items, size, remote_key
            row.finished_at = datetime.now(UTC)
            await s.commit()
        await ctx.log(f"Xong: {size / 1024 / 1024:.1f} MB, {len(items)} mục")
        await prune_backups(app.id, app.backup_retention, ctx)
        return {"backup_id": bk.id, "size": size}
    except Exception as e:
        async with SessionLocal() as s:
            row = await s.get(Backup, bk.id)
            row.status, row.error, row.items = "failed", str(e)[:4000], items
            row.finished_at = datetime.now(UTC)
            await s.commit()
        raise
    finally:
        if stopped:
            await ctx.log("Khởi động lại app")
            await compose.action(app, "start", ctx)


async def prune_backups(app_id: int, keep: int, ctx: JobContext | None = None) -> None:
    if keep <= 0:
        return
    async with SessionLocal() as s:
        rows = (
            await s.execute(select(Backup).where(Backup.app_id == app_id, Backup.status == "success").order_by(Backup.started_at.desc()))
        ).scalars().all()
        for old in rows[keep:]:
            await delete_backup_files(old, s)
            await s.delete(old)
            if ctx:
                await ctx.log(f"Xoá backup cũ #{old.id}")
        await s.commit()


def _safe_unpack(archive: Path, dest: Path) -> None:
    """Giải nén tar.gz với filter 'data': bỏ đường dẫn tuyệt đối/'..', symlink trỏ ra ngoài, setuid..."""
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="data")


def _deletable(p: Path, root: Path) -> bool:
    """Chỉ xoá thư mục <root>/<slug>/<stamp> (đúng 2 cấp dưới gốc backup) — không bao giờ xoá gốc hay thư mục slug."""
    try:
        rel = p.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return len(rel.parts) == 2


async def delete_backup_files(b: Backup, s) -> None:
    p = Path(b.path) if b.path else None
    # chỉ xoá thư mục đúng cấu trúc <root>/<slug>/<stamp> có manifest, tránh xoá nhầm khi đổi thư mục lưu trữ
    if p and p.is_dir() and ((p / "manifest.json").exists() or _deletable(p, settings.backups_dir)):
        await asyncio.to_thread(shutil.rmtree, p, True)
    if b.remote_key and b.remote_key.startswith("target:"):
        _, tid, rel = b.remote_key.split(":", 2)
        t = await s.get(BackupTarget, int(tid))
        if t:
            try:
                await asyncio.to_thread(backup_targets.delete, t, rel)
            except Exception:  # noqa: BLE001
                pass
    elif b.remote_key:
        app = await s.get(App, b.app_id)
        if app and app.backup_target_id:
            target = await s.get(PlatformService, app.backup_target_id)
            bucket, _, key = b.remote_key.partition("/")
            try:
                await docker_svc.run(platform.s3_delete, target, bucket, key)
            except Exception:  # noqa: BLE001
                pass


@job_handler("restore")
async def run_restore(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    only: set[str] | None = set(params["items"]) if params.get("items") else None
    async with SessionLocal() as s:
        bk = await s.get(Backup, params["backup_id"])
        app = await s.get(App, bk.app_id)
        bindings = {b.id: b for b in (await s.execute(select(ServiceBinding).where(ServiceBinding.app_id == app.id))).scalars()}
        target = await s.get(PlatformService, app.backup_target_id) if app.backup_target_id else None
        dest = None
        if bk.remote_key and bk.remote_key.startswith("target:"):
            dest = await s.get(BackupTarget, int(bk.remote_key.split(":", 2)[1]))
    src = Path(bk.path)
    if not src.is_dir() and dest is not None:
        rel = bk.remote_key.split(":", 2)[2]
        await ctx.log(f"Tải backup từ đích '{dest.name}': {rel}")
        src.mkdir(parents=True, exist_ok=True)
        archive = src.parent / Path(rel).name
        await asyncio.to_thread(backup_targets.download, dest, rel, str(archive))
        await asyncio.to_thread(_safe_unpack, archive, src)
        archive.unlink(missing_ok=True)
    if not src.is_dir():
        if not (bk.remote_key and target):
            raise RuntimeError("Không còn file backup cục bộ và không có bản remote")
        await ctx.log(f"Tải backup từ {bk.remote_key}")
        src.mkdir(parents=True, exist_ok=True)
        bucket, _, key = bk.remote_key.partition("/")
        archive = src.parent / Path(key).name
        await docker_svc.run(platform.s3_download, target, bucket, key, str(archive))
        await asyncio.to_thread(_safe_unpack, archive, src)
        archive.unlink(missing_ok=True)

    items = bk.items or []
    if not items and (src / "manifest.json").exists():
        items = json.loads((src / "manifest.json").read_text()).get("items", [])
        async with SessionLocal() as s:
            row = await s.get(Backup, bk.id)
            row.items = items
            await s.commit()
    await ctx.log(f"Restore backup #{bk.id} cho {app.slug} — dừng app")
    await compose.action(app, "stop", ctx)
    restored = []
    try:
        for it in items:
            if only is not None and it["file"] not in only:
                continue
            t = it["type"]
            if t == "volume":
                await ctx.log(f"Khôi phục volume {it['name']}")
                await docker_svc.ensure_network(settings.edge_network)
                await _untar_from(it["name"], src, it["file"])
            elif t == "bind":
                await ctx.log(f"Khôi phục thư mục {it['source']}")
                Path(it["source"]).mkdir(parents=True, exist_ok=True)
                await _untar_from(it["source"], src, it["file"])
            elif t == "postgres":
                b = bindings.get(it["binding_id"])
                if not b:
                    await ctx.log(f"⚠ bỏ qua {it['resource']}: binding không còn")
                    continue
                await ctx.log(f"pg_restore {it['resource']}")
                env = subprocess_env(PGPASSWORD=decrypt(b.password_enc))
                cmd = ["pg_restore", "--clean", "--if-exists", "--no-owner", "-h", b.service.host, "-p", str(b.service.port),
                       "-U", b.username, "-d", b.resource, str(src / it["file"])]
                proc = await asyncio.create_subprocess_exec(*cmd, env=env, stderr=asyncio.subprocess.PIPE)
                _, err = await proc.communicate()
                if proc.returncode not in (0, 1):
                    raise RuntimeError(err.decode()[-800:])
                if err:
                    await ctx.log(err.decode()[-600:])
            elif t == "redis":
                b = bindings.get(it["binding_id"])
                if b:
                    n = await _redis_restore(b, src / it["file"])
                    await ctx.log(f"Redis: khôi phục {n} key")
            elif t == "s3":
                b = bindings.get(it["binding_id"])
                if b:
                    n = await docker_svc.run(_s3_mirror_up, b.service, b.resource, src / it["file"])
                    await ctx.log(f"S3: đẩy lại {n} object")
            else:
                continue
            restored.append(it["file"])
    finally:
        await ctx.log("Khởi động lại app")
        await compose.action(app, "start", ctx)
    return {"restored": restored}
