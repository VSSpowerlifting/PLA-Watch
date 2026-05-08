"""
LLM analysis engine for PLA Watch.

Runs four tasks per article using the Anthropic Messages API:
  1. Relevance scoring (Chinese text → 0.0–1.0 score)
  2. Translation     (Chinese text → English title + body)
  3. Analytic summary (English text → 2–3 sentence CFR-voice summary)
  4. Categories + significance flag (English text → taxonomy tags + flag)

Steps 3 and 4 run in parallel after translation completes.

Token budgets and temperatures per task:
  Relevance:   max_tokens=500,  temperature=0.0  (deterministic classification)
  Translation: max_tokens=4000, temperature=0.3  (fluent, close rendering)
  Summary:     max_tokens=1000, temperature=0.3  (analytic writing, slight variation)
  Categories:  max_tokens=500,  temperature=0.0  (deterministic classification)
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import anthropic

from analysis.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    VALID_CATEGORIES,
    build_category_messages,
    build_relevance_messages,
    build_summary_messages,
    build_translation_messages,
)
from config import ANALYSIS_MODEL, ANTHROPIC_API_KEY, RELEVANCE_THRESHOLD

logger = logging.getLogger(__name__)


class AnalysisError(Exception):
    """Raised when an API call fails or returns unparseable output."""


class Analyzer:
    """
    Runs all four LLM analysis tasks.

    Thread-safe: the Anthropic client uses per-request HTTP connections.
    Multiple threads in ThreadPoolExecutor can safely call _call() concurrently.
    """

    def __init__(self) -> None:
        if not ANTHROPIC_API_KEY:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Add it to .env or export it."
            )
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # ── Core API call ─────────────────────────────────────────────────────────

    # The system prompt is identical across all four tasks and sent on every
    # API call — up to 4 times per article, 100+ times per daily run at full
    # scale.  Marking it ephemeral tells Anthropic to cache the compiled KV
    # state for 5 minutes, cutting cached-token cost by ~90% on subsequent
    # calls within that window.
    _SYSTEM_WITH_CACHE: list[dict] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    def _call(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Single API call. Returns raw text. Raises AnalysisError on failure."""
        try:
            response = self._client.messages.create(
                model=ANALYSIS_MODEL,
                system=self._SYSTEM_WITH_CACHE,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.content[0].text
        except anthropic.APIStatusError as exc:
            raise AnalysisError(f"API status error ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AnalysisError(f"API connection error: {exc}") from exc

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """
        Parse JSON from LLM output.

        Belt-and-suspenders approach:
          1. Strip any leading ```json/``` fence and trailing ``` fence via
             regex — handles the common case where the model wraps its output.
          2. Fallback: locate the outermost { ... } in the raw string in case
             the fence was non-standard or there was unexpected surrounding text.
        """
        cleaned = raw.strip()
        # Remove leading fence: optional whitespace, ```, optional "json", newline
        cleaned = re.sub(r"^\s*```(?:json)?\s*\n?", "", cleaned)
        # Remove trailing fence: optional newline, ```, optional whitespace
        cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned).strip()

        # Fallback: if what remains still doesn't look like JSON, extract braces
        if not cleaned.startswith("{"):
            start = raw.find("{")
            end   = raw.rfind("}")
            if start != -1 and end > start:
                cleaned = raw[start : end + 1]

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            # TODO (v2): Replace this regex approach with the Anthropic API's
            # native structured output / tool-use mode, which enforces a JSON
            # schema at the API level and eliminates formatting drift entirely.
            #
            # Observed failure mode: long doctrinal and historical-essay
            # translations (3000+ character responses) intermittently produce
            # formatting drift that isn't caught by the fence-stripping regex
            # above — e.g., nested code fences, prose introductions, or
            # mid-response formatting breaks in the body_en field.  We've seen
            # this specifically on ancient military history essays (Battle of
            # Changping) and long PLA doctrinal pieces.
            #
            # Escalating the regex is the wrong direction.  The clean fix is:
            #   client.messages.create(..., tools=[translation_tool_schema],
            #                          tool_choice={"type": "tool", ...})
            # which returns structured data rather than free-text JSON.
            # That work is scoped to a future session.
            raise AnalysisError(
                f"JSON parse failed. Raw output was:\n{raw[:400]}"
            ) from exc

    # ── Individual task methods ───────────────────────────────────────────────

    def score_relevance(self, title: str, body: str) -> tuple[float, str]:
        """
        Returns (score, reasoning).
        Score is clamped to [0.0, 1.0] as a safeguard against out-of-range values.
        """
        messages = build_relevance_messages(title, body)
        raw  = self._call(messages, max_tokens=500, temperature=0.0)
        data = self._parse_json(raw)
        score = float(max(0.0, min(1.0, data["score"])))
        return score, str(data.get("reasoning", ""))

    def translate(self, title: str, body: str) -> tuple[str, str]:
        """Returns (title_en, body_en)."""
        messages = build_translation_messages(title, body)
        raw  = self._call(messages, max_tokens=4000, temperature=0.3)
        data = self._parse_json(raw)
        return str(data["title_en"]), str(data["body_en"])

    def summarize(self, title_en: str, body_en: str) -> str:
        messages = build_summary_messages(title_en, body_en)
        raw  = self._call(messages, max_tokens=1000, temperature=0.3)
        data = self._parse_json(raw)
        return str(data["summary"])

    def categorize(
        self, title_en: str, body_en: str
    ) -> tuple[list[str], bool, Optional[str]]:
        """
        Returns (categories, is_significant, significance_reason).
        Category slugs are validated against VALID_CATEGORIES; any hallucinated
        values returned by the model are silently dropped to prevent bad DB writes.
        """
        messages = build_category_messages(title_en, body_en)
        raw  = self._call(messages, max_tokens=500, temperature=0.0)
        data = self._parse_json(raw)

        categories = [c for c in data.get("categories", []) if c in VALID_CATEGORIES]
        is_significant = bool(data.get("significance", False))
        reason: Optional[str] = data.get("significance_reason") if is_significant else None

        return categories, is_significant, reason

    # ── Full pipeline ─────────────────────────────────────────────────────────

    def analyze(self, title_zh: str, body_zh: str) -> Optional[dict]:
        """
        Run the full four-task pipeline for one article.

        Returns a dict with all analysis fields populated, or None only if
        a hard failure occurs before translation (relevance failure still
        returns a partial result so it can be stored for the audit trail).

        Return keys:
            relevance_score, relevance_reasoning, passed_relevance,
            title_english, text_english, summary_english,
            categories, is_significant, significance_reasoning,
            model_id, prompt_version
        """
        # ── Step 1: Relevance ─────────────────────────────────────────────────
        try:
            score, reasoning = self.score_relevance(title_zh, body_zh)
        except AnalysisError as exc:
            logger.error("Relevance scoring failed: %s", exc)
            return None

        if score < RELEVANCE_THRESHOLD:
            logger.debug(
                "Below relevance threshold (%.2f): %.60s", score, title_zh
            )
            return {
                "relevance_score":     score,
                "relevance_reasoning": reasoning,
                "passed_relevance":    False,
                "model_id":            ANALYSIS_MODEL,
                "prompt_version":      PROMPT_VERSION,
            }

        # ── Step 2: Translation ───────────────────────────────────────────────
        try:
            title_en, body_en = self.translate(title_zh, body_zh)
        except AnalysisError as exc:
            logger.error("Translation failed: %s", exc)
            # Return partial result so relevance data isn't lost
            return {
                "relevance_score":     score,
                "relevance_reasoning": reasoning,
                "passed_relevance":    True,
                "model_id":            ANALYSIS_MODEL,
                "prompt_version":      PROMPT_VERSION,
            }

        # ── Steps 3 + 4: Summary and categories (parallel) ───────────────────
        summary             = ""
        categories: list[str]   = []
        is_significant          = False
        significance_reason: Optional[str] = None

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_summary    = pool.submit(self.summarize,  title_en, body_en)
            f_categories = pool.submit(self.categorize, title_en, body_en)

            try:
                summary = f_summary.result()
            except AnalysisError as exc:
                logger.error("Summary generation failed: %s", exc)

            try:
                categories, is_significant, significance_reason = f_categories.result()
            except AnalysisError as exc:
                logger.error("Categorization failed: %s", exc)

        return {
            "relevance_score":        score,
            "relevance_reasoning":    reasoning,
            "passed_relevance":       True,
            "title_english":          title_en,
            "text_english":           body_en,
            "summary_english":        summary,
            "categories":             categories,
            "is_significant":         is_significant,
            "significance_reasoning": significance_reason,
            "model_id":               ANALYSIS_MODEL,
            "prompt_version":         PROMPT_VERSION,
        }
