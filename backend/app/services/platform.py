"""Dịch vụ nền tảng dùng chung: Postgres / Redis / S3 (MinIO).

Mỗi app được cấp tài nguyên riêng (database+role, ACL user + key prefix, bucket+user)
và nhận biến môi trường kết nối.
"""

import io
import json
import re
from typing import Any

import asyncpg
import redis.asyncio as aioredis
from minio import Minio
from minio.minioadmin import MinioAdmin
from minio.credentials import StaticProvider

from ..core.security import decrypt, random_password
from ..models import PlatformService, ServiceBinding
from . import docker_svc

IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MANAGED_IMAGES = {
    "postgres": "postgres:17-alpine",
    "redis": "redis:7-alpine",
    "s3": "minio/minio:RELEASE.2025-04-08T15-41-24Z",
}


def ident(name: str) -> str:
    s = re.sub(r"[^a-z0-9_]", "_", name.lower())
    if not s or not s[0].isalpha():
        s = "a_" + s
    s = s[:63]
    if not IDENT_RE.match(s):
        raise ValueError(f"Tên không hợp lệ: {name}")
    return s


def admin_password(svc: PlatformService) -> str:
    return decrypt(svc.admin_password_enc)


def app_host(svc: PlatformService) -> tuple[str, int]:
    return svc.public_host or svc.host, svc.public_port or svc.port


# ---------------------------------------------------------------- postgres
async def _pg(svc: PlatformService, database: str = "postgres") -> asyncpg.Connection:
    return await asyncpg.connect(
        host=svc.host, port=svc.port, user=svc.admin_user, password=admin_password(svc), database=database, timeout=10
    )


async def pg_provision(svc: PlatformService, db_name: str, user: str, password: str) -> None:
    conn = await _pg(svc)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", user)
        if exists:
            await conn.execute(f'ALTER ROLE "{user}" WITH LOGIN PASSWORD $pw${password}$pw$')
        else:
            await conn.execute(f'CREATE ROLE "{user}" WITH LOGIN PASSWORD $pw${password}$pw$')
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", db_name):
            # role admin cần là thành viên role đích để gán OWNER (PG16+)
            await conn.execute(f'GRANT "{user}" TO CURRENT_USER')
            await conn.execute(f'CREATE DATABASE "{db_name}" OWNER "{user}"')
        await conn.execute(f'REVOKE ALL ON DATABASE "{db_name}" FROM PUBLIC')
        await conn.execute(f'GRANT ALL ON DATABASE "{db_name}" TO "{user}"')
        if svc.mode == "managed":
            # server do panel tạo: chặn app vào DB bảo trì (server external giữ nguyên để không ảnh hưởng app cũ)
            await conn.execute('REVOKE CONNECT ON DATABASE postgres FROM PUBLIC')
    finally:
        await conn.close()


async def pg_deprovision(svc: PlatformService, db_name: str, user: str, drop_data: bool) -> None:
    conn = await _pg(svc)
    try:
        if drop_data:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()", db_name
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
            await conn.execute(f'DROP ROLE IF EXISTS "{user}"')
        else:
            await conn.execute(f'ALTER ROLE "{user}" NOLOGIN')
    finally:
        await conn.close()


async def pg_stats(svc: PlatformService) -> dict[str, Any]:
    conn = await _pg(svc)
    try:
        version = await conn.fetchval("SHOW server_version")
        dbs = await conn.fetch(
            "SELECT datname, pg_database_size(datname) AS size FROM pg_database WHERE NOT datistemplate ORDER BY 2 DESC"
        )
        conns = await conn.fetchval("SELECT count(*) FROM pg_stat_activity")
        maxc = await conn.fetchval("SHOW max_connections")
        return {
            "version": version,
            "connections": conns,
            "max_connections": int(maxc),
            "databases": [{"name": r["datname"], "size": r["size"]} for r in dbs],
        }
    finally:
        await conn.close()


# ---------------------------------------------------------------- redis
def _redis(svc: PlatformService) -> aioredis.Redis:
    return aioredis.Redis(
        host=svc.host, port=svc.port, username=svc.admin_user or None, password=admin_password(svc) or None, socket_timeout=10
    )


async def redis_provision(svc: PlatformService, prefix: str, user: str, password: str) -> None:
    r = _redis(svc)
    try:
        await r.execute_command(
            "ACL", "SETUSER", user, "reset", "on", f">{password}", f"~{prefix}:*", f"&{prefix}:*",
            "+@all", "-@admin", "-@dangerous", "+info", "+ping", "+select",
        )
        try:
            await r.execute_command("ACL", "SAVE")
        except aioredis.ResponseError:
            pass  # không có aclfile -> ACL chỉ tồn tại tới khi redis restart; xem cảnh báo trong UI
    finally:
        await r.aclose()


