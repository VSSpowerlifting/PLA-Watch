"""
Deduplication filter.

Two articles are considered duplicates if they share:
  (a) an identical URL — exact match, or
  (b) an identical content hash — catches reposts at different URLs.

Both checks are run against the database so the filter is cumulative
across scrape runs, not just within a single day.
"""

import logging
from typing import Optional

from storage import db

logger = logging.getLogger(__name__)


def deduplicate(articles: list[dict]) -> list[dict]:
    """
    Filter a list of normalized article dicts to those not yet in the DB.
    Runs two passes:
      1. URL check (fast index lookup)
      2. Content-hash check (catches cross-source reposts)

    Returns only genuinely new articles.
    """
    new_articles: list[dict] = []
    url_seen: set[str]  = set()  # Within-batch dedup for the same run
    hash_seen: set[str] = set()

    for article in articles:
        url  = article["url"]
        chash = article["content_hash"]

        # Within-batch dedup
        if url in url_seen:
            logger.debug("Skipping batch duplicate URL: %s", url)
            continue
        if chash in hash_seen:
            logger.debug("Skipping batch duplicate hash: %s", url)
            continue

        # DB checks
        if db.url_exists(url):
            logger.debug("Skipping DB duplicate URL: %s", url)
            url_seen.add(url)
            continue
        if db.hash_exists(chash):
            logger.debug("Skipping DB duplicate content: %s", url)
            hash_seen.add(chash)
            continue

        url_seen.add(url)
        hash_seen.add(chash)
        new_articles.append(article)

    logger.info(
        "Dedup: %d in → %d new (%d duplicates removed)",
        len(articles),
        len(new_articles),
        len(articles) - len(new_articles),
    )
    return new_articles
