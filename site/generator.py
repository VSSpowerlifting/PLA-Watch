"""
PLA Watch — static site generator.

Reads all analyzed articles from the SQLite database and renders three page
types to the output/ directory:

  output/index.html          Daily brief for the most recent date with data
  output/archive.html        Searchable archive (client-side JS filtering)
  output/article/{id}.html   Individual article with full translation

Run standalone:
    python site/generator.py

Called from pipeline.py after each analysis run.
"""

import json
import logging
import shutil
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# Allow running as a standalone script from the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import OUTPUT_DIR
from storage.db import get_all_analyzed_articles

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CATEGORY_LABELS: dict[str, str] = {
    "taiwan":             "Taiwan",
    "south_china_sea":    "South China Sea",
    "east_china_sea":     "East China Sea",
    "us_china_military":  "U.S.–China Military",
    "exercises":          "Exercises",
    "modernization":      "Modernization",
    "doctrine":           "Doctrine",
    "personnel":          "Personnel",
    "nuclear":            "Nuclear",
    "cyber_info":         "Cyber & Info",
    "internal_security":  "Internal Security",
    "coast_guard":        "Coast Guard",
    "military_diplomacy": "Military Diplomacy",
    "political_work":     "Political Work",
}

# Lower number = higher priority; used to sort articles by category
_CATEGORY_PRIORITY: dict[str, int] = {
    "taiwan":             1,
    "south_china_sea":    2,
    "east_china_sea":     3,
    "us_china_military":  4,
    "nuclear":            5,
    "exercises":          6,
    "modernization":      7,
    "coast_guard":        8,
    "military_diplomacy": 9,
    "doctrine":           10,
    "cyber_info":         11,
    "political_work":     12,
    "personnel":          13,
    "internal_security":  14,
}


def _format_date(date_str: str) -> str:
    """'2026-05-07' → '7 May 2026'"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%-d %B %Y")
    except (ValueError, AttributeError):
        return date_str or ""


def _article_sort_key(article: dict) -> tuple:
    """Sort: significant first, then by category priority, then by relevance desc."""
    sig = 0 if article["is_significant"] else 1
    cats = article["categories"]
    cat_priority = min((_CATEGORY_PRIORITY.get(c, 99) for c in cats), default=99)
    relevance = -(article["relevance_score"] or 0.0)
    return (sig, cat_priority, relevance)


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict and parse categories."""
    d = dict(row)
    slugs = d.pop("category_slugs", "") or ""
    d["categories"] = [s for s in slugs.split(",") if s]
    d["category_labels"] = [CATEGORY_LABELS.get(s, s) for s in d["categories"]]
    d["published_date_fmt"] = _format_date(d.get("published_date", ""))
    return d


# ── Jinja2 setup ──────────────────────────────────────────────────────────────

