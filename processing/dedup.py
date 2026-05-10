"""
Chinese-title-based deduplication for PLA Daily syndicated reposts.

PLA Daily (81.cn) reposts the same article across multiple service-branch
sub-paths. The URL differs, the body text often has small variations
(headers, footers, paragraph ordering), but the Chinese title is reused
verbatim. The existing URL + content-hash dedup misses these because both
signals diverge across reposts.

Design choice: title hash is the sole grouping key. Body-prefix matching
was considered as a confirmation step but rejected — body content is not
stable across reposts (the very problem this module exists to solve), so
a prefix hash will sometimes split a true duplicate group and keep both
copies. PLA Daily reuses titles deliberately; that makes the title the
only reliable signal. The body_prefix_hash() helper is kept in the module
because it's cheap and may be useful elsewhere, but dedup_articles() does
not call it.
"""

import hashlib
import re
from typing import Optional
from urllib.parse import urlparse


# ── Section → canonical priority ─────────────────────────────────────────────
#
# Built from scraper/sources/pla_daily.py _SECTIONS. Higher = more canonical.
# When the same article appears in both 要闻 (main news) and a service-branch
# section, prefer the main-news copy.
_SOURCE_PRIORITY: dict[str, int] = {
    # Main news — most canonical
    "yw_208727":  100,  # 要闻 (Top News)

    # Service branches
    "bz_208549":   90,  # 备战 (Combat Readiness / Army-adjacent)
    "hj_208557":   90,  # 海军 (Navy)
    "kj_208559":   90,  # 空军 (Air Force)
    "hjj_208561":  90,  # 火箭军 (Rocket Force)

    # Paramilitary
    "wj_208567":   85,  # 武警 (PAP)

    # Higher-level command and policy sections
    "jw_208551":   80,  # 军委 (CMC)
    "zq_208553":   80,  # 战区 (Theater Commands)

    # Other known sections from the scraper
    "fyr":         50,  # 发言人 (Spokesperson)
}

_DEFAULT_PRIORITY = 50


# ── Title normalization ─────────────────────────────────────────────────────

# Leading bracket tags like 【双语】, 【独家】, [Bilingual], [Exclusive], etc.
# Matches both CJK 【】 and ASCII [] brackets at the start of the string.
_LEADING_TAG_RE = re.compile(r"^\s*(?:【[^】]*】|\[[^\]]*\])\s*")


def _normalize_title(title: str) -> str:
    """
    Strip whitespace and leading bracket tags like 【双语】 or [Bilingual].
    Preserves all CJK characters and Chinese punctuation.
    """
    if not title:
        return ""
    t = title.strip()
    # Strip repeated leading tags, e.g. "【双语】【独家】标题"
    while True:
        stripped = _LEADING_TAG_RE.sub("", t)
        if stripped == t:
            break
        t = stripped
    return t.strip()


def title_hash(title: str) -> str:
    """SHA-1 of the normalized title. Empty string if title is empty."""
    norm = _normalize_title(title or "")
    if not norm:
        return ""
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def body_prefix_hash(body: str, n: int = 200) -> str:
    """
    SHA-1 of the first n non-whitespace characters of the body.

    Not used by dedup_articles() (see module docstring). Retained as a
    cheap utility for callers that need a body fingerprint.
    """
    if not body:
        return ""
    compact = re.sub(r"\s+", "", body)
    if not compact:
        return ""
    return hashlib.sha1(compact[:n].encode("utf-8")).hexdigest()


# ── URL → section / priority ────────────────────────────────────────────────

_SECTION_RE = re.compile(r"^/([^/]+)/")


def url_section(url: str) -> str:
    """
    Extract the section identifier from an 81.cn URL.
    e.g. "yw_208727" from "http://www.81.cn/yw_208727/16459467.html".
    Returns empty string if no section can be parsed.
    """
    if not url:
        return ""
    try:
        path = urlparse(url).path
    except Exception:
        return ""
    m = _SECTION_RE.match(path)
    return m.group(1) if m else ""


def source_priority(url: str) -> int:
    """
    Priority score for a given URL — higher means more canonical.
    Known mapped sections use _SOURCE_PRIORITY; other sections present on
    81.cn but not in the map fall through to 70; truly unknown / non-81.cn
    URLs get 50.
    """
    section = url_section(url)
    if section in _SOURCE_PRIORITY:
        return _SOURCE_PRIORITY[section]
    if section:
        # Section parsed but not in our map — likely a real 81.cn section we
        # haven't seen yet. Worth more than total-unknown but less than mapped.
        return 70
    return _DEFAULT_PRIORITY


# ── Dedup ────────────────────────────────────────────────────────────────────

def dedup_articles(articles: list[dict]) -> list[dict]:
    """
    Group articles by Chinese-title hash and keep the highest-priority copy
    from each group. PLA Daily reposts the same piece across multiple
    service-branch sub-paths under the same Chinese title; this filter
    collapses those into one before LLM translation runs.

    Each input dict is expected to carry either:
        - `title_zh` / `body_zh`  (spec naming), or
        - `title_original` / `text_original`  (existing pipeline naming).
    The function reads `title_zh` first and falls back to `title_original`;
    same for body. Articles with no usable Chinese title pass through
    unchanged (we have no reliable grouping signal for them).

    Returns a new list; does not mutate the input.
    """
    groups: dict[str, list[dict]] = {}
    passthrough: list[dict] = []

    for article in articles:
        title = article.get("title_zh") or article.get("title_original") or ""
        h = title_hash(title)
        if not h:
            passthrough.append(article)
            continue
        groups.setdefault(h, []).append(article)

    deduped: list[dict] = []
    for h, group in groups.items():
        if len(group) == 1:
            deduped.append(group[0])
            continue
        # Highest priority first; tie-break by shorter URL (usually cleaner).
        winner = max(
            group,
            key=lambda a: (source_priority(a.get("url", "")),
                           -len(a.get("url", ""))),
        )
        deduped.append(winner)

    # Preserve input order roughly: re-sort by first appearance.
    order = {id(a): i for i, a in enumerate(articles)}
    deduped.sort(key=lambda a: order.get(id(a), 0))
    passthrough.sort(key=lambda a: order.get(id(a), 0))

    return deduped + passthrough