async def redis_deprovision(svc: PlatformService, prefix: str, user: str, drop_data: bool) -> None:
    r = _redis(svc)
    try:
        await r.execute_command("ACL", "DELUSER", user)
        if drop_data:
            async for key in r.scan_iter(match=f"{prefix}:*", count=1000):
                await r.delete(key)
    finally:
        await r.aclose()


async def redis_stats(svc: PlatformService) -> dict[str, Any]:
    r = _redis(svc)
    try:
        info = await r.info()
        try:
            aclfile = (await r.config_get("aclfile")).get("aclfile", "")
        except aioredis.ResponseError:
            aclfile = ""
        return {
            "version": info.get("redis_version"),
            "used_memory": info.get("used_memory"),
            "maxmemory": info.get("maxmemory"),
            "connected_clients": info.get("connected_clients"),
            "keys": sum(v.get("keys", 0) for k, v in info.items() if k.startswith("db") and isinstance(v, dict)),
            "aclfile": aclfile,
            "acl_persistent": bool(aclfile),
        }
    finally:
        await r.aclose()


# ---------------------------------------------------------------- s3 / minio
def _endpoint(svc: PlatformService) -> tuple[str, bool]:
    return f"{svc.host}:{svc.port}", bool((svc.extra or {}).get("secure"))


def _minio(svc: PlatformService) -> Minio:
    ep, secure = _endpoint(svc)
    return Minio(ep, access_key=svc.admin_user, secret_key=admin_password(svc), secure=secure, region=(svc.extra or {}).get("region"))


def _madmin(svc: PlatformService) -> MinioAdmin:
    ep, secure = _endpoint(svc)
    return MinioAdmin(ep, credentials=StaticProvider(svc.admin_user, admin_password(svc)), secure=secure)


def _bucket_policy(bucket: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:*"], "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"]}
        ],
    }


def s3_provision(svc: PlatformService, bucket: str, user: str, password: str) -> None:
    mc = _minio(svc)
    if not mc.bucket_exists(bucket):
        mc.make_bucket(bucket)
    if (svc.extra or {}).get("provider", "minio") != "minio":
        return  # S3 khác MinIO: chỉ tạo bucket, app dùng chung access key được cấp sẵn
    adm = _madmin(svc)
    policy_name = f"th-{bucket}"
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(_bucket_policy(bucket), f)
        policy_file = f.name
    adm.policy_add(policy_name, policy_file)
    adm.user_add(user, password)
    adm.policy_set(policy_name, user=user)


def s3_deprovision(svc: PlatformService, bucket: str, user: str, drop_data: bool) -> None:
    if (svc.extra or {}).get("provider", "minio") == "minio":
        adm = _madmin(svc)
        try:
            adm.user_remove(user)
        except Exception:  # noqa: BLE001
            pass
        try:
            adm.policy_remove(f"th-{bucket}")
        except Exception:  # noqa: BLE001
            pass
    if drop_data:
        mc = _minio(svc)
        if mc.bucket_exists(bucket):
            from minio.deleteobjects import DeleteObject

            objs = (DeleteObject(o.object_name) for o in mc.list_objects(bucket, recursive=True))
            for _ in mc.remove_objects(bucket, objs):
                pass
            mc.remove_bucket(bucket)


def s3_stats(svc: PlatformService) -> dict[str, Any]:
    mc = _minio(svc)
    return {"buckets": [{"name": b.name, "created": b.creation_date.isoformat() if b.creation_date else None} for b in mc.list_buckets()]}


def s3_upload(svc: PlatformService, bucket: str, key: str, path: str) -> None:
    mc = _minio(svc)
    if not mc.bucket_exists(bucket):
        mc.make_bucket(bucket)
    mc.fput_object(bucket, key, path)


def s3_download(svc: PlatformService, bucket: str, key: str, path: str) -> None:
    _minio(svc).fget_object(bucket, key, path)


def s3_delete(svc: PlatformService, bucket: str, key: str) -> None:
    _minio(svc).remove_object(bucket, key)


def s3_put_bytes(svc: PlatformService, bucket: str, key: str, data: bytes) -> None:
    _minio(svc).put_object(bucket, key, io.BytesIO(data), len(data))


# ---------------------------------------------------------------- facade
async def test_connection(svc: PlatformService) -> dict[str, Any]:
    if svc.kind == "postgres":
        return await pg_stats(svc)
    if svc.kind == "redis":
        return await redis_stats(svc)
    if svc.kind == "s3":
        return await docker_svc.run(s3_stats, svc)
    raise ValueError(svc.kind)


