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
from xml.sax.saxutils import escape as _xml_escape

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

CONFIGURED_SOURCES: list[dict[str, str]] = [
    {"slug": "pla_daily", "name": "PLA Daily", "fallback": "configured / expanding"},
    {"slug": "global_times_mil", "name": "Global Times Defense", "fallback": "configured / expanding"},
    {"slug": "mod_china", "name": "MND", "fallback": "configured / expanding"},
    {"slug": "china_mil_online", "name": "China Military Online", "fallback": "configured / expanding"},
    {"slug": "xinhua_mil", "name": "Xinhua Military", "fallback": "in development"},
]

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


_EXTRACTION_SIGNALS = (
    "does not match",
    "body does not match",
    "cannot be meaningfully analyzed",
    "mismatch between headline",
    "insufficient substantive content",
    "visible text does not",
)


def _is_extraction_issue(article: dict) -> bool:
    summary = (article.get("summary_english") or "").lower()
    return any(s in summary for s in _EXTRACTION_SIGNALS)


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
    d["extraction_issue"] = _is_extraction_issue(d)
    return d


def _homepage_excerpt(text: str, limit: int = 300) -> str:
    """Display-only excerpt for homepage cards; does not alter stored summaries."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut}..."


def _dominant_category_labels(articles: list[dict], max_items: int = 3) -> list[str]:
    counter: Counter = Counter()
    for a in articles:
        if a.get("extraction_issue"):
            continue
        for slug in a.get("categories", []) or []:
            counter[slug] += 1
    return [CATEGORY_LABELS.get(slug, slug) for slug, _ in counter.most_common(max_items)]


def _make_daily_readout(articles: list[dict]) -> dict:
    normal = [a for a in articles if not a.get("extraction_issue")]
    significant = [a for a in normal if a.get("is_significant")]
    routine = [a for a in normal if not a.get("is_significant")]
    dominant = _dominant_category_labels(normal)
    sig_categories = _dominant_category_labels(significant)
    routine_categories = _dominant_category_labels(routine)

    if not normal:
        overview = (
            "Today’s monitored coverage did not produce enough clean article text for a clear single analytical signal. "
            "The brief should be read article-by-article, with collection notes separated from normal analysis."
        )
        mattered = "No clean analytical signal identified."
        routine_line = "Collection quality limited normal triage."
    elif significant:
        pattern = ", ".join(sig_categories) if sig_categories else "higher-signal coverage"
        overview = (
            f"Today’s monitored coverage produced {len(significant)} analytical signal"
            f"{'' if len(significant) == 1 else 's'}, concentrated in {pattern}. "
            "The signal should be read as official institutional messaging, not evidence of classified activity or confirmed intent."
        )
        mattered = "; ".join((a.get("title_english") or a.get("title_original") or "Untitled") for a in significant[:2])
        routine_line = (
            f"Most other clean items centered on {', '.join(routine_categories)}."
            if routine_categories else "Other clean items were lower-signal or routine."
        )
    else:
        pattern = ", ".join(dominant) if dominant else "routine official military-media themes"
        overview = (
            f"Today’s official military-media coverage appears mostly routine, centered on {pattern}. "
            "No single item indicates a major new operational development in the collected material."
        )
        mattered = "No article was flagged as an analytical signal."
        routine_line = f"Dominant categories: {pattern}."

    watch_cats = sig_categories or dominant
    watch = (
        f"Watch whether {', '.join(watch_cats[:2])} themes recur across multiple sources or senior-level placements."
        if watch_cats else
        "Watch for repeated themes across multiple sources or senior-level placements."
    )
    return {
        "overview": overview,
        "what_mattered": mattered,
        "what_was_routine": routine_line,
        "what_to_watch": watch,
    }


def _make_source_statuses(articles: list[dict]) -> list[dict]:
    collected = Counter(a.get("source_slug") for a in articles if a.get("source_slug"))
    statuses = []
    for src in CONFIGURED_SOURCES:
        count = collected.get(src["slug"], 0)
        if count:
            status = "articles collected"
        else:
            status = src["fallback"]
        statuses.append({"name": src["name"], "status": status, "count": count})
    return statuses


# ── Jinja2 setup ──────────────────────────────────────────────────────────────

def _make_env() -> Environment:
    templates_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["format_date"] = _format_date
    env.filters["homepage_excerpt"] = _homepage_excerpt
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
    # Tie generated_at to the data date so re-runs with unchanged DB produce
    # identical HTML — prevents cosmetic "Daily update" commits with no new content.
    generated_at = brief_date if brief_date else datetime.utcnow().strftime("%Y-%m-%d")
    brief_articles = by_date.get(brief_date, []) if brief_date else []
    n_significant = sum(1 for a in brief_articles if a["is_significant"])

    # Count unique sources for the brief
    brief_sources = len({a["source_slug"] for a in brief_articles})
    daily_readout = _make_daily_readout(brief_articles)
    source_statuses = _make_source_statuses(brief_articles)
    recent_signals = [a for a in articles if a.get("is_significant") and not a.get("extraction_issue")][:3]

    tmpl_index = env.get_template("index.html")
    (output_dir / "index.html").write_text(
        tmpl_index.render(
            root_path="",
            page_url="https://chinamilwatch.org/",
            brief_date=brief_date,
            brief_date_fmt=_format_date(brief_date) if brief_date else "",
            articles=brief_articles,
            n_significant=n_significant,
            n_sources=brief_sources,
            daily_readout=daily_readout,
            source_statuses=source_statuses,
            recent_signals=recent_signals,
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
            "extraction_issue": bool(a.get("extraction_issue")),
        }
        for a in articles
    ]

    (data_dir / "articles.json").write_text(
        json.dumps(archive_json, ensure_ascii=False, indent=None),
        encoding="utf-8",
    )

    articles_static = sorted(archive_json, key=lambda a: a.get("date") or "", reverse=True)[:100]

    tmpl_archive = env.get_template("archive.html")
    (output_dir / "archive.html").write_text(
        tmpl_archive.render(
            root_path="",
            page_url="https://chinamilwatch.org/archive.html",
            total_articles=len(articles),
            articles_json=json.dumps(archive_json, ensure_ascii=False),
            articles_static=articles_static,
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
                page_url=f"https://chinamilwatch.org/article/{a['id']}.html",
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
        tmpl_method.render(
            root_path="",
            page_url="https://chinamilwatch.org/methodology.html",
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote methodology.html")

    # ── robots.txt ────────────────────────────────────────────────────────────
    _generate_robots_txt(output_dir)

    # ── sitemap.xml ───────────────────────────────────────────────────────────
    _generate_sitemap_xml(output_dir, articles)

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
            page_url="https://chinamilwatch.org/signals.html",
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


def _generate_robots_txt(output_dir: Path) -> None:
    """Generate robots.txt for SEO."""
    robots_content = """User-agent: *
Allow: /
Sitemap: https://chinamilwatch.org/sitemap.xml
"""
    (output_dir / "robots.txt").write_text(robots_content, encoding="utf-8")
    logger.info("Wrote robots.txt")


def _generate_sitemap_xml(output_dir: Path, articles: list[dict]) -> None:
    """Generate sitemap.xml with all article pages."""
    urls = [
        "https://chinamilwatch.org/",
        "https://chinamilwatch.org/archive.html",
        "https://chinamilwatch.org/signals.html",
        "https://chinamilwatch.org/methodology.html",
        "https://chinamilwatch.org/the-pla-watch/",
    ]

    for article in articles:
        urls.append(f"https://chinamilwatch.org/article/{article['id']}.html")

    sitemap_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url in urls:
        sitemap_lines.append("  <url>")
        sitemap_lines.append(f"    <loc>{_xml_escape(url)}</loc>")
        sitemap_lines.append("  </url>")
    sitemap_lines.append("</urlset>")

    (output_dir / "sitemap.xml").write_text("\n".join(sitemap_lines) + "\n", encoding="utf-8")
    logger.info("Wrote sitemap.xml (%d URLs)", len(urls))


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
