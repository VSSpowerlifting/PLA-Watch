"""
Scraper for PLA Daily (解放军报) — www.81.cn

PLA Daily is published directly under the CMC's Political Work Department and
is the primary outlet for CMC-attributed statements and official PLA narrative.

URL structure (verified May 2026):
  Listing page:  http://www.81.cn/{section_slug}_{section_id}/index.html
  Article page:  http://www.81.cn/{section_slug}_{section_id}/{numeric_id}.html

Date filtering is done via link text on listing pages, which include
the publication datetime (e.g. "2026-05-07 06:00").  Since the article
URL path carries no date, this is the most reliable same-day filter.

Article structure (verified May 2026):
  Title:  <h2> (no class, first h2 on page)
  Date:   <div class="container artichle-info"> → <p> containing "发布：YYYY-MM-DD"
  Body:   <p class="ueditor-text-p_display"> within <ul class="row m-t-list">
"""

import re
from datetime import date
from typing import Optional
from urllib.parse import urljoin


from bs4 import BeautifulSoup

from scraper.base import BaseScraper

# Sections with highest analytical value for military/security monitoring.
# slug_id → display label (for logging)
_SECTIONS: dict[str, str] = {
    "yw_208727":   "要闻 (Top News)",
    "jw_208551":   "军委 (CMC)",
    "zq_208553":   "战区 (Theater Commands)",
    "bz_208549":   "备战 (Combat Readiness)",
    "hj_208557":   "海军 (Navy)",
    "kj_208559":   "空军 (Air Force)",
    "hjj_208561":  "火箭军 (Rocket Force)",
    "wj_208567":   "武警 (PAP)",
    "fyr":         "发言人 (Spokesperson)",
}

_BASE = "http://www.81.cn"


class PLADailyScraper(BaseScraper):
    """Scrapes articles from PLA Daily (81.cn) for a given target date."""

    def __init__(self, target_date: Optional[date] = None) -> None:
        super().__init__("pla_daily", target_date=target_date)

    # ── Listing pages ─────────────────────────────────────────────────────────

    def get_article_urls(self) -> list[str]:
        today_str = self.target_date.strftime("%Y-%m-%d")  # e.g. "2026-05-07"
        seen: set[str] = set()

        for section_id, label in _SECTIONS.items():
            listing_url = f"{_BASE}/{section_id}/index.html"
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

        text  = self._extract_text(soup)
        pub_date = self._extract_date(soup)

        return {
            "url":            url,
            "source_slug":    self.source_slug,
            "title_original": title,
            "text_original":  text,
            "published_date": pub_date,
        }

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        # 81.cn uses <h2> for article titles (no class)
        h2 = soup.find("h2")
        if h2:
            text = h2.get_text(strip=True)
            if text:
                return text
        # Fallback to <h1>
        h1 = soup.find("h1")
        if h1:
            text = h1.get_text(strip=True)
            if text:
                return text
        return None

    def _extract_text(self, soup: BeautifulSoup) -> str:
        # Primary: paragraphs with class "ueditor-text-p_display"
        # within the <ul class="row m-t-list"> content container
        content_ul = soup.find("ul", class_=lambda c: c and "m-t-list" in c)
        if content_ul:
            paras = content_ul.find_all("p", class_=lambda c: c and "ueditor" in c)
            if paras:
                return "\n".join(p.get_text(strip=True) for p in paras)
            # Fallback: all <p> within the container
            text = content_ul.get_text(separator="\n", strip=True)
            if len(text) > 50:
                return text

        # Broad fallback: collect substantive <p> tags anywhere on the page
        paragraphs = [
            p.get_text(strip=True)
            for p in soup.find_all("p")
            if len(p.get_text(strip=True)) > 30
               and "版权" not in p.get_text()   # Exclude copyright notices
               and "责任编辑" not in p.get_text()
        ]
        return "\n".join(paragraphs)

    def _extract_date(self, soup: BeautifulSoup) -> str:
        # Primary: <div class="container artichle-info"> → <p> with "发布："
        info_div = soup.find("div", class_=lambda c: c and "artichle-info" in c)
        if info_div:
            text = info_div.get_text()
            match = re.search(r"发布[：:]\s*(\d{4}-\d{2}-\d{2})", text)
            if match:
                return match.group(1)

        # Fallback: any text on the page containing a recognizable date
        for el in soup.find_all(string=re.compile(r"发布[：:]\s*\d{4}-\d{2}-\d{2}")):
            match = re.search(r"(\d{4}-\d{2}-\d{2})", el)
            if match:
                return match.group(1)

        return date.today().isoformat()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_article_url(url: str) -> bool:
    """
    True if the URL looks like a 81.cn article page.
    Pattern: http://www.81.cn/{section}/{numeric_id}.html
    Excludes index pages, video subdomains, and external links.
    """
    return bool(
        re.match(r"https?://www\.81\.cn/[^/]+/\d+\.html$", url)
    )
