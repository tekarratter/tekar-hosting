"""Job clone app: chép thư mục app, dữ liệu volume / APP_DATA_DIR, dữ liệu dịch vụ nền tảng (postgres/redis/s3), rồi deploy."""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import settings
from ..core.procenv import subprocess_env
from ..core.jobs import JobContext, job_handler
from ..core.security import decrypt
from ..db import SessionLocal
from ..models import App, ServiceBinding
from . import compose, docker_svc, git, platform
from .app_jobs import job_deploy, job_git_update
from .backup import HELPER, _s3_mirror_down, _s3_mirror_up

# file panel sinh / file compose của app mới — không chép đè
GENERATED = ("docker-compose.override.yml", compose.LEGACY_OVERRIDE, "README.tekar.md", ".env")


async def _copy_dir(src: str, dst: str, exclude: list[str], ctx: JobContext) -> None:
    """Chép bằng container tạm (tar) để giữ owner/quyền của file do container tạo."""
    Path(dst).mkdir(parents=True, exist_ok=True)
    ex = " ".join(f"--exclude='./{e}'" for e in exclude)
    await ctx.log(f"Chép {src} → {dst}")
    await docker_svc.run(docker_svc.helper_run, HELPER, ["sh", "-c", f"cd /from && tar cf - {ex} . | tar xf - -C /to"],
                         {src: {"bind": "/from", "mode": "ro"}, dst: {"bind": "/to", "mode": "rw"}})


async def _copy_volumes(src: App, app: App, ctx: JobContext) -> None:
    cfg = await compose.normalized_config(src)
    cli = docker_svc.client()
    for vname, vdef in (cfg.get("volumes") or {}).items():
        vdef = vdef or {}
        if vdef.get("external"):
            await ctx.log(f"Volume {vname}: external → dùng chung, không chép")
            continue
        src_name = vdef.get("name") or f"{src.compose_project}_{vname}"
        try:
            await docker_svc.run(cli.volumes.get, src_name)
        except Exception:  # noqa: BLE001
            await ctx.log(f"Volume {src_name} chưa tồn tại, bỏ qua")
            continue
        if app.volumes_dir:  # app mới dùng volume dạng thư mục (override sinh khi deploy trỏ vào đây)
            dst = str(Path(app.volumes_dir) / vname)
            Path(dst).mkdir(parents=True, exist_ok=True)
        else:
            dst = f"{app.compose_project}_{vname}"
            await docker_svc.run(cli.volumes.create, name=dst, driver="local",
                                 labels={"com.docker.compose.project": app.compose_project, "com.docker.compose.volume": vname})
        await ctx.log(f"Chép volume {src_name} → {dst}")
        await docker_svc.run(docker_svc.helper_run, HELPER, ["sh", "-c", "cp -a /from/. /to/"],
                             {src_name: {"bind": "/from", "mode": "ro"}, dst: {"bind": "/to", "mode": "rw"}})


async def _copy_binding(a: ServiceBinding, b: ServiceBinding, ctx: JobContext) -> None:
    kind = a.service.kind
    tmp = Path(tempfile.mkdtemp(prefix="clone-", dir=settings.data_dir))
    try:
        if kind == "postgres":
            dump = tmp / "db.dump"
            await ctx.log(f"Postgres {a.resource} → {b.resource}")
            for user, pwd, cmd in (
                (a.username, a.password_enc, ["pg_dump", "-Fc", "-h", a.service.host, "-p", str(a.service.port), "-U", a.username, "-d", a.resource, "-f", str(dump)]),
                (b.username, b.password_enc, ["pg_restore", "--no-owner", "--no-acl", "-h", b.service.host, "-p", str(b.service.port), "-U", b.username, "-d", b.resource, str(dump)]),
            ):
                proc = await asyncio.create_subprocess_exec(*cmd, env=subprocess_env(PGPASSWORD=decrypt(pwd)), stderr=asyncio.subprocess.PIPE)
                _, err = await proc.communicate()
                if proc.returncode not in (0, 1) or (proc.returncode == 1 and cmd[0] == "pg_dump"):
                    raise RuntimeError(f"{cmd[0]} {user}: {err.decode()[-800:]}")
                if err.strip():
                    await ctx.log(err.decode()[-600:])
        elif kind == "redis":
            r = platform._redis(a.service)
            n = 0
            try:
                old, new = f"{a.resource}:".encode(), f"{b.resource}:".encode()
                async for key in r.scan_iter(match=f"{a.resource}:*", count=1000):
                    data = await r.dump(key)
                    if data is not None:
                        await r.restore(new + key[len(old):], max(await r.pttl(key), 0), data, replace=True)
                        n += 1
            finally:
                await r.aclose()
            await ctx.log(f"Redis {a.resource}:* → {b.resource}:* ({n} key)")
        elif kind == "s3":
            n = await docker_svc.run(_s3_mirror_down, a.service, a.resource, tmp)
            await docker_svc.run(_s3_mirror_up, b.service, b.resource, tmp)
            await ctx.log(f"S3 {a.resource} → {b.resource} ({n} object)")
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp, True)


@job_handler("app_clone")
async def job_clone(ctx: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as s:
        src = await s.get(App, params["source_id"])
        app = await s.get(App, params["app_id"])
        if src is None or app is None:
            raise RuntimeError("App nguồn hoặc app mới không còn")
        pairs = []
        for old_id, new_id in (params.get("bindings") or {}).items():
            a, b = await s.get(ServiceBinding, int(old_id)), await s.get(ServiceBinding, int(new_id))
            if a and b:
                pairs.append((a, b))
    await ctx.log(f"Clone {src.slug} → {app.slug}")
    root = params.get("copy_root")
    if params.get("copy_files") and root and os.path.isdir(root):
        exclude = [*GENERATED, "*.bak"]
        if src.kind == "managed":
            exclude.append("docker-compose.yml")
        else:
            exclude += [Path(f).name for f in src.compose_files if f and str(Path(f).parent) == root]
        await _copy_dir(root, app.work_dir, exclude, ctx)
    stopped = False
    try:
        if params.get("copy_data"):
            if params.get("stop_source"):
                await ctx.log("Tạm dừng app gốc để chép dữ liệu nhất quán")
                await compose.action(src, "stop", ctx)
                stopped = True
            await _copy_volumes(src, app, ctx)
            if src.data_dir and app.data_dir and os.path.isdir(src.data_dir):
                await _copy_dir(src.data_dir, app.data_dir, [], ctx)
            for a, b in pairs:
                await _copy_binding(a, b, ctx)
        elif pairs:
            await ctx.log(f"Cấp {len(pairs)} tài nguyên nền tảng mới (trống), không chép dữ liệu")
    finally:
        if stopped:
            await ctx.log("Khởi động lại app gốc")
            await compose.action(src, "start", ctx)
    compose.fix_owner(app)
    if (app.git or {}).get("repo") and not (Path(app.work_dir) / git.SRC / ".git").is_dir():
        return await job_git_update(ctx, {"app_id": app.id, "deploy": params.get("deploy", True), "initial": True})
    if params.get("deploy", True):
        return await job_deploy(ctx, {"app_id": app.id, "pull": True})
    return {"deployed": False}
