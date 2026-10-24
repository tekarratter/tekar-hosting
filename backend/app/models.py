from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- users / rbac
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    # admin: toàn quyền | operator: quản lý mọi app + hạ tầng, không quản lý user/settings
    # member: chỉ thấy app được cấp quyền
    role: Mapped[str] = mapped_column(String(20), default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # tăng khi đổi mật khẩu / reset 2FA → JWT cũ (mang "ver") hết hiệu lực
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = _now()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user: Mapped[User] = relationship(lazy="joined")


# ---------------------------------------------------------------- apps
class App(Base):
    __tablename__ = "apps"
    __mapper_args__ = {"eager_defaults": True}  # lấy updated_at (onupdate) qua RETURNING, tránh lazy-load sau commit
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    # managed: compose do hệ thống lưu & sinh | adopted: dự án compose có sẵn trên host
    kind: Mapped[str] = mapped_column(String(20), default="managed")
    compose_project: Mapped[str] = mapped_column(String(100), unique=True)
    work_dir: Mapped[str] = mapped_column(String(500))
    compose_files: Mapped[list[str]] = mapped_column(JSON, default=list)
    compose_yaml: Mapped[str] = mapped_column(Text, default="")
    env_enc: Mapped[str] = mapped_column(Text, default="")  # .env đã mã hoá
    status: Mapped[str] = mapped_column(String(30), default="unknown")

    # cấp phát tài nguyên
    cpu_limit: Mapped[float | None] = mapped_column(Float, nullable=True)  # số core
    mem_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_quota_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_usage_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    # {"service": {"cpus": 0.5, "memory_mb": 512}} — áp dụng qua compose override + docker update
    service_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # các service được gắn vào mạng edge để proxy truy cập
    edge_services: Mapped[list[str]] = mapped_column(JSON, default=list)

    # backup
    backup_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    backup_retention: Mapped[int] = mapped_column(Integer, default=7)
    backup_target_id: Mapped[int | None] = mapped_column(ForeignKey("platform_services.id", ondelete="SET NULL"), nullable=True)
    backup_stop_app: Mapped[bool] = mapped_column(Boolean, default=False)
    backup_include_binds: Mapped[bool] = mapped_column(Boolean, default=True)

    # vendor-free: dữ liệu volume dạng thư mục bind + thư mục data mount (biến APP_DATA_DIR)
    volumes_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    data_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    backup_destination_id: Mapped[int | None] = mapped_column(ForeignKey("backup_targets.id", ondelete="SET NULL"), nullable=True)
    backup_keep_local: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    converted_from: Mapped[str | None] = mapped_column(String(50), nullable=True)  # dockhand | portainer | ...

    deploy_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    auto_update: Mapped[bool] = mapped_column(Boolean, default=False)
    template: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # nguồn code từ git: {repo, branch, credential_id, subdir, compose_path, sync_compose, auto_pull, commit, commit_msg, pulled_at}
    git: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GitCredential(Base):
    """Kết nối git (GitHub / GitLab / Gitea…) bằng personal access token để clone repo private."""

    __tablename__ = "git_credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(20), default="github")  # github | gitlab | generic
    host: Mapped[str] = mapped_column(String(255), default="github.com")
    username: Mapped[str] = mapped_column(String(100), default="")  # tài khoản (GitHub: lấy từ API)
    token_enc: Mapped[str] = mapped_column(Text)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)  # admin chia sẻ cho mọi người tạo app
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = _now()


class AppTemplate(Base):
    """Template do người dùng lưu (compose + env + dịch vụ nền tảng + service web)."""

    __tablename__ = "app_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    compose: Mapped[str] = mapped_column(Text)
    env: Mapped[str] = mapped_column(Text, default="")
    services: Mapped[list[str]] = mapped_column(JSON, default=list)
    web: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = _now()


class BackupTarget(Base):
    """Nơi lưu backup: local (thư mục khác) | s3 (AWS/R2/B2/Wasabi/MinIO) | sftp."""

    __tablename__ = "backup_targets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    # s3: endpoint, bucket, region, access_key, prefix, path_style | sftp: host, port, username, path | local: path
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secret_enc: Mapped[str] = mapped_column(Text, default="")  # s3 secret key | sftp password | sftp private key
    created_at: Mapped[datetime] = _now()


