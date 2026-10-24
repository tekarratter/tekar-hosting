"""Nơi lưu backup ngoài máy: local (thư mục khác/ổ gắn ngoài) | S3-compatible (AWS, R2, B2, Wasabi, MinIO) | SFTP."""

import io
import os
import shutil
import stat
from pathlib import Path
from typing import Any

from ..core.security import decrypt
from ..models import BackupTarget


class TargetError(RuntimeError):
    pass


def _s3(t: BackupTarget):
    import boto3
    from botocore.config import Config

    c = t.config or {}
    return boto3.client(
        "s3",
        endpoint_url=c.get("endpoint") or None,
        region_name=c.get("region") or "us-east-1",
        aws_access_key_id=c.get("access_key"),
        aws_secret_access_key=decrypt(t.secret_enc),
        config=Config(s3={"addressing_style": "path" if c.get("path_style", True) else "virtual"}, retries={"max_attempts": 3}),
    )


def _sftp(t: BackupTarget):
    import paramiko

    c = t.config or {}
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    secret = decrypt(t.secret_enc)
    kw: dict[str, Any] = {"hostname": c["host"], "port": int(c.get("port") or 22), "username": c["username"], "timeout": 20,
                          "allow_agent": False, "look_for_keys": False}
    if c.get("auth") == "key":
        pkey = None
        for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                pkey = cls.from_private_key(io.StringIO(secret), password=c.get("key_passphrase") or None)
                break
            except Exception:  # noqa: BLE001
                continue
        if pkey is None:
            raise TargetError("Private key không hợp lệ")
        kw["pkey"] = pkey
    else:
        kw["password"] = secret
    ssh.connect(**kw)
    return ssh, ssh.open_sftp()


def _prefix(t: BackupTarget) -> str:
    c = t.config or {}
    return (c.get("prefix") or c.get("path") or "").strip("/")


def _key(t: BackupTarget, rel: str) -> str:
    p = _prefix(t)
    return f"{p}/{rel}" if p and t.kind == "s3" else rel


def _sftp_mkdirs(sftp, path: str) -> None:
    absolute = path.startswith("/")
    cur = ""
    for part in [p for p in path.strip("/").split("/") if p and p != "."]:
        cur = f"{cur}/{part}" if cur else (("/" if absolute else "") + part)
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)


def _sftp_path(t: BackupTarget, rel: str) -> str:
    base = (t.config or {}).get("path") or "."
    return f"{base.rstrip('/')}/{rel}"


def test(t: BackupTarget) -> str:
    if t.kind == "local":
        p = Path((t.config or {}).get("path") or "")
        if not p.is_absolute():
            raise TargetError("Đường dẫn phải tuyệt đối")
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".tekar-write-test"
        probe.write_text("ok")
        probe.unlink()
        free = shutil.disk_usage(p).free
        return f"Ghi được vào {p} (trống {free / 1e9:.1f} GB)"
    if t.kind == "s3":
        cli = _s3(t)
        bucket = t.config["bucket"]
        try:
            cli.head_bucket(Bucket=bucket)
        except Exception:  # noqa: BLE001
            cli.create_bucket(Bucket=bucket)
        k = _key(t, ".tekar-write-test")
        cli.put_object(Bucket=bucket, Key=k, Body=b"ok")
        cli.delete_object(Bucket=bucket, Key=k)
        return f"Ghi được vào s3://{bucket}/{_prefix(t)}"
    if t.kind == "sftp":
        ssh, sftp = _sftp(t)
        try:
            base = (t.config or {}).get("path") or "."
            if base != ".":
                _sftp_mkdirs(sftp, base)
            probe = _sftp_path(t, ".tekar-write-test")
            with sftp.open(probe, "w") as f:
                f.write("ok")
            sftp.remove(probe)
            return f"Ghi được vào sftp://{t.config['username']}@{t.config['host']}:{t.config.get('port', 22)}/{'' if base == '.' else base.lstrip('/')}"
        finally:
            sftp.close()
            ssh.close()
    raise TargetError(f"Loại không hỗ trợ: {t.kind}")


