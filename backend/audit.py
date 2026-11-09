"""Audit trail: every staff read or write of a patient record, and every agent write."""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AuditLog, StaffUser


def _ip(request: Request | None) -> str | None:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def record(
    session: AsyncSession,
    *,
    actor: StaffUser | str,
    action: str,
    entity: str,
    entity_id: object | None = None,
    details: dict | None = None,
    request: Request | None = None,
) -> None:
    """Add an audit row to the current transaction; it commits with the change it describes."""
    if isinstance(actor, StaffUser):
        actor_type, actor_id = "staff", str(actor.id)
    else:
        actor_type, actor_id = actor, None  # "agent" | "system"
    session.add(AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details,
        ip=_ip(request),
    ))
