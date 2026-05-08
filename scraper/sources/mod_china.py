"""
Scraper for Ministry of National Defense (国防部网) — www.mod.gov.cn

MOD China is the official press office of the Ministry of National Defense.
It publishes spokesperson press releases, military diplomacy readouts,
theater command and service branch news, and senior leadership activities.

URL structure (verified May 2026):
  Listing page:  http://www.mod.gov.cn/gfbw/{section_path}/index.html
  Article page:  http://www.mod.gov.cn/gfbw/{section_path}/{numeric_id}.html

Date filtering: Links on listing pages include the publication datetime in
the link text (e.g. "标题文字2026-05-07 15:00").  The date is appended
directly to the title text without a separator.

Article structure (verified May 2026):
  Title:  <h1> (first on page)
  Date:   regex YYYY-MM-DD in full page text (present in article-info area)
  Body:   <p class="ueditor-text-p_display"> — same CMS as 81.cn
"""

import re
from datetime import date
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper.base import BaseScraper

_BASE = "http://www.mod.gov.cn"

# Sections with highest analytical value.
# Format: path → display label
_SECTIONS: dict[str, str] = {
    "gfbw/xwfyr/yzxwfb":   "例行新闻发布 (Regular Press Releases)",
    "gfbw/xwfyr/fyrthhdjzw": "发言人谈话 (Spokesperson Q&A)",
    "gfbw/wzll/yw_214068":  "要闻 (Armed Forces Top News)",
    "gfbw/wzll/hj":         "海军 (Navy)",
    "gfbw/wzll/kj":         "空军 (Air Force)",
    "gfbw/wzll/lj":         "陆军 (Army)",
}


class MODChinaScraper(BaseScraper):
    """Scrapes articles from the Ministry of National Defense website."""

    def __init__(self, target_date: Optional[date] = None) -> None:
        super().__init__("mod_china", target_date=target_date)

    # ── Listing pages ─────────────────────────────────────────────────────────

    def get_article_urls(self) -> list[str]:
        today_str = self.target_date.strftime("%Y-%m-%d")
        seen: set[str] = set()

        for section_path, label in _SECTIONS.items():
            listing_url = f"{_BASE}/{section_path}/index.html"
            html = self.fetch(listing_url)
            if not html:
                self.logger.warning("Could not fetch section: %s", label)
                continue

            soup = self.parse(html)
            count = 0
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                full_url = urljoin(listing_url, href)
                link_text = a.get_text()

                if (
                    _is_article_url(full_url)
                    and today_str in link_text
                    and full_url not in seen
                ):
                    seen.add(full_url)
                    count += 1

            self.logger.debug("Section %s: %d today's articles", label, count)

        return list(seen)

    # ── Article parsing ───────────────────────────────────────────────────────

    def parse_article(self, url: str, html: str) -> Optional[dict]:
        soup = self.parse(html)

        title = self._extract_title(soup)
        if not title:
            self.logger.debug("No title found, skipping: %s", url)
            return None

        text = self._extract_text(soup)
        pub_date = self._extract_date(html)

        return {
            "url":            url,
            "source_slug":    self.source_slug,
            "title_original": title,
            "text_original":  text,
            "published_date": pub_date,
        }

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        h1 = soup.find("h1")
        if h1:
            text = h1.get_text(strip=True)
            if text:
                return text
        h2 = soup.find("h2")
        if h2:
            text = h2.get_text(strip=True)
            if text:
                return text
        return None

    def _extract_text(self, soup: BeautifulSoup) -> str:
        # MOD uses the same CMS as 81.cn: ueditor-text-p_display paragraphs
        paras = soup.find_all("p", class_=lambda c: c and "ueditor" in c)
        if paras:
            return "\n".join(
                p.get_text(strip=True) for p in paras
                if "ueditor-text-tushuo" not in (p.get("class") or [])
                   and len(p.get_text(strip=True)) > 10
            )

        # Fallback: substantive <p> tags anywhere
        paragraphs = [
            p.get_text(strip=True)
            for p in soup.find_all("p")
            if len(p.get_text(strip=True)) > 30
               and "版权" not in p.get_text()
               and "责任编辑" not in p.get_text()
        ]
        return "\n".join(paragraphs)

    def _extract_date(self, html: str) -> str:
        # Date appears in the article-info area as YYYY-MM-DD
        match = re.search(r"(\d{4}-\d{2}-\d{2})", html)
        if match:
            return match.group(1)
        return self.target_date.isoformat()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_article_url(url: str) -> bool:
    """
    True if the URL looks like a mod.gov.cn article page.
    Pattern: http://www.mod.gov.cn/gfbw/{path}/{numeric_id}.html
    """
    return bool(
        re.match(r"https?://www\.mod\.gov\.cn/gfbw/[^/]+(?:/[^/]+)*/\d+\.html$", url)
    )
