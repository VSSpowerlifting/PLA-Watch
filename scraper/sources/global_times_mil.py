"""
Scraper for Global Times — Military section (环球时报·军事)
https://www.globaltimes.cn/china/military/

Global Times is a CCP-affiliated tabloid published under People's Daily.
Its military coverage is English-language, often more sensational than
official PLA Daily output but useful for tracking official narrative aimed
at international audiences and for triangulating PLA signaling.

URL structure (verified May 2026):
  Listing page:  https://www.globaltimes.cn/china/military/
  Article page:  https://www.globaltimes.cn/page/YYYYMM/{numeric_id}.shtml

Date filtering: The listing page includes <div class="source_time"> elements
with text in the format "By Author  |  YYYY/M/D H:MM:SS".  The year/month
portion of the article URL also encodes the publication month.

Article structure (verified May 2026):
  Title:  <div class="article_title"> text content
  Date:   <div class="source_time"> — regex parses "YYYY/M/D" portion
  Body:   <div class="article_content"> → <p> tags (first occurrence only;
          subsequent div.article_content elements are related-article embeds)
"""

import re
from datetime import date
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper.base import BaseScraper

_BASE = "https://www.globaltimes.cn"
_LISTING_URL = f"{_BASE}/china/military/"

# Article URL: /page/YYYYMM/ID.shtml
_ARTICLE_URL_RE = re.compile(
    r"https?://www\.globaltimes\.cn/page/(\d{4})(\d{2})/\d+\.shtml$"
)


class GlobalTimesMilScraper(BaseScraper):
    """Scrapes military articles from the Global Times."""

    def __init__(self, target_date: Optional[date] = None) -> None:
        super().__init__("global_times_mil", target_date=target_date)

    # ── Listing page ──────────────────────────────────────────────────────────

    def get_article_urls(self) -> list[str]:
        html = self.fetch(_LISTING_URL)
        if not html:
            self.logger.warning("Could not fetch Global Times military listing")
            return []

        soup = self.parse(html)
        today_str = self.target_date.strftime("%Y-%m-%d")
        target_ym = self.target_date.strftime("%Y%m")     # e.g. "202605"
        target_year = str(self.target_date.year)          # e.g. "2026"
        target_month = str(self.target_date.month)        # e.g. "5" (no zero-pad)
        target_day = str(self.target_date.day)            # e.g. "7"

        seen: set[str] = set()

        # Each article entry is a pair: <a href> (link) + <div class="source_time">
        # (date).  Walk all links and validate against the date shown in source_time.
        for a in soup.find_all("a", href=True):
            href = urljoin(_LISTING_URL, a["href"])
            m = _ARTICLE_URL_RE.match(href)
            if not m:
                continue
            # Quickly reject articles from a different month/year
            if m.group(1) + m.group(2) != target_ym:
                continue

            # Find the nearest sibling or parent <div class="source_time">
            date_text = _nearest_source_time(a)
            if date_text is None:
                # No date found; accept if the URL year/month matches today
                pass
            else:
                # Parse "By Author  |  2026/5/7 18:37:07"
                date_match = re.search(
                    r"(\d{4})/(\d{1,2})/(\d{1,2})", date_text
                )
                if date_match:
                    y, mo, d = date_match.groups()
                    article_date = f"{y}-{int(mo):02d}-{int(d):02d}"
                    if article_date != today_str:
                        continue

            if href not in seen:
                seen.add(href)

        self.logger.debug("Global Times military: %d today's articles", len(seen))
        return list(seen)

    # ── Article parsing ───────────────────────────────────────────────────────

    def parse_article(self, url: str, html: str) -> Optional[dict]:
        soup = self.parse(html)

        title = self._extract_title(soup)
        if not title:
            self.logger.debug("No title found, skipping: %s", url)
            return None

        text = self._extract_text(soup)
        pub_date = self._extract_date(soup, url)

        return {
            "url":            url,
            "source_slug":    self.source_slug,
            "title_original": title,
            "text_original":  text,
            "published_date": pub_date,
        }

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        div = soup.find("div", class_="article_title")
        if div:
            text = div.get_text(strip=True)
            if text:
                return text
        h1 = soup.find("h1")
        if h1:
            text = h1.get_text(strip=True)
            if text:
                return text
        return None

    def _extract_text(self, soup: BeautifulSoup) -> str:
        # Use the first div.article_content (subsequent ones are related-article embeds)
        content_div = soup.find("div", class_="article_content")
        if content_div:
            paras = [
                p.get_text(strip=True)
                for p in content_div.find_all("p")
                if len(p.get_text(strip=True)) > 30
                   and p.get("class") != ["picture"]
            ]
            if paras:
                return "\n".join(paras)

        # Fallback: all substantive <p> tags without class
        paragraphs = [
            p.get_text(strip=True)
            for p in soup.find_all("p")
            if not p.get("class")
               and len(p.get_text(strip=True)) > 40
        ]
        return "\n".join(paragraphs)

    def _extract_date(self, soup: BeautifulSoup, url: str) -> str:
        # Primary: <div class="source_time"> — "By X  |  2026/5/7 18:37:07"
        for div in soup.find_all("div", class_=lambda c: c and "source_time" in c):
            text = div.get_text(strip=True)
            m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", text)
            if m:
                y, mo, d = m.groups()
                return f"{y}-{int(mo):02d}-{int(d):02d}"

        # Fallback: year/month from URL, day unknown → use target date
        m = _ARTICLE_URL_RE.match(url)
        if m:
            year, month = m.group(1), m.group(2)
            if year == str(self.target_date.year) and int(month) == self.target_date.month:
                return self.target_date.isoformat()

        return self.target_date.isoformat()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _nearest_source_time(tag) -> Optional[str]:
    """
    Walk up the DOM tree from <a> looking for a sibling or ancestor
    <div class="source_time"> that carries the article's publication date.
    Checks parent, grandparent, and great-grandparent elements.
    """
    node = tag
    for _ in range(4):
        node = node.parent
        if node is None:
            return None
        # Look at siblings of the current node
        for sibling in node.find_all("div", class_=lambda c: c and "source_time" in c):
            text = sibling.get_text(strip=True)
            if re.search(r"\d{4}/\d{1,2}/\d{1,2}", text):
                return text
    return None
