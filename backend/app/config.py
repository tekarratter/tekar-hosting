from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TH_", env_file=".env", extra="ignore")

    secret_key: str = "dev-secret-change-me"
    database_url: str = "postgresql+asyncpg://th:th@localhost:5432/th"
    data_dir: Path = Path("/data/application/tekar-hosting/data")
    apps_root: Path = Path("/data/application")
    static_dir: Path | None = None
    api_docs: bool = False  # bật /api/docs (Swagger) — mặc định tắt trên môi trường public

    admin_email: str = "admin@example.com"
    admin_password: str = "admin"

    caddy_admin: str = "http://th-caddy:2019"
    edge_network: str = "th-edge"
    caddy_container: str = "th-caddy"
    cloudflared_container: str = "th-cloudflared"
    claude_container: str = "th-claude"

    host_proc: str = "/proc"
    host_cgroup: str = "/sys/fs/cgroup"
    npm_sqlite: str = "/data/application/proxy/data/database.sqlite"

    caddy_http_port: int = 8880
    caddy_https_port: int = 8843
    compose_project: str = "tekar-hosting"

    jwt_ttl_hours: int = 12
    metrics_interval: int = 30
    metrics_retention_hours: int = 72

    @property
    def apps_dir(self) -> Path:
        return self.data_dir / "apps"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
