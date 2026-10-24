"""Hàng đợi job lưu trong Postgres. API enqueue, worker (app.worker) xử lý.

Handler đăng ký bằng @job_handler("kind") và nhận (ctx: JobContext, params: dict).
"""

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import Job

log = logging.getLogger("th.jobs")
Handler = Callable[["JobContext", dict[str, Any]], Awaitable[dict[str, Any] | None]]
HANDLERS: dict[str, Handler] = {}


def job_handler(kind: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


async def enqueue(
    db: AsyncSession, kind: str, params: dict[str, Any] | None = None, app_id: int | None = None, user_id: int | None = None
) -> Job:
    job = Job(kind=kind, params=params or {}, app_id=app_id, created_by=user_id)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


class JobContext:
    def __init__(self, job: Job):
        self.job = job
        self._buf: list[str] = []
        self._lock = asyncio.Lock()

    async def log(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        for part in str(line).rstrip("\n").split("\n"):
            self._buf.append(f"[{stamp}] {part}\n")
        if len(self._buf) >= 20:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            if not self._buf:
                return
            chunk, self._buf = "".join(self._buf), []
            async with SessionLocal() as s:
                await s.execute(update(Job).where(Job.id == self.job.id).values(log=Job.log + chunk))
                await s.commit()


async def run_job(job_id: int) -> None:
    async with SessionLocal() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await s.commit()

    ctx = JobContext(job)
    handler = HANDLERS.get(job.kind)
    status, result = "success", {}
    try:
        if handler is None:
            raise RuntimeError(f"Không có handler cho job '{job.kind}'")
        result = await handler(ctx, job.params or {}) or {}
    except Exception as e:  # noqa: BLE001
        status = "failed"
        await ctx.log(f"LỖI: {e}")
        log.warning("job %s failed: %s", job_id, traceback.format_exc())
        result = {"error": str(e)}
    await ctx.flush()
    async with SessionLocal() as s:
        await s.execute(
            update(Job).where(Job.id == job_id).values(status=status, result=result, finished_at=datetime.now(UTC))
        )
        await s.commit()
    if status == "failed":
        from ..services.notify import notify

        await notify("critical", f"Job {job.kind} #{job_id} thất bại", str(result.get("error", ""))[:500], key=f"job:{job_id}")


async def claim_next() -> int | None:
    async with SessionLocal() as s:
        row = (
            await s.execute(
                select(Job).where(Job.status == "queued").order_by(Job.id).limit(1).with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = "claimed"
        await s.commit()
        return row.id
