"""
Safe local re-render for The PLA Watch.

Loads existing JSON sidecars from output/the-pla-watch/posts/ and re-renders
HTML using the current Jinja templates. Does NOT call the Anthropic API,
does NOT scrape, does NOT run the daily pipeline.

Usage:
    python scripts/rerender_pla_watch.py
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jinja2 import Environment, FileSystemLoader

# Reuse author identity from the generator module without calling its main().
from scripts.generate_pla_watch import (
    AUTHOR_NAME, AUTHOR_TITLE, AUTHOR_BIO, AUTHOR_LINKS,
)


POSTS_DIR = ROOT / "output" / "the-pla-watch" / "posts"
PLA_WATCH_DIR = ROOT / "output" / "the-pla-watch"
TEMPLATES_DIR = ROOT / "site" / "templates"


def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host or url
    except Exception:
        return url


def _flatten_term(sidecar: dict) -> tuple[str, str]:
    """
    Sidecar may carry term_to_know as {term, term_translation, explanation}
    or as the flat fields term_to_know_term / term_to_know_explanation.
    Return (term_word, explanation) suitable for the template.
    """
    if "term_to_know" in sidecar and isinstance(sidecar["term_to_know"], dict):
        t = sidecar["term_to_know"]
        word = t.get("term", "")
        trans = t.get("term_translation", "")
        if word and trans:
            display = f"{word} — {trans}"
        else:
            display = word or trans
        return display, t.get("explanation", "")
    return (
        sidecar.get("term_to_know_term", ""),
        sidecar.get("term_to_know_explanation", ""),
    )


def _articles_from_sidecar(sidecar: dict) -> list[dict]:
    """
    Map source_trail entries (label/url) into the shape the post template
    expects (title/url/source/date/is_significant). Falls back to an
    already-shaped articles list if present.
    """
    if sidecar.get("articles"):
        return sidecar["articles"]
    out = []
    sources_seen = sidecar.get("sources_seen") or []
    default_source = sources_seen[0] if sources_seen else ""
    for entry in sidecar.get("source_trail", []) or []:
        out.append({
            "title": entry.get("label") or entry.get("title") or _domain_from_url(entry.get("url", "")),
            "url":   entry.get("url", ""),
            "source": entry.get("source") or default_source,
            "date":   entry.get("date") or "",
            "is_significant": bool(entry.get("is_significant", False)),
        })
    return out


def _build_post_context(sidecar: dict) -> dict:
    term_word, term_explanation = _flatten_term(sidecar)
    return {
        # Hero / metadata
        "title":         sidecar.get("title", ""),
        "dek":           sidecar.get("dek", ""),
        "signal":        sidecar.get("signal", "") or "",
        "week_ending":   sidecar.get("week_ending", ""),
        "week_start":    sidecar.get("week_start", ""),
        "n_articles":    sidecar.get("n_articles", 0),
        "n_significant": sidecar.get("n_significant", 0),
        "days_covered":  sidecar.get("days_covered", 0),
        "edition_label": sidecar.get("edition_label", ""),
        "sources_seen":  sidecar.get("sources_seen", []),

        # Body
        "opening_note":          sidecar.get("opening_note", ""),
        "what_stood_out":        sidecar.get("what_stood_out", ""),
        "why_it_matters":        sidecar.get("why_it_matters", ""),
        "what_was_routine":      sidecar.get("what_was_routine", ""),
        "term_to_know_term":     term_word,
        "term_to_know_explanation": term_explanation,
        "what_im_watching_next": sidecar.get("what_im_watching_next", ""),

        # Source trail
        "articles": _articles_from_sidecar(sidecar),
        "source_trail_truncated": sidecar.get("source_trail_truncated", False),

        # Author identity (graceful fallbacks: missing keys → chip omitted)
        "author_name":  sidecar.get("author_name",  AUTHOR_NAME),
        "author_title": sidecar.get("author_title", AUTHOR_TITLE),
        "author_bio":   sidecar.get("author_bio",   AUTHOR_BIO),
        "author_links": sidecar.get("author_links", AUTHOR_LINKS),

        "root_path": "../../",
    }


def main() -> int:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    post_tmpl = env.get_template("pla-watch-post.html")
    index_tmpl = env.get_template("pla-watch-index.html")
    archive_tmpl = env.get_template("pla-watch-archive.html")

    # Render every post sidecar.
    sidecars = []
    for json_path in sorted(POSTS_DIR.glob("*.json"), reverse=True):
        sidecar = json.loads(json_path.read_text(encoding="utf-8"))
        # Make sure the in-memory sidecar carries author metadata for the
        # landing-page byline, even if the on-disk JSON predates this layer.
        sidecar.setdefault("author_name", AUTHOR_NAME)
        sidecar.setdefault("author_title", AUTHOR_TITLE)
        sidecars.append(sidecar)

        ctx = _build_post_context(sidecar)
        html = post_tmpl.render(**ctx)
        out_path = POSTS_DIR / f"{sidecar['date']}.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"Wrote {out_path.relative_to(ROOT)}")

    # Sort newest-first for index/archive.
    sidecars.sort(key=lambda s: s.get("date", ""), reverse=True)

    latest = sidecars[0] if sidecars else None
    archive_posts = sidecars[1:] if len(sidecars) > 1 else []

    index_html = index_tmpl.render(
        latest_post=latest, archive_posts=archive_posts, root_path="../"
    )
    (PLA_WATCH_DIR / "index.html").write_text(index_html, encoding="utf-8")
    print(f"Wrote {(PLA_WATCH_DIR / 'index.html').relative_to(ROOT)}")

    archive_html = archive_tmpl.render(posts=sidecars, root_path="../")
    (PLA_WATCH_DIR / "archive.html").write_text(archive_html, encoding="utf-8")
    print(f"Wrote {(PLA_WATCH_DIR / 'archive.html').relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
