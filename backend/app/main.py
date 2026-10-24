import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .ai.mcp_server import mcp_asgi, session_manager
from .config import settings
from .core.security import hash_password
from .db import SessionLocal, init_db
from .models import User
from .routers import (
    apps, auth, backups, git, claude, cloudflare, containers, metrics, platform, proxy, security, stacks, storage, system, terminal, users,
)
from .services import docker_svc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("th")


async def bootstrap() -> None:
    if settings.secret_key in ("dev-secret-change-me", "change-me") or len(settings.secret_key) < 16:
        # secret ký JWT + mã hoá secret trong DB; để mặc định là ai cũng giả được phiên admin
        raise SystemExit("TH_SECRET_KEY chưa đặt hoặc quá ngắn (≥16 ký tự) — sửa .env rồi khởi động lại")
    await init_db()
    for d in (settings.apps_dir, settings.backups_dir):
        d.mkdir(parents=True, exist_ok=True)
    async with SessionLocal() as s:
        if not (await s.execute(select(func.count()).select_from(User))).scalar():
            s.add(User(email=settings.admin_email.lower(), name="Administrator", role="admin", password_hash=hash_password(settings.admin_password)))
            await s.commit()
            log.info("Đã tạo tài khoản admin %s", settings.admin_email)
    try:
        await docker_svc.ensure_network(settings.edge_network)
    except Exception as e:  # noqa: BLE001
        log.warning("Không tạo được mạng edge: %s", e)
    try:
        from .services.proxy import apply

        async with SessionLocal() as s:
            await apply(s)
    except Exception as e:  # noqa: BLE001
        log.warning("Chưa đẩy được cấu hình Caddy: %s", e)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await bootstrap()
    async with session_manager.run():
        yield


app = FastAPI(title="Tekar Hosting", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs" if settings.api_docs else None, openapi_url="/api/openapi.json" if settings.api_docs else None)

for r in (auth, users, apps, git, stacks, storage, containers, platform, proxy, cloudflare, backups, security, metrics, system, terminal, claude):
    app.include_router(r.router)
app.include_router(system.hooks)
app.mount("/mcp", mcp_asgi)


class McpNoSlash:
    """`/mcp` (không có / cuối) → `/mcp/`; nếu không, route SPA bắt mất và POST trả 405."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.inner(scope, receive, send)


app.add_middleware(McpNoSlash)


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.exception_handler(ValueError)
async def value_error(_req: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---- SPA
static = settings.static_dir
if static and Path(static).is_dir():
    app.mount("/assets", StaticFiles(directory=Path(static) / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith(("api/", "mcp")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        root = Path(static).resolve()
        f = (root / path).resolve()
        # chặn ../ thoát khỏi thư mục static (đọc được .env, autosave Caddy...)
        if path and f.is_relative_to(root) and f.is_file():
            return FileResponse(f)
        return FileResponse(root / "index.html")