async def provision(svc: PlatformService, resource: str, user: str, password: str) -> None:
    if svc.kind == "postgres":
        await pg_provision(svc, resource, user, password)
    elif svc.kind == "redis":
        await redis_provision(svc, resource, user, password)
    elif svc.kind == "s3":
        await docker_svc.run(s3_provision, svc, resource, user, password)


async def deprovision(svc: PlatformService, resource: str, user: str, drop_data: bool) -> None:
    if svc.kind == "postgres":
        await pg_deprovision(svc, resource, user, drop_data)
    elif svc.kind == "redis":
        await redis_deprovision(svc, resource, user, drop_data)
    elif svc.kind == "s3":
        await docker_svc.run(s3_deprovision, svc, resource, user, drop_data)


def binding_env(b: ServiceBinding) -> dict[str, str]:
    svc = b.service
    host, port = app_host(svc)
    pw = decrypt(b.password_enc)
    p = b.env_prefix
    if svc.kind == "postgres":
        return {
            f"{p}DATABASE_URL": f"postgresql://{b.username}:{pw}@{host}:{port}/{b.resource}",
            f"{p}PGHOST": host,
            f"{p}PGPORT": str(port),
            f"{p}PGDATABASE": b.resource,
            f"{p}PGUSER": b.username,
            f"{p}PGPASSWORD": pw,
        }
    if svc.kind == "redis":
        return {
            f"{p}REDIS_URL": f"redis://{b.username}:{pw}@{host}:{port}/0",
            f"{p}REDIS_KEY_PREFIX": f"{b.resource}:",
        }
    if svc.kind == "s3":
        provider = (svc.extra or {}).get("provider", "minio")
        scheme = "https" if (svc.extra or {}).get("secure") else "http"
        access, secret = (b.username, pw) if provider == "minio" else (svc.admin_user, admin_password(svc))
        return {
            f"{p}S3_ENDPOINT": (svc.extra or {}).get("public_endpoint") or f"{scheme}://{host}:{port}",
            f"{p}S3_BUCKET": b.resource,
            f"{p}S3_ACCESS_KEY": access,
            f"{p}S3_SECRET_KEY": secret,
            f"{p}S3_REGION": (svc.extra or {}).get("region") or "us-east-1",
        }
    return {}


async def create_managed(kind: str, name: str) -> dict[str, Any]:
    """Tạo container dịch vụ nền tảng mới trên mạng edge. Trả về thông tin đăng ký."""
    from ..config import settings

    image = MANAGED_IMAGES[kind]
    cname = f"th-{kind}-{ident(name).replace('_', '-')}"
    password = random_password(28)
    vol = f"{cname}-data"
    if kind == "postgres":
        env, port, user, cmd, mount = {"POSTGRES_PASSWORD": password, "POSTGRES_USER": "postgres"}, 5432, "postgres", None, "/var/lib/postgresql/data"
    elif kind == "redis":
        env, port, user, mount = {}, 6379, "default", "/data"
        cmd = ["redis-server", "--appendonly", "yes", "--requirepass", password, "--aclfile", "/data/users.acl"]
    else:
        env, port, user, mount = {"MINIO_ROOT_USER": "thadmin", "MINIO_ROOT_PASSWORD": password}, 9000, "thadmin", "/data"
        cmd = ["server", "/data", "--console-address", ":9001"]

    def _create():
        docker_svc.client().images.pull(image) if not _has_image(image) else None
        if kind == "redis":
            # aclfile phải tồn tại trước khi redis khởi động
            docker_svc.helper_run("alpine:3.20", ["sh", "-c", f"touch /data/users.acl && echo 'user default on >{password} ~* &* +@all' > /data/users.acl"],
                                  {vol: {"bind": "/data", "mode": "rw"}})
            run_cmd = ["redis-server", "--appendonly", "yes", "--aclfile", "/data/users.acl"]
        else:
            run_cmd = cmd
        docker_svc.client().containers.run(
            image, run_cmd, name=cname, detach=True, environment=env, network=settings.edge_network,
            volumes={vol: {"bind": mount, "mode": "rw"}}, restart_policy={"Name": "unless-stopped"},
            labels={"th.platform": kind, "th.managed": "true"},
        )

    await docker_svc.run(_create)
    return {"host": cname, "port": port, "admin_user": user, "admin_password": password, "container_name": cname}


def _has_image(image: str) -> bool:
    try:
        docker_svc.client().images.get(image)
        return True
    except Exception:  # noqa: BLE001
        return False
