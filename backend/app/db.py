from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.database_url, pool_size=10, max_overflow=10, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


# Cột thêm sau khi bảng đã tồn tại (create_all không ALTER bảng cũ)
MIGRATIONS = [
    "ALTER TABLE proxy_routes ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'proxy'",
    "ALTER TABLE proxy_routes ADD COLUMN IF NOT EXISTS access_list_id INTEGER REFERENCES access_lists(id) ON DELETE SET NULL",
    "ALTER TABLE proxy_routes ADD COLUMN IF NOT EXISTS certificate_id INTEGER REFERENCES certificates(id) ON DELETE SET NULL",
    "ALTER TABLE apps ADD COLUMN IF NOT EXISTS volumes_dir VARCHAR(500)",
    "ALTER TABLE apps ADD COLUMN IF NOT EXISTS data_dir VARCHAR(500)",
    "ALTER TABLE apps ADD COLUMN IF NOT EXISTS backup_destination_id INTEGER REFERENCES backup_targets(id) ON DELETE SET NULL",
    "ALTER TABLE apps ADD COLUMN IF NOT EXISTS backup_keep_local BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE apps ADD COLUMN IF NOT EXISTS converted_from VARCHAR(50)",
    "ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS read_only BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS engine VARCHAR(20)",
    "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS engine_session VARCHAR(64)",
    "ALTER TABLE apps ADD COLUMN IF NOT EXISTS git JSON",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
]


async def init_db() -> None:
    from sqlalchemy import text

    from . import models  # noqa: F401  (đăng ký metadata)

    async with engine.begin() as conn:
        # khoá để api và worker khởi động cùng lúc không đua nhau tạo bảng
        await conn.execute(text("SELECT pg_advisory_xact_lock(424242)"))
        await conn.run_sync(Base.metadata.create_all)
        for stmt in MIGRATIONS:
            await conn.execute(text(stmt))
