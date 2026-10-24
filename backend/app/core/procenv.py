"""Môi trường tối thiểu cho tiến trình con (docker compose, git, pg_dump...).

Không truyền nguyên os.environ: th-api/th-worker nạp .env của panel (TH_SECRET_KEY, TH_DB_PASSWORD...),
mà `docker compose` nội suy ${VAR} từ môi trường tiến trình → compose của app có thể đọc secret của panel.
"""

import os

_KEEP = {
    "PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "USER",
    "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "DOCKER_API_VERSION", "DOCKER_BUILDKIT",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
}
_KEEP_PREFIX = ("COMPOSE_", "BUILDKIT_")


def subprocess_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _KEEP or k.startswith(_KEEP_PREFIX)}
    env.update(extra)
    return env