def _check_rel(rel: str) -> str:
    """Đường dẫn tương đối trên đích: không tuyệt đối, không '..' (tránh xoá/tải file ngoài thư mục backup)."""
    parts = rel.replace("\\", "/").split("/")
    if not rel or rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise TargetError(f"Đường dẫn backup không hợp lệ: {rel!r}")
    return rel


def upload(t: BackupTarget, local_file: str, rel: str) -> str:
    """Tải file lên, trả về định danh lưu trong Backup.remote_key."""
    _check_rel(rel)
    if t.kind == "local":
        dst = Path(t.config["path"]) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_file, dst)
        return rel
    if t.kind == "s3":
        _s3(t).upload_file(local_file, t.config["bucket"], _key(t, rel))
        return rel
    if t.kind == "sftp":
        ssh, sftp = _sftp(t)
        try:
            dst = _sftp_path(t, rel)
            _sftp_mkdirs(sftp, os.path.dirname(dst))
            sftp.put(local_file, dst)
            if sftp.stat(dst).st_size != os.path.getsize(local_file):
                raise TargetError("Kích thước file trên SFTP không khớp")
            return rel
        finally:
            sftp.close()
            ssh.close()
    raise TargetError(t.kind)


def download(t: BackupTarget, rel: str, local_file: str) -> None:
    _check_rel(rel)
    if t.kind == "local":
        shutil.copy2(Path(t.config["path"]) / rel, local_file)
    elif t.kind == "s3":
        _s3(t).download_file(t.config["bucket"], _key(t, rel), local_file)
    elif t.kind == "sftp":
        ssh, sftp = _sftp(t)
        try:
            sftp.get(_sftp_path(t, rel), local_file)
        finally:
            sftp.close()
            ssh.close()


def delete(t: BackupTarget, rel: str) -> None:
    _check_rel(rel)
    if t.kind == "local":
        (Path(t.config["path"]) / rel).unlink(missing_ok=True)
    elif t.kind == "s3":
        _s3(t).delete_object(Bucket=t.config["bucket"], Key=_key(t, rel))
    elif t.kind == "sftp":
        ssh, sftp = _sftp(t)
        try:
            try:
                sftp.remove(_sftp_path(t, rel))
            except FileNotFoundError:
                pass
        finally:
            sftp.close()
            ssh.close()


def list_files(t: BackupTarget, sub: str = "") -> list[dict[str, Any]]:
    """Liệt kê file backup trên đích (để xem / khôi phục khi mất dữ liệu panel)."""
    out: list[dict[str, Any]] = []
    if t.kind == "local":
        root = Path(t.config["path"]) / sub
        for p in root.rglob("*.tar.gz") if root.exists() else []:
            out.append({"key": str(p.relative_to(t.config["path"])), "size": p.stat().st_size, "modified": p.stat().st_mtime})
    elif t.kind == "s3":
        cli = _s3(t)
        prefix = _key(t, sub).strip("/")
        for page in cli.get_paginator("list_objects_v2").paginate(Bucket=t.config["bucket"], Prefix=prefix):
            for o in page.get("Contents", []):
                key = o["Key"][len(_prefix(t)) + 1:] if _prefix(t) else o["Key"]
                out.append({"key": key, "size": o["Size"], "modified": o["LastModified"].timestamp()})
    elif t.kind == "sftp":
        ssh, sftp = _sftp(t)
        try:
            def walk(rel: str):
                try:
                    entries = sftp.listdir_attr(_sftp_path(t, rel) if rel else ((t.config or {}).get("path") or "."))
                except FileNotFoundError:
                    return
                for e in entries:
                    r = f"{rel}/{e.filename}".strip("/")
                    if stat.S_ISDIR(e.st_mode or 0):
                        walk(r)
                    elif e.filename.endswith(".tar.gz"):
                        out.append({"key": r, "size": e.st_size, "modified": e.st_mtime})
            walk(sub.strip("/"))
        finally:
            sftp.close()
            ssh.close()
    return sorted(out, key=lambda x: -(x["modified"] or 0))
