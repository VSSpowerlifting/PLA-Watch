"""
Base scraper class.

Provides: rate limiting, HTML caching, retry with backoff, and a
standard interface that all source-specific scrapers implement.

Cache layout:  cache/{source_slug}/{YYYY-MM-DD}/{url_sha256[:16]}.html
This means each day's raw HTML is preserved independently.  Old cache
directories can be pruned with standard filesystem tools.
"""

import hashlib
import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import (
    CACHE_DIR,
    MAX_RETRIES,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
)

# Polite user-agent that identifies the project and its purpose.
# Replace [username] with your GitHub username before deploying.
_USER_AGENT = (
    "PLAWatch/1.0 "
    "(academic OSINT research; non-commercial; "
    "source: github.com/[username]/pla-watch) "
    "Python-requests/2.x"
)


def _decode_response(resp: requests.Response) -> str:
    """
    Decode an HTTP response to a string, respecting charset declared in the
    HTML <meta> tag when the HTTP Content-Type header omits one.

    requests defaults to ISO-8859-1 when no charset is in the header (per
    RFC 2616), which misidentifies UTF-8 pages from Chinese government sites
    that declare charset in HTML but not in HTTP headers.
    """
    # Use HTTP header charset if explicitly set (and not the RFC default)
    ct = resp.headers.get("Content-Type", "")
    if "charset=" in ct.lower():
        return resp.text

    # Sniff charset from HTML <meta> tags in the first 2 KB of raw bytes
    raw = resp.content
    meta_m = re.search(rb'charset=["\']?\s*([^"\'\s;>]+)', raw[:2048])
    if meta_m:
        try:
            encoding = meta_m.group(1).decode("ascii", errors="ignore").strip()
            return raw.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass

    # Fall back to UTF-8 (better default than ISO-8859-1 for Chinese sites)
    return raw.decode("utf-8", errors="replace")


class BaseScraper(ABC):
    """Abstract base for all PLA Watch source scrapers."""

    def __init__(self, source_slug: str, target_date: Optional[date] = None) -> None:
        self.source_slug = source_slug
        self.target_date = target_date or date.today()
        self.logger = logging.getLogger(f"scraper.{source_slug}")

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._last_request_at: float = 0.0

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = REQUEST_DELAY_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)

    def _cache_path(self, url: str) -> Path:
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        # Cache directory is keyed to target_date so scraping different dates
        # doesn't overwrite each other's cached HTML.
        path = CACHE_DIR / self.source_slug / self.target_date.isoformat() / f"{url_hash}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch(self, url: str, force_refresh: bool = False) -> Optional[str]:
        """
        Fetch URL, returning raw HTML.  Caches to disk; returns cached copy
        on subsequent calls unless force_refresh=True.
        Returns None if all retries are exhausted.
        """
        cache_path = self._cache_path(url)

        if not force_refresh and cache_path.exists():
            self.logger.debug("Cache hit: %s", url)
            return cache_path.read_text(encoding="utf-8", errors="replace")

        self._rate_limit()

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
                self._last_request_at = time.monotonic()
                resp.raise_for_status()
                html = _decode_response(resp)
                cache_path.write_text(html, encoding="utf-8")
                self.logger.debug("Fetched: %s", url)
                return html
            except requests.RequestException as exc:
                self.logger.warning(
                    "Attempt %d/%d failed for %s: %s",
                    attempt, MAX_RETRIES, url, exc,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)  # Exponential backoff: 2s, 4s

        self.logger.error("All retries exhausted for %s", url)
        return None

    @staticmethod
    def parse(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    def scrape(self) -> list[dict]:
        """
        Full scrape cycle for one source.
        Returns a list of raw article dicts ready for processing.
        """
        urls = self.get_article_urls()
        self.logger.info("%s: found %d candidate URLs", self.source_slug, len(urls))

        articles: list[dict] = []
        for url in urls:
            html = self.fetch(url)
            if not html:
                continue
            article = self.parse_article(url, html)
            if article:
                articles.append(article)

        self.logger.info("%s: parsed %d articles", self.source_slug, len(articles))
        return articles

    # ── Interface ─────────────────────────────────────────────────────────────

    @abstractmethod
    def get_article_urls(self) -> list[str]:
        """
        Fetch listing page(s) and return URLs of today's articles.
        Implementations should filter to today's date where possible.
        """
        ...

    @abstractmethod
    def parse_article(self, url: str, html: str) -> Optional[dict]:
        """
        Extract article data from a fetched HTML page.

        Must return a dict with at minimum:
            url              str   — canonical article URL
            source_slug      str   — matches sources.slug in DB
            title_original   str   — headline in source language
            text_original    str   — body text in source language
            published_date   str   — YYYY-MM-DD

        Returns None if the page cannot be parsed (logged as a warning).
        """
        ...
