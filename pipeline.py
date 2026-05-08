"""
PLA Watch — daily pipeline runner.

Execution stages:
  1.  Initialize DB (create tables and seed data if absent)
  2.  Open a scrape_run record
  3.  Scrape source(s) for the target date
  4.  Normalize metadata and compute content hashes
  5.  Deduplicate against the DB
  6.  Keyword relevance pre-filter (free, fast)
  7.  Store all keyword-passing articles to DB
  8.  Store keyword-rejected articles with passed_relevance=0 (audit trail)
  9.  LLM relevance scoring on stored candidates (Analyzer.score_relevance)
  10. Update DB with relevance scores; skip fully analyzed articles
  11. Full analysis on passing articles: translate → (summary ∥ categorize)
  12. Update DB with analysis results
  13. Complete the scrape_run record with summary stats

The pipeline is resumable: re-running with the same --date will skip
articles already in the DB (dedup) and articles with analyzed_at already
set (pending-analysis query), so a crashed mid-analysis run picks up where
it left off.

Usage:
    python pipeline.py                        # All sources, today
    python pipeline.py --source pla_daily     # Single source
    python pipeline.py --date 2026-05-06      # Specific date
    python pipeline.py --dry-run              # No DB writes, no API calls
"""

import argparse
import logging
import sys
from datetime import date, datetime
from typing import Optional

from config import ANTHROPIC_API_KEY, CACHE_DIR, DB_PATH, OUTPUT_DIR
from processing.deduplicator import deduplicate
from processing.metadata import normalize_article
from processing.relevance import (
    apply_relevance_threshold,
    keyword_filter,
    llm_relevance_check,
)
from scraper.sources.china_mil_online import ChinaMilOnlineScraper
from scraper.sources.global_times_mil import GlobalTimesMilScraper
from scraper.sources.mod_china import MODChinaScraper
from scraper.sources.pla_daily import PLADailyScraper
from scraper.sources.xinhua_mil import XinhuaMilScraper
from storage import db

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pipeline")

# ── Source registry ───────────────────────────────────────────────────────────

