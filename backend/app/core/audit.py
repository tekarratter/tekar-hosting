from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog, User
from .deps import client_ip


async def audit(
    db: AsyncSession,
    user: User | None,
    action: str,
    target: str = "",
    detail: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    ip = client_ip(request)
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            user_email=user.email if user else "system",
            action=action,
            target=target,
            detail=detail or {},
            ip=ip,
        )
    )
    await db.commit()
