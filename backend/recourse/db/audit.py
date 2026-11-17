"""Writing to the append-only audit log."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from recourse.db.models import Actor, AuditEvent
from recourse.security.pii import redact_text

_EVENT_TYPE_RE = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")


def redact_details(value: Any) -> Any:
    """Redact PII from every string in a JSON-like structure (keys and values)."""
    if isinstance(value, str):
        return redact_text(value)[0]
    if isinstance(value, dict):
        return {redact_details(str(k)): redact_details(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_details(v) for v in value]
    return value


def record_event(
    session: AsyncSession,
    case_id: uuid.UUID,
    actor: Actor,
    event_type: str,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an audit event to the session (it is written on the caller's commit).

    `details` should hold metadata (IDs, statuses, field names), not content. Strings are
    PII-redacted anyway, as a safety net.
    """
    if not _EVENT_TYPE_RE.match(event_type):
        raise ValueError(f"event_type must look like 'area.action', got {event_type!r}")
    event = AuditEvent(
        case_id=case_id,
        actor=actor,
        event_type=event_type,
        details=redact_details(details or {}),
    )
    session.add(event)
    return event
