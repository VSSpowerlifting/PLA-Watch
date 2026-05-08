"""
Scraper for China Military Online (中国军网英文版) — eng.chinamil.com.cn

China Military Online is the English-language website of the PLA.  It
publishes translated versions of PLA Daily and chinamil.com.cn content,
making it the most accessible English-language primary source for PLA
official narrative.

Note: The homepage is at english.chinamil.com.cn but all article links
resolve to eng.chinamil.com.cn.  This scraper uses the latter directly.

URL structure (verified May 2026):
  Listing page:  http://eng.chinamil.com.cn/2025xb/{section}/index.html
  Article page:  http://eng.chinamil.com.cn/2025xb/{section}/{numeric_id}.html

Date filtering: Listing page items include the publication datetime in the
link text via a <div class="title"> element:
  "China Coast Guard patrols around Diaoyu Dao2026-05-07 18:25"

Article structure (verified May 2026):
  Title:  <div class="article-header"> → <div class="title"> → <h1>
  Date:   <div class="pub-info"> → <dd> containing "YYYY-MM-DD HH:MM:SS"
  Body:   <p class="ueditor-text-p_display">
"""

import re
from datetime import date
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper.base import BaseScraper

_BASE = "http://eng.chinamil.com.cn"

# Section paths → display labels (verified May 2026)
_SECTIONS: dict[str, str] = {
    "2025xb/H_251454/L_251456": "Latest News",
    "2025xb/V_251452":          "Voice (Commentary)",
    "2025xb/C_251453/TE":       "Training & Exercise",
    "2025xb/C_251453/EC":       "Exchange & Cooperation",
}


class ChinaMilOnlineScraper(BaseScraper):
    """Scrapes articles from China Military Online (English)."""

    def __init__(self, target_date: Optional[date] = None) -> None:
        super().__init__("china_mil_online", target_date=target_date)

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

            # Structure: <a href="..."><div class="title"><h3>title</h3>
            #                           <small class="time">YYYY-MM-DD HH:MM</small>
            #            </div></a>
            # Walk div.title elements; the date is in a sibling <small class="time">;
            # the href is on the parent <a>.
            for title_div in soup.find_all("div", class_="title"):
                time_el = title_div.find("small", class_=lambda c: c and "time" in c)
                if time_el is None:
                    continue
                date_text = time_el.get_text(strip=True)
                if not date_text.startswith(today_str):
                    continue

                parent_a = title_div.parent
                if parent_a is None or parent_a.name != "a":
                    continue
                href = parent_a.get("href", "")
                full_url = urljoin(listing_url, href)
                if _is_article_url(full_url) and full_url not in seen:
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
        pub_date = self._extract_date(soup)

        return {
            "url":            url,
            "source_slug":    self.source_slug,
            "title_original": title,
            "text_original":  text,
            "published_date": pub_date,
        }

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        # Primary: <div class="article-header"> → <h1>
        header = soup.find("div", class_="article-header")
        if header:
            h1 = header.find("h1")
            if h1:
                text = h1.get_text(strip=True)
                if text:
                    return text

        # Fallback to any <h1>
        h1 = soup.find("h1")
        if h1:
            text = h1.get_text(strip=True)
            # Exclude boilerplate site title
            if text and text != "China Military":
                return text
        return None

    def _extract_text(self, soup: BeautifulSoup) -> str:
        # Body paragraphs use the same CMS class as 81.cn and mod.gov.cn
        paras = soup.find_all("p", class_=lambda c: c and "ueditor" in c)
        if paras:
            return "\n".join(
                p.get_text(strip=True) for p in paras
                if len(p.get_text(strip=True)) > 10
            )

        # Fallback: substantive <p> tags anywhere on page
        paragraphs = [
            p.get_text(strip=True)
            for p in soup.find_all("p")
            if len(p.get_text(strip=True)) > 40
        ]
        return "\n".join(paragraphs)

    def _extract_date(self, soup: BeautifulSoup) -> str:
        # <div class="pub-info"> contains: <dt>Time</dt><dd>2026-05-08 10:34:11</dd>
        pub_info = soup.find("div", class_="pub-info")
        if pub_info:
            for dd in pub_info.find_all("dd"):
                text = dd.get_text(strip=True)
                match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
                if match:
                    return match.group(1)

        # Fallback: first ISO date in the page
        match = re.search(r"(\d{4}-\d{2}-\d{2})", str(soup))
        if match:
            return match.group(1)
        return self.target_date.isoformat()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_article_url(url: str) -> bool:
    """
    True if the URL looks like a chinamil article page.
    Pattern: http://eng.chinamil.com.cn/2025xb/{path}/{numeric_id}.html
    """
    return bool(
        re.match(r"https?://eng\.chinamil\.com\.cn/\S+/\d+\.html$", url)
    )