def _make_env() -> Environment:
    templates_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    env.filters["format_date"] = _format_date
    return env


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_site(output_dir: Path = OUTPUT_DIR) -> None:
    """Render the full site to output_dir."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    article_dir = output_dir / "article"
    article_dir.mkdir(exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)

    # Copy static assets
    _site = Path(__file__).parent
    for asset in ("favicon.svg", "logo-icon.png", "logo-wordmark.png"):
        src = _site / asset
        if src.exists():
            shutil.copy2(src, output_dir / asset)

    env = _make_env()
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ── Load all articles ─────────────────────────────────────────────────────
    rows = get_all_analyzed_articles()
    articles = [_row_to_dict(r) for r in rows]

    if not articles:
        logger.warning("No analyzed articles in DB — site will be empty.")

    # Group by date
    by_date: dict[str, list[dict]] = {}
    for a in articles:
        d = a.get("published_date") or "unknown"
        by_date.setdefault(d, []).append(a)

    dates_sorted = sorted(by_date.keys(), reverse=True)

    # Sort each day's articles
    for d in dates_sorted:
        by_date[d].sort(key=_article_sort_key)

    # ── index.html ────────────────────────────────────────────────────────────
    brief_date = dates_sorted[0] if dates_sorted else None
    brief_articles = by_date.get(brief_date, []) if brief_date else []
    n_significant = sum(1 for a in brief_articles if a["is_significant"])

    # Count unique sources for the brief
    brief_sources = len({a["source_slug"] for a in brief_articles})

    tmpl_index = env.get_template("index.html")
    (output_dir / "index.html").write_text(
        tmpl_index.render(
            root_path="",
            brief_date=brief_date,
            brief_date_fmt=_format_date(brief_date) if brief_date else "",
            articles=brief_articles,
            n_significant=n_significant,
            n_sources=brief_sources,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote index.html (%d articles)", len(brief_articles))

    # ── archive.html + data/articles.json ────────────────────────────────────
    archive_json = [
        {
            "id":                a["id"],
            "title_en":         a.get("title_english") or "",
            "title_zh":         a.get("title_original") or "",
            "summary":          a.get("summary_english") or "",
            "date":             a.get("published_date") or "",
            "date_fmt":         a["published_date_fmt"],
            "source":           a.get("source_name") or "",
            "source_slug":      a.get("source_slug") or "",
            "categories":       a["categories"],
            "category_labels":  a["category_labels"],
            "is_significant":   bool(a.get("is_significant")),
            "significance_reason": a.get("significance_reasoning") or "",
            "relevance_score":  round(a.get("relevance_score") or 0.0, 2),
            "url":              a.get("url") or "",
            "article_path":     f"article/{a['id']}.html",
        }
        for a in articles
    ]

    (data_dir / "articles.json").write_text(
        json.dumps(archive_json, ensure_ascii=False, indent=None),
        encoding="utf-8",
    )

    tmpl_archive = env.get_template("archive.html")
    (output_dir / "archive.html").write_text(
        tmpl_archive.render(
            root_path="",
            total_articles=len(articles),
            articles_json=json.dumps(archive_json, ensure_ascii=False),
            category_labels=CATEGORY_LABELS,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote archive.html (%d articles in JSON)", len(articles))

    # ── article/{id}.html ─────────────────────────────────────────────────────
    tmpl_article = env.get_template("article.html")
    count = 0
    for a in articles:
        (article_dir / f"{a['id']}.html").write_text(
            tmpl_article.render(
                root_path="../",
                article=a,
                generated_at=generated_at,
            ),
            encoding="utf-8",
        )
        count += 1

    logger.info("Wrote %d article pages", count)

    # Prune orphan per-article pages whose article was removed from the DB
    # (e.g. by scripts/cleanup_duplicates.py). Without this, deleted articles
    # remain reachable via their direct URL even though nothing links to them.
    expected_ids = {a["id"] for a in articles}
    removed = 0
    for path in article_dir.glob("*.html"):
        try:
            file_id = int(path.stem)
        except ValueError:
            continue
        if file_id not in expected_ids:
            path.unlink()
            removed += 1
    if removed:
        logger.info("Pruned %d stale article page(s)", removed)
    else:
        logger.info("Pruned 0 stale article pages")

    # ── signals.html ──────────────────────────────────────────────────────────
    _write_signals_page(env, output_dir, articles, generated_at)

    # ── methodology.html ──────────────────────────────────────────────────────
    tmpl_method = env.get_template("methodology.html")
    (output_dir / "methodology.html").write_text(
        tmpl_method.render(root_path="", generated_at=generated_at),
        encoding="utf-8",
    )
    logger.info("Wrote methodology.html")

    logger.info("Site generated → %s", output_dir)

    _generate_og_image(output_dir)


def _compute_window_stats(articles: list[dict], days: int, today=None) -> dict:
    """
    Read-only aggregate over `articles` for a rolling window of `days` ending on `today`
    (inclusive). Returns total count and significant count. Tolerates missing/malformed
    published_date values by skipping them.
    """
    today = today or _latest_article_date(articles) or datetime.utcnow().date()
    cutoff = today - timedelta(days=days - 1)
    total = 0
    significant = 0
    for a in articles:
        d_str = a.get("published_date")
        if not d_str:
            continue
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if cutoff <= d <= today:
            total += 1
            if a.get("is_significant"):
                significant += 1
    return {"total": total, "significant": significant}


def _latest_article_date(articles: list[dict]):
    """Return the most recent valid published_date in the corpus, or None."""
    latest = None
    for a in articles:
        d_str = a.get("published_date")
        if not d_str:
            continue
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if latest is None or d > latest:
            latest = d
    return latest


def _articles_in_window(articles: list[dict], days: int, today=None) -> list[dict]:
    today = today or _latest_article_date(articles) or datetime.utcnow().date()
    cutoff = today - timedelta(days=days - 1)
    out = []
    for a in articles:
        d_str = a.get("published_date")
        if not d_str:
            continue
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if cutoff <= d <= today:
            out.append(a)
    return out


def _write_signals_page(env, output_dir: Path, articles: list[dict], generated_at: str) -> None:
    """Render output/signals.html. Read-only — never writes to the DB."""
    in_30d = _articles_in_window(articles, 30)

    cat_counter: Counter = Counter()
    for a in in_30d:
        for slug in a.get("categories", []) or []:
            if slug:
                cat_counter[slug] += 1
    top_categories = [
        {"slug": slug, "label": CATEGORY_LABELS.get(slug, slug), "count": count}
        for slug, count in cat_counter.most_common(10)
    ]

    src_counter: Counter = Counter()
    for a in in_30d:
        name = a.get("source_name") or "Unknown"
        src_counter[name] += 1
    source_mix = [{"name": n, "count": c} for n, c in src_counter.most_common(10)]

    significant_recent = [a for a in articles if a.get("is_significant")]
    latest_significant = significant_recent[:6]

    tmpl = env.get_template("signals.html")
    (output_dir / "signals.html").write_text(
        tmpl.render(
            root_path="",
            stats_7d=_compute_window_stats(articles, 7),
            stats_30d=_compute_window_stats(articles, 30),
            top_categories=top_categories,
            source_mix=source_mix,
            latest_significant=latest_significant,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote signals.html (7d=%s, 30d=%s, %d significant in latest list)",
                _compute_window_stats(articles, 7),
                _compute_window_stats(articles, 30),
                len(latest_significant))


def _generate_og_image(output_dir: Path) -> None:
    """Render index.html to a 1200×630 PNG for Open Graph / Twitter Card."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed — skipping og-image generation")
        return

    index_path = (output_dir / "index.html").resolve()
    out_path = output_dir / "og-image.png"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.goto(f"file://{index_path}", wait_until="networkidle")
            page.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": 1200, "height": 630})
            browser.close()
        logger.info("Wrote og-image.png")
    except Exception as exc:
        logger.warning("og-image generation failed: %s", exc)


# ── Standalone entry ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    generate_site()
    print(f"\nSite written to: {OUTPUT_DIR}/")
    print(f"Open with:  open {OUTPUT_DIR}/index.html\n")
