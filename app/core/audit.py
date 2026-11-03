from typing import Any, Optional

from app.models.settings import AuditLog
from app.models.user import User


def add_audit_entry(
    session,
    *,
    actor: Optional[User],
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            actor_username=actor.username if actor else "system",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )