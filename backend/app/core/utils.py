"""Small, dependency-free helpers shared across layers."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    """Timezone-aware UTC now. Always use this instead of ``datetime.utcnow``."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    """Primary-key generator: opaque 32-char hex UUID4 string."""
    return uuid.uuid4().hex


def safe_json_loads(raw: str | None) -> Any | None:
    """Parse JSON, returning ``None`` instead of raising on malformed input."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


_NORMALIZE_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_text(text: str) -> str:
    """Lowercase and strip punctuation/whitespace — used for duplicate detection."""
    return _NORMALIZE_RE.sub("", (text or "").lower())


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_comments(raw: str | list | None) -> list[dict[str, Any]]:
    """Coerce heterogeneous client input into a uniform comment list.

    Every entry always exposes the same five keys, so the frontend never has to
    branch on shape::

        {"index": int, "content": str, "author": str|None,
         "likes": int, "created_at": str|None}

    ``created_at`` is echoed verbatim (not re-parsed) — the burst heuristic
    parses it internally.

    Accepts:
      * JSON array of strings              -> content only
      * JSON array of objects             -> keys normalized
      * newline-separated plain text      -> one comment per non-empty line
    """
    if not raw:
        return []

    items: Any = raw
    if isinstance(raw, str):
        items = safe_json_loads(raw)
        if items is None:
            items = [line.strip() for line in raw.splitlines() if line.strip()]

    if not isinstance(items, list):
        items = [items]

    comments: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if isinstance(item, str):
            content = item.strip()
            if not content:
                continue
            comments.append(
                {"index": index, "content": content, "author": None, "likes": 0, "created_at": None}
            )
        elif isinstance(item, dict):
            content = str(
                item.get("content") or item.get("text") or item.get("comment") or ""
            ).strip()
            if not content:
                continue
            comments.append(
                {
                    "index": index,
                    "content": content,
                    "author": item.get("author") or item.get("user") or item.get("nickname"),
                    "likes": _coerce_int(item.get("likes") or item.get("like_count")),
                    "created_at": item.get("created_at") or item.get("time"),
                }
            )
    return comments


def normalize_account_history(raw: str | dict | list | None) -> dict[str, Any] | None:
    """Normalize caller-supplied account history for the T (账号可信度) dimension.

    Accepts a JSON string or a mapping — either a bare list of posts, or an object
    carrying account fields. Every post receives a stable unique id (the engine
    requires unique ids) and a uniform key set.
    """
    if not raw:
        return None
    payload = safe_json_loads(raw) if isinstance(raw, str) else raw
    if payload is None:
        return None
    if isinstance(payload, list):
        payload = {"posts": payload}
    if not isinstance(payload, dict):
        return None

    posts: list[dict[str, Any]] = []
    for index, item in enumerate(payload.get("posts") or []):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        posts.append(
            {
                "id": str(item.get("id") or f"post-{index + 1}"),
                "text": str(item.get("text") or ""),
                "published_at": item.get("published_at") or item.get("created_at"),
                "like_count": _coerce_int(item.get("like_count") or item.get("likes")),
                "comment_count": _coerce_int(item.get("comment_count") or item.get("comments")),
                "source_url": item.get("source_url") or item.get("url"),
            }
        )

    seen: set[str] = set()
    for index, post in enumerate(posts):
        if post["id"] in seen:
            post["id"] = f"{post['id']}-{index + 1}"
        seen.add(post["id"])

    return {
        "handle": payload.get("handle") or payload.get("account_id") or payload.get("nickname"),
        "profile_text": payload.get("profile_text") or payload.get("signature"),
        "follower_count": _coerce_int(payload.get("follower_count") or payload.get("followers")),
        "total_post_count": _coerce_int(
            payload.get("total_post_count") or payload.get("post_count")
        ),
        "posts": posts,
    }
