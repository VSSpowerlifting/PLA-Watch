"""
Two-stage relevance filter.

Stage 1 — Keyword pre-filter (fast, free)
    An article must match at least one keyword to proceed to the LLM pass.
    High recall, low precision by design: we'd rather pass a false positive
    to the LLM than drop a true positive with a keyword miss.

Stage 2 — LLM relevance scoring
    Applied only to keyword-passing candidates.  Calls Analyzer.score_relevance()
    which returns a 0.0–1.0 confidence score and one-sentence reasoning.
    Articles at or above RELEVANCE_THRESHOLD are kept; the rest are stored
    with passed_relevance=0 for the audit trail.
"""

import logging
from typing import TYPE_CHECKING, Optional

from config import RELEVANCE_KEYWORDS_EN, RELEVANCE_KEYWORDS_ZH, RELEVANCE_THRESHOLD

if TYPE_CHECKING:
    from analysis.analyzer import Analyzer

logger = logging.getLogger(__name__)


# ── Stage 1: Keyword filter ───────────────────────────────────────────────────

def passes_keyword_filter(article: dict) -> bool:
    """
    True if the article's title or body contains any relevance keyword.
    Chinese: exact substring match.  English: case-insensitive substring match.
    """
    title    = article.get("title_original", "") or ""
    text     = article.get("text_original",  "") or ""
    combined = title + " " + text

    for kw in RELEVANCE_KEYWORDS_ZH:
        if kw in combined:
            return True

    combined_lower = combined.lower()
    for kw in RELEVANCE_KEYWORDS_EN:
        if kw.lower() in combined_lower:
            return True

    return False


def keyword_filter(articles: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split into (passed, rejected). Logs counts at INFO level."""
    passed:   list[dict] = []
    rejected: list[dict] = []

    for article in articles:
        (passed if passes_keyword_filter(article) else rejected).append(article)

    logger.info(
        "Keyword filter: %d in → %d passed, %d rejected",
        len(articles), len(passed), len(rejected),
    )
    return passed, rejected


# ── Stage 2: LLM relevance scoring ───────────────────────────────────────────

def llm_relevance_check(
    articles: list[dict],
    analyzer: Optional["Analyzer"],
) -> list[tuple[dict, float, str]]:
    """
    Score each article with the LLM relevance prompt.

    If analyzer is None (no API key available), falls back to assigning
    score=1.0 with a stub note — this keeps the dry-run path functional.

    Returns list of (article, score, reasoning) tuples, one per input article.
    Articles are not filtered here; the caller decides what to do with low scores.
    """
    if analyzer is None:
        logger.warning(
            "No Analyzer provided; assigning score=1.0 to all %d articles (stub mode).",
            len(articles),
        )
        return [(a, 1.0, "stub: no API key provided") for a in articles]

    results: list[tuple[dict, float, str]] = []
    for i, article in enumerate(articles, 1):
        url = article.get("url", "?")
        try:
            score, reasoning = analyzer.score_relevance(
                article.get("title_original", ""),
                article.get("text_original",  ""),
            )
            logger.debug(
                "[%d/%d] Relevance %.2f — %s",
                i, len(articles), score, url,
            )
        except Exception as exc:
            logger.error("Relevance scoring failed for %s: %s", url, exc)
            score, reasoning = 0.0, f"error: {exc}"

        results.append((article, score, reasoning))

    return results


def apply_relevance_threshold(
    scored: list[tuple[dict, float, str]],
    threshold: float = RELEVANCE_THRESHOLD,
) -> tuple[list[tuple[dict, float, str]], list[tuple[dict, float, str]]]:
    """Split scored results into (kept, filtered) at threshold."""
    kept     = [(a, s, r) for a, s, r in scored if s >= threshold]
    filtered = [(a, s, r) for a, s, r in scored if s <  threshold]
    logger.info(
        "Relevance threshold %.2f: %d kept, %d filtered",
        threshold, len(kept), len(filtered),
    )
    return kept, filtered