class AppMember(Base):
    __tablename__ = "app_members"
    __table_args__ = (UniqueConstraint("app_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # owner > deployer > viewer
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    user: Mapped[User] = relationship(lazy="joined")


# ---------------------------------------------------------------- platform services
class PlatformService(Base):
    __tablename__ = "platform_services"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # postgres | redis | s3
    # external: dịch vụ có sẵn | managed: do hệ thống tạo container
    mode: Mapped[str] = mapped_column(String(20), default="external")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    # host/port mà app container dùng để kết nối (có thể khác host/port control plane dùng)
    public_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    public_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_user: Mapped[str] = mapped_column(String(100), default="")
    admin_password_enc: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    container_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = _now()


class ServiceBinding(Base):
    __tablename__ = "service_bindings"
    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("platform_services.id", ondelete="CASCADE"), index=True)
    resource: Mapped[str] = mapped_column(String(100))  # tên db / key prefix / bucket
    username: Mapped[str] = mapped_column(String(100))
    password_enc: Mapped[str] = mapped_column(Text)
    env_prefix: Mapped[str] = mapped_column(String(50), default="")
    include_in_backup: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()
    service: Mapped[PlatformService] = relationship(lazy="joined")


# ---------------------------------------------------------------- proxy
class AccessList(Base):
    """Danh sách truy cập dùng lại cho nhiều route (tương đương Access List của NPM)."""

    __tablename__ = "access_lists"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # True: đạt 1 trong 2 điều kiện (IP hoặc đăng nhập) là đủ | False: phải đạt cả hai
    satisfy_any: Mapped[bool] = mapped_column(Boolean, default=False)
    pass_auth: Mapped[bool] = mapped_column(Boolean, default=False)  # chuyển header Authorization xuống upstream
    auth: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # [{"username","password_hash"}]
    clients: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # [{"address":"1.2.3.0/24","directive":"allow|deny"}]
    created_at: Mapped[datetime] = _now()


class Certificate(Base):
    """Chứng chỉ tải lên thủ công (cert ACME do Caddy tự quản lý)."""

    __tablename__ = "certificates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    cert_pem: Mapped[str] = mapped_column(Text)
    key_pem_enc: Mapped[str] = mapped_column(Text)
    not_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issuer: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = _now()


class Stream(Base):
    """Chuyển tiếp TCP/UDP (layer4) — tương đương Streams của NPM."""

    __tablename__ = "streams"
    id: Mapped[int] = mapped_column(primary_key=True)
    listen_port: Mapped[int] = mapped_column(Integer, unique=True)
    protocol: Mapped[str] = mapped_column(String(10), default="tcp")  # tcp | udp | both
    upstream: Mapped[str] = mapped_column(String(255))  # host:port | container:port
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    app_id: Mapped[int | None] = mapped_column(ForeignKey("apps.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = _now()


class ProxyRoute(Base):
    __tablename__ = "proxy_routes"
    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int | None] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), nullable=True, index=True)
    # proxy | redirect | dead (404)
    kind: Mapped[str] = mapped_column(String(20), default="proxy", server_default="proxy")
    access_list_id: Mapped[int | None] = mapped_column(ForeignKey("access_lists.id", ondelete="SET NULL"), nullable=True)
    certificate_id: Mapped[int | None] = mapped_column(ForeignKey("certificates.id", ondelete="SET NULL"), nullable=True)
    hosts: Mapped[list[str]] = mapped_column(JSON, default=list)
    path_prefix: Mapped[str] = mapped_column(String(255), default="")
    # container:port | host:port | http(s)://url  (nhiều upstream = load balance)
    upstreams: Mapped[list[str]] = mapped_column(JSON, default=list)
    # tunnel: chỉ HTTP qua Cloudflare Tunnel | auto: Caddy tự cấp cert (DNS-01 CF) | internal: cert nội bộ
    tls_mode: Mapped[str] = mapped_column(String(20), default="tunnel")
    cf_tunnel: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    health_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    health_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | npm-import | template
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- backups
class Backup(Base):
    __tablename__ = "backups"
    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    path: Mapped[str] = mapped_column(String(500), default="")
    remote_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------- security
class ScanResult(Base):
    __tablename__ = "scan_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # image | config
    target: Mapped[str] = mapped_column(String(500), index=True)
    app_id: Mapped[int | None] = mapped_column(ForeignKey("apps.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="done")
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- jobs / audit
class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    app_id: Mapped[int | None] = mapped_column(ForeignKey("apps.id", ondelete="SET NULL"), nullable=True, index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    log: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_email: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(100), index=True)
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), index=True)  # khoá chống trùng
    level: Mapped[str] = mapped_column(String(20), default="warning")  # info | warning | critical
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------- metrics
class ContainerSample(Base):
    __tablename__ = "container_samples"
    __table_args__ = (Index("ix_csample_name_ts", "name", "ts"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    name: Mapped[str] = mapped_column(String(255))
    project: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    cpu_pct: Mapped[float] = mapped_column(Float, default=0)
    mem_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mem_limit: Mapped[int] = mapped_column(BigInteger, default=0)
    net_rx: Mapped[int] = mapped_column(BigInteger, default=0)
    net_tx: Mapped[int] = mapped_column(BigInteger, default=0)
    blk_read: Mapped[int] = mapped_column(BigInteger, default=0)
    blk_write: Mapped[int] = mapped_column(BigInteger, default=0)


class HostSample(Base):
    __tablename__ = "host_samples"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cpu_pct: Mapped[float] = mapped_column(Float)
    load1: Mapped[float] = mapped_column(Float)
    mem_used: Mapped[int] = mapped_column(BigInteger)
    mem_total: Mapped[int] = mapped_column(BigInteger)
    disk_used: Mapped[int] = mapped_column(BigInteger)
    disk_total: Mapped[int] = mapped_column(BigInteger)


# ---------------------------------------------------------------- claude
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="Cuộc trò chuyện mới")
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    engine: Mapped[str | None] = mapped_column(String(20), nullable=True)
    engine_session: Mapped[str | None] = mapped_column(String(64), nullable=True)  # session id Claude Code (gói Claude)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = _now()
