"""
Metadata normalization and content hashing.

Called before storage to ensure every article dict is well-formed
and has a stable deduplication key.
"""

import hashlib
import re
from typing import Optional


def compute_content_hash(title: Optional[str], text: Optional[str]) -> str:
    """
    SHA-256 of normalized (title + text).
    Used to detect reposts: identical content at different URLs.
    """
    normalized = _normalize(title or "") + "\n" + _normalize(text or "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_article(raw: dict) -> dict:
    """
    Ensure a raw article dict has all required fields in expected types.
    Mutates a copy; does not alter the input.
    """
    article = dict(raw)

    article["title_original"] = _normalize(article.get("title_original") or "")
    article["text_original"]  = _normalize(article.get("text_original")  or "")
    article["published_date"] = _clean_date(article.get("published_date") or "")
    article["content_hash"]   = compute_content_hash(
        article["title_original"], article["text_original"]
    )

    return article


# ── Internal ──────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Collapse whitespace and strip leading/trailing spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _clean_date(raw: str) -> str:
    """Return YYYY-MM-DD if recognizable, else empty string."""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw.strip())
    return match.group(0) if match else ""
