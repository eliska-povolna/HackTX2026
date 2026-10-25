"""Demo file attachments for planning/run context.

In-memory store (survives for process lifetime). Text excerpts are included in
LLM planning prompts; binaries are accepted but not OCR'd.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_EXCERPT_CHARS = 6000
MAX_ATTACHMENTS = 40

_ALLOWED_TEXT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "application/json",
    "application/xml",
    "text/xml",
}


@dataclass
class AttachmentRecord:
    id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: str
    text_excerpt: str = ""
    raw_bytes: bytes = field(repr=False, default=b"")


_lock = threading.Lock()
_store: dict[str, AttachmentRecord] = {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_filename(name: str) -> str:
    base = (name or "upload.bin").replace("\\", "/").split("/")[-1]
    base = re.sub(r"[^\w.\- ()\[\]]+", "_", base).strip("._") or "upload.bin"
    return base[:180]


def _extract_text(content_type: str, filename: str, data: bytes) -> str:
    ctype = (content_type or "").split(";")[0].strip().lower()
    lower_name = filename.lower()
    looks_text = (
        ctype.startswith("text/")
        or ctype in _ALLOWED_TEXT_TYPES
        or lower_name.endswith((".txt", ".md", ".csv", ".json", ".xml", ".html", ".log"))
    )
    if not looks_text:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            text = ""
    else:
        return ""
    text = text.replace("\x00", " ").strip()
    if len(text) > MAX_EXCERPT_CHARS:
        return text[:MAX_EXCERPT_CHARS] + "\n…[truncated]"
    return text


def save_upload(
    *,
    filename: str,
    content_type: str,
    data: bytes,
) -> AttachmentRecord:
    if not data:
        raise ValueError("Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File exceeds {MAX_UPLOAD_BYTES} byte limit")

    safe_name = _safe_filename(filename)
    excerpt = _extract_text(content_type, safe_name, data)
    record = AttachmentRecord(
        id=f"att-{uuid4().hex[:12]}",
        filename=safe_name,
        content_type=(content_type or "application/octet-stream").split(";")[0].strip()
        or "application/octet-stream",
        size_bytes=len(data),
        created_at=_utcnow(),
        text_excerpt=excerpt,
        raw_bytes=data,
    )
    with _lock:
        if len(_store) >= MAX_ATTACHMENTS:
            # Drop oldest
            oldest = min(_store.values(), key=lambda r: r.created_at)
            _store.pop(oldest.id, None)
        _store[record.id] = record
    logger.info(
        "Stored attachment %s (%s, %s bytes, excerpt=%s)",
        record.id,
        record.filename,
        record.size_bytes,
        bool(excerpt),
    )
    return record


def get_attachment(attachment_id: str) -> Optional[AttachmentRecord]:
    with _lock:
        return _store.get(attachment_id)


def list_attachments() -> list[AttachmentRecord]:
    with _lock:
        return sorted(_store.values(), key=lambda r: r.created_at, reverse=True)


def delete_attachment(attachment_id: str) -> bool:
    with _lock:
        return _store.pop(attachment_id, None) is not None


def planning_context_for_ids(attachment_ids: list[str]) -> list[dict]:
    """Non-secret summaries for the LLM planner prompt."""
    out: list[dict] = []
    for aid in attachment_ids:
        rec = get_attachment(aid)
        if rec is None:
            out.append({"id": aid, "missing": True})
            continue
        out.append(
            {
                "id": rec.id,
                "filename": rec.filename,
                "content_type": rec.content_type,
                "size_bytes": rec.size_bytes,
                "text_excerpt": rec.text_excerpt
                or "(binary or non-text file — no extractable text)",
            }
        )
    return out


def clear_all_for_tests() -> None:
    with _lock:
        _store.clear()