SCRAPERS = {
    "pla_daily":        PLADailyScraper,
    "mod_china":        MODChinaScraper,
    "china_mil_online": ChinaMilOnlineScraper,
    "global_times_mil": GlobalTimesMilScraper,
    "xinhua_mil":       XinhuaMilScraper,
}


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run(
    sources:     list[str],
    target_date: date,
    dry_run:     bool = False,
) -> None:
    start_time = datetime.now()
    logger.info("=== PLA Watch pipeline — %s ===", target_date.isoformat())
    logger.info("Sources: %s | dry-run: %s", sources, dry_run)
    if dry_run:
        logger.info("DRY RUN — no DB writes, no API calls")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    db.init_db()
    run_id = None if dry_run else db.start_scrape_run()

    all_scraped: list[dict] = []
    errors:      list[str]  = []

    # ── Stage 3: Scrape ───────────────────────────────────────────────────────
    for slug in sources:
        if slug not in SCRAPERS:
            logger.warning("Unknown source slug '%s' — skipping", slug)
            continue
        scraper = SCRAPERS[slug](target_date=target_date)
        try:
            articles = scraper.scrape()
            all_scraped.extend(articles)
        except Exception as exc:
            msg = f"{slug}: scrape failed — {exc}"
            logger.error(msg)
            errors.append(msg)

    logger.info("Scraped (raw): %d", len(all_scraped))

    # ── Stages 4–6: Normalize, dedup, keyword filter ──────────────────────────
    normalized  = [normalize_article(a) for a in all_scraped]
    new_articles = deduplicate(normalized)
    kw_passed, kw_rejected = keyword_filter(new_articles)

    if dry_run:
        _print_summary(all_scraped, new_articles, kw_passed, [], [], dry_run)
        return

    # ── Stage 7–8: Store articles ─────────────────────────────────────────────
    inserted: list[tuple[int, dict]] = []   # (article_id, article)

    for article in kw_passed:
        aid = db.insert_article(article, run_id)
        if aid is not None:
            inserted.append((aid, article))

    for article in kw_rejected:
        aid = db.insert_article(article, run_id)
        if aid is not None:
            db.update_relevance(aid, 0.0, "failed keyword pre-filter", False)

    logger.info("Stored %d new articles (%d keyword-rejected, stored with passed=0)",
                len(inserted), len(kw_rejected))

    # ── Stages 9–12: LLM analysis ────────────────────────────────────────────
    articles_analyzed = 0

    # Build analysis queue: newly stored this run + any that passed relevance in
    # a prior run but whose translation/summary/categorization failed or was
    # interrupted before completion.
    # Format: (article_id, title_zh, body_zh, url)
    queue: list[tuple[int, str, str, str]] = [
        (aid,
         a.get("title_original", ""),
         a.get("text_original",  ""),
         a.get("url", "?"))
        for aid, a in inserted
    ]

    pending_rows = db.get_articles_pending_analysis()
    pending: list[tuple[int, str, str, str]] = [
        (r["id"],
         r["title_original"] or "",
         r["text_original"]  or "",
         r["url"]            or "?")
        for r in pending_rows
    ]

    logger.info(
        "Analysis stage: %d newly stored, %d pending from prior run, %d total to analyze",
        len(queue), len(pending), len(queue) + len(pending),
    )
    queue.extend(pending)

    if not ANTHROPIC_API_KEY:
        logger.warning(
            "ANTHROPIC_API_KEY is not set — skipping LLM analysis.\n"
            "Set the key in .env and re-run to complete analysis."
        )
    elif not queue:
        logger.info("No articles to analyze — all up to date.")
    else:
        from analysis.analyzer import Analyzer
        analyzer = Analyzer()

        for i, (aid, title_zh, body_zh, url) in enumerate(queue, 1):
            logger.info(
                "[%d/%d] Analyzing: %s",
                i, len(queue), title_zh[:70],
            )

            result = analyzer.analyze(title_zh, body_zh)

            if result is None:
                msg = f"Analysis failed entirely for article {aid} ({url})"
                logger.error(msg)
                errors.append(msg)
                continue

            # Always write relevance result
            db.update_relevance(
                aid,
                score     = result["relevance_score"],
                reasoning = result["relevance_reasoning"],
                passed    = result["passed_relevance"],
            )

            # Write full analysis only for articles that passed relevance
            if result["passed_relevance"] and result.get("title_english"):
                db.update_analysis(
                    article_id             = aid,
                    title_english          = result.get("title_english",          ""),
                    text_english           = result.get("text_english",           ""),
                    summary_english        = result.get("summary_english",        ""),
                    is_significant         = result.get("is_significant",         False),
                    significance_reasoning = result.get("significance_reasoning"),
                    categories             = result.get("categories",             []),
                    model_id               = result["model_id"],
                    prompt_version         = result["prompt_version"],
                )
                articles_analyzed += 1

                if result.get("is_significant"):
                    logger.info(
                        "  ★ SIGNIFICANT: %s",
                        result.get("significance_reasoning", ""),
                    )

    # ── Stage 13: Close run record ────────────────────────────────────────────
    if run_id is not None:
        db.complete_scrape_run(
            run_id,
            articles_scraped  = len(all_scraped),
            articles_new      = len(inserted),
            articles_analyzed = articles_analyzed,
            errors            = errors,
        )

    elapsed = (datetime.now() - start_time).total_seconds()
    db_total = db.get_total_analyzed_count() if not dry_run else 0
    _print_summary(all_scraped, new_articles, kw_passed, inserted, errors, dry_run,
                   articles_analyzed=articles_analyzed, db_total=db_total, elapsed=elapsed)

    # ── Stage 14: Generate site ───────────────────────────────────────────────
    if not dry_run:
        try:
            import importlib.util
            from pathlib import Path as _Path
            _spec = importlib.util.spec_from_file_location(
                "site_generator",
                _Path(__file__).parent / "site" / "generator.py",
            )
            _gen = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_gen)
            _gen.generate_site()
            logger.info("Site generated → %s", OUTPUT_DIR)
        except Exception as exc:
            logger.error("Site generation failed: %s", exc)


# ── Terminal summary ──────────────────────────────────────────────────────────

def _print_summary(
    scraped:           list,
    after_dedup:       list,
    after_kw:          list,
    inserted:          list,
    errors:            list,
    dry_run:           bool,
    articles_analyzed: int   = 0,
    db_total:          int   = 0,
    elapsed:           float = 0.0,
) -> None:
    sep = "─" * 52
    tag = "(DRY RUN)" if dry_run else ""
    print(f"\n{sep}")
    print(f"  PLA Watch pipeline complete {tag}")
    print(sep)
    print(f"  Scraped (raw):          {len(scraped):>4}")
    print(f"  After dedup:            {len(after_dedup):>4}")
    print(f"  After keyword filter:   {len(after_kw):>4}")
    if not dry_run:
        print(f"  Stored to DB:           {len(inserted):>4}")
        print(f"  Analyzed this run:      {articles_analyzed:>4}")
        print(f"  Total analyzed in DB:   {db_total:>4}")
    if elapsed:
        print(f"  Elapsed:                {elapsed:>6.1f}s")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for e in errors:
            print(f"    • {e}")
    print(sep)
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PLA Watch — scrape, analyze, and store daily PLA media coverage."
    )
    parser.add_argument(
        "--source",
        choices=list(SCRAPERS.keys()),
        default=None,
        help="Scrape a single source (default: all sources)",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Target date to scrape (default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without writing to DB or calling LLM APIs",
    )
    args = parser.parse_args()

    target   = args.date or date.today()
    sources  = [args.source] if args.source else list(SCRAPERS.keys())

    run(sources=sources, target_date=target, dry_run=args.dry_run)
