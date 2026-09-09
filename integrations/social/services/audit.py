import json
import logging
from collections.abc import Mapping

from django.db import transaction

from integrations.social.models import SocialAuditEvent, SocialAuditEventType


logger = logging.getLogger("social.audit")
SENSITIVE_KEY_PARTS = (
    "secret", "token", "password", "authorization", "cookie", "credential", "api_key",
    "provider_profile_id", "provider_account_id", "payload", "body", "copy", "text",
)


def safe_audit_details(value, *, depth=0):
    if depth > 3 or not isinstance(value, Mapping):
        return {}
    result = {}
    for key, item in value.items():
        key = str(key)[:80]
        if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
            result[key] = "<redacted>"
        elif isinstance(item, Mapping):
            result[key] = safe_audit_details(item, depth=depth + 1)
        elif isinstance(item, (str, int, float, bool)) or item is None:
            result[key] = item if not isinstance(item, str) else item[:500]
        elif isinstance(item, (list, tuple)):
            result[key] = [str(entry)[:100] for entry in item[:25]]
    return result


def record_audit_event(*, workspace, event_type, actor=None, target=None, details=None):
    event_type = SocialAuditEventType(event_type)
    target_type = target.__class__.__name__ if target is not None else ""
    target_id = str(getattr(target, "pk", "") or "")
    safe_details = safe_audit_details(details or {})
    event = SocialAuditEvent.objects.create(
        workspace=workspace,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        details=safe_details,
    )
    log_payload = json.dumps({
            "event_id": str(event.id),
            "event_type": event.event_type,
            "workspace_id": str(workspace.id),
            "actor_id": str(getattr(actor, "pk", "") or ""),
            "target_type": target_type,
            "target_id": target_id,
            "details": safe_details,
        }, sort_keys=True, separators=(",", ":"))
    transaction.on_commit(lambda: logger.info("social_audit %s", log_payload))
    return event
