"""
Scraper for Xinhua Military (新华军事) — www.xinhuanet.com/mil/

NOTE — NOT YET FUNCTIONAL (requires JS rendering support):

The Xinhua military section at xinhuanet.com/mil/ renders article listings
entirely via JavaScript API calls to xhpfmapi.zhongguowangshi.com.  The
static HTML delivered to requests-based clients contains only 2020-era
articles baked into the template; no current content is accessible without
executing JavaScript.

This stub returns an empty list and logs a warning on every call.  To make
this scraper functional, one of the following approaches is needed:

  Option A — Headless browser: Use Playwright or Selenium to render the
    listing page and extract dynamically-loaded article links.

  Option B — Reverse-engineer the API: The listing page issues calls to
    xhpfmapi.zhongguowangshi.com with channel IDs.  If the military channel
    ID and API contract can be documented, a direct HTTP request to the API
    endpoint would work without JS execution.

  Option C — Substitute source: Replace xinhua_mil with a different static
    source that covers similar ground (e.g. chinamil.com.cn Chinese edition).

This work is deferred.  The source is registered in the DB with is_active=1
so it appears in source counts, but produces 0 articles per run until fixed.

See docs/v2_roadmap.md (P3) for tracking.
"""

from datetime import date
from typing import Optional

from scraper.base import BaseScraper


class XinhuaMilScraper(BaseScraper):
    """Stub scraper for Xinhua Military (JS-rendered, not yet functional)."""

    def __init__(self, target_date: Optional[date] = None) -> None:
        super().__init__("xinhua_mil", target_date=target_date)

    def get_article_urls(self) -> list[str]:
        self.logger.warning(
            "xinhua_mil scraper is not yet functional: "
            "xinhuanet.com/mil/ requires JavaScript rendering. "
            "See scraper/sources/xinhua_mil.py for implementation options."
        )
        return []

    def parse_article(self, url: str, html: str) -> Optional[dict]:
        return None
