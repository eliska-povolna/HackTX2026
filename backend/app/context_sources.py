"""Built-in + file context sources (no secrets vault)."""

from __future__ import annotations

from typing import Any, Optional

from app.attachments import list_attachments

# Built-in catalog IDs used by Toyota runs.
TOYOTA_CUSTOMER = "toyota_customer"
NESSIE_BANKING = "nessie_banking"
BUILTIN_IDS = (TOYOTA_CUSTOMER, NESSIE_BANKING)


def default_builtin_ids() -> list[str]:
    return list(BUILTIN_IDS)


def resolve_enabled_ids(requested: Optional[list[str]]) -> set[str]:
    """Empty / None → all built-ins. Explicit list → intersection with known ids + file:*."""
    if not requested:
        return set(default_builtin_ids())
    enabled: set[str] = set()
    for sid in requested:
        if sid in BUILTIN_IDS or sid.startswith("file:"):
            enabled.add(sid)
        elif sid.startswith("att-") or sid.startswith("attachment-"):
            enabled.add(f"file:{sid}")
        else:
            # Treat bare attachment ids as file sources when they match uploads.
            enabled.add(f"file:{sid}" if not sid.startswith("file:") else sid)
    return enabled


def toyota_enabled(enabled: set[str]) -> bool:
    return TOYOTA_CUSTOMER in enabled


def nessie_enabled(enabled: set[str]) -> bool:
    return NESSIE_BANKING in enabled


def catalog(*, include_files: bool = True) -> list[dict[str, Any]]:
    """Non-secret catalog for GET /api/context-sources."""
    items: list[dict[str, Any]] = [
        {
            "id": TOYOTA_CUSTOMER,
            "type": "builtin",
            "label": "Toyota customer context",
            "status": "AVAILABLE",
            "dismissible": True,
            "description": "Synthetic Toyota profile, vehicle, lease, and usage signals.",
            "executor_hint": "TOOL",
        },
        {
            "id": NESSIE_BANKING,
            "type": "builtin",
            "label": "Capital One Nessie banking",
            "status": "AVAILABLE",
            "dismissible": True,
            "description": "Live (or fixture) banking accounts and purchases for FinancialContext.",
            "executor_hint": "TOOL",
        },
    ]
    if include_files:
        for rec in list_attachments():
            items.append(
                {
                    "id": f"file:{rec.id}",
                    "type": "file",
                    "label": rec.filename,
                    "status": "AVAILABLE",
                    "dismissible": True,
                    "description": "Uploaded demo context file.",
                    "executor_hint": "FILE",
                    "attachment_id": rec.id,
                }
            )
    return items
