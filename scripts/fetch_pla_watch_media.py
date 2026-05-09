"""
Guarded automatic media selection for The PLA Watch.

For a given PLA Watch issue (identified by --post-date), this script:
  1. Reads the JSON sidecar at output/the-pla-watch/posts/YYYY-MM-DD.json
  2. Builds a small set of restrained, non-sensational image search queries
     from the post's title, dek, signal, source trail, and tags.
  3. Searches Wikimedia Commons for candidate images.
  4. Verifies each candidate's license metadata (PD / CC0 / CC BY / CC BY-SA
     only). Anything else — non-commercial, no-derivatives, editorial-only,
     all rights reserved, unknown — is rejected.
  5. Scores the surviving candidates for relevance, tone, resolution, and
     orientation. Picks at most one.
  6. In --apply mode: downloads the selected image into
       output/the-pla-watch/media/YYYY-MM-DD-auto-image.<ext>
     writes a metadata sidecar at
       output/the-pla-watch/media/YYYY-MM-DD-auto-image.json
     and adds a media_items entry to the post JSON.
     In --dry-run mode (default): prints the candidate audit only.
  7. After --apply, runs scripts/rerender_pla_watch.py to regenerate HTML.

This script never calls the Anthropic API, never scrapes PLA Daily / 81.cn,
and never runs pipeline.py. It only touches Wikimedia Commons and local
files inside output/the-pla-watch/.

Usage:
    python scripts/fetch_pla_watch_media.py --post-date 2026-05-10 --dry-run
    python scripts/fetch_pla_watch_media.py --post-date 2026-05-10 --apply
"""

import argparse
import datetime as dt
import html
import json
import mimetypes
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import requests


ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "output" / "the-pla-watch" / "posts"
MEDIA_DIR = ROOT / "output" / "the-pla-watch" / "media"

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = (
    "ChinaMilWatch-PLAWatch-MediaFetcher/0.1 "
    "(https://chinamilwatch.org; contact via site)"
)
HTTP_TIMEOUT = 20

# Licenses we accept (case-insensitive substring match against Commons'
# LicenseShortName / License field). Conservative — only public domain,
# CC0, CC BY, and CC BY-SA. NC, ND, editorial-only, all-rights-reserved
# are explicitly rejected below.
ALLOWED_LICENSE_KEYS = (
    "public domain",
    "cc0",
    "cc-zero",
    "cc by 1.0", "cc by 2.0", "cc by 2.5", "cc by 3.0", "cc by 4.0",
    "cc-by-1.0", "cc-by-2.0", "cc-by-2.5", "cc-by-3.0", "cc-by-4.0",
    "cc by-sa 1.0", "cc by-sa 2.0", "cc by-sa 2.5", "cc by-sa 3.0", "cc by-sa 4.0",
    "cc-by-sa-1.0", "cc-by-sa-2.0", "cc-by-sa-2.5", "cc-by-sa-3.0", "cc-by-sa-4.0",
)
REJECTED_LICENSE_KEYS = (
    "nc",                       # non-commercial
    "nd",                       # no derivatives
    "non-commercial",
    "noderivative",
    "no derivative",
    "all rights reserved",
    "editorial",
    "fair use",
    "unknown",
)

# Words we never want in a query, even if they appear in a post.
SENSATIONAL_TERMS = {
    "war", "wars", "warfare",
    "missile", "missiles",
    "invasion", "invade",
    "explosion", "explosions",
    "blood", "casualty", "casualties",
    "attack", "attacks",
    "strike", "strikes",
}

# Heuristic penalty terms in candidate titles / descriptions.
LOW_VALUE_TITLE_TERMS = (
    "logo", "icon", "emblem", "seal of",
    "coat of arms", "crest",
    "diagram", "schematic", "infographic",
    "map of", "locator map",
    "meme",
    "cartoon",
)
SENSATIONAL_TITLE_TERMS = (
    "explosion", "blast", "wreck", "wreckage", "fireball",
    "casualty", "casualties", "destruction", "burning",
)

# Stopwords trimmed from query construction.
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at",
    "is", "are", "was", "were", "be", "with", "from", "as", "by",
    "that", "this", "these", "those", "it", "its", "into", "over",
    "after", "before", "but", "than", "their", "they", "them", "we",
    "our", "us", "you", "your", "i", "my", "me", "he", "she", "his",
    "her", "him", "not", "no", "do", "does", "did", "have", "has", "had",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "very", "more", "most", "less", "least",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(text: str) -> list[str]:
    if not text:
        return []
    cleaned = re.sub(r"[^\w\s\-']", " ", text.lower())
    return [t for t in cleaned.split() if t and t not in STOPWORDS and len(t) > 2]


def _filter_sensational(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in SENSATIONAL_TERMS]


def _ext_for_mime(mime: str, fallback_url: str = "") -> str:
    if mime:
        guess = mimetypes.guess_extension(mime)
        if guess:
            return guess
    if fallback_url:
        suffix = Path(fallback_url).suffix.lower()
        if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"):
            return suffix
    return ".jpg"


def _is_allowed_license(short_name: str, license_field: str) -> bool:
    blob = f"{short_name} {license_field}".lower()
    if not blob.strip():
        return False
    for bad in REJECTED_LICENSE_KEYS:
        # Avoid false positives: we want to reject "NC" as a token, not as
        # part of "Public domain". Match only on word boundaries for short
        # codes like nc/nd.
        if bad in ("nc", "nd"):
            if re.search(rf"\b{bad}\b", blob):
                return False
        elif bad in blob:
            return False
    for ok in ALLOWED_LICENSE_KEYS:
        if ok in blob:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Query construction
# ─────────────────────────────────────────────────────────────────────────────


def build_queries(sidecar: dict) -> list[str]:
    """
    Build a small set of restrained image search queries from the sidecar.

    We deliberately favor institutional, contextual phrasing over event-
    specific phrasing to avoid landing on sensational or rights-restricted
    imagery.
    """
    title = sidecar.get("title", "") or ""
    dek = sidecar.get("dek", "") or ""
    signal = sidecar.get("signal", "") or ""
    tags: list[str] = list(sidecar.get("tags", []) or [])
    sources_seen: list[str] = list(sidecar.get("sources_seen", []) or [])
    source_trail = sidecar.get("source_trail", []) or []

    title_tokens = _filter_sensational(_tokens(title))
    dek_tokens = _filter_sensational(_tokens(dek))
    signal_tokens = _filter_sensational(_tokens(signal))

    title_phrase = " ".join(title_tokens[:5])
    dek_phrase = " ".join(dek_tokens[:6])
    signal_phrase = " ".join(signal_tokens[:5])

    base_qs: list[str] = []

    institutional_anchors = [
        "People's Liberation Army",
        "Chinese military",
        "PLA",
    ]

    # Topic-aware additions, kept generic and non-sensational.
    blob = f"{title} {dek} {signal}".lower()
    topic_phrases = []
    if any(k in blob for k in ("corruption", "rectification", "discipline",
                               "verdict", "minister", "anti-corruption")):
        topic_phrases.extend([
            "Chinese military political work",
            "Central Military Commission Beijing",
            "PLA National Defense University",
        ])
    if any(k in blob for k in ("training", "exercise", "drill", "brigade",
                               "group army")):
        topic_phrases.extend([
            "Chinese military training",
            "PLA ground forces training",
        ])
    if any(k in blob for k in ("coast guard", "south china sea", "diaoyu",
                               "maritime")):
        topic_phrases.append("China Coast Guard")
    if any(k in blob for k in ("rocket force", "strategic")):
        # Only "PLA Rocket Force" — general term, not weapon glamour.
        topic_phrases.append("PLA Rocket Force")
    if any(k in blob for k in ("xiangshan", "forum", "diplomacy",
                               "international")):
        topic_phrases.append("Xiangshan Forum")
    if any(k in blob for k in ("japan", "japanese")):
        topic_phrases.append("Japan Self-Defense Forces")
    if any(k in blob for k in ("ndu", "national defense university")):
        topic_phrases.append("PLA National Defense University Beijing")

    # Build candidate queries. Order matters — earlier queries are tried first.
    # Topic-aware first (most likely to land on institutional context).
    for tp in topic_phrases:
        base_qs.append(tp)
    # Title phrase grounded in an institutional anchor.
    if title_phrase:
        for anchor in institutional_anchors[:2]:
            base_qs.append(f"{anchor} {title_phrase}")
    # Tag-based.
    for tag in tags[:3]:
        clean_tag = " ".join(_filter_sensational(_tokens(tag)))
        if clean_tag:
            base_qs.append(f"People's Liberation Army {clean_tag}")
    # Generic institutional fallback.
    base_qs.extend([
        "People's Liberation Army",
        "Central Military Commission China",
    ])

    # De-dup, preserve order, cap.
    seen: set[str] = set()
    out: list[str] = []
    for q in base_qs:
        norm = q.strip()
        if not norm:
            continue
        # Strip any sensational tokens that may have slipped in.
        norm = " ".join(t for t in norm.split() if t.lower() not in SENSATIONAL_TERMS)
        norm_l = norm.lower()
        if norm_l in seen:
            continue
        seen.add(norm_l)
        out.append(norm)
        if len(out) >= 8:
            break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Wikimedia Commons access
# ─────────────────────────────────────────────────────────────────────────────


def _commons_get(params: dict) -> dict:
    headers = {"User-Agent": USER_AGENT}
    params = dict(params)
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    resp = requests.get(COMMONS_API, params=params, headers=headers,
                        timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def commons_search(query: str, limit: int = 12) -> list[str]:
    """Search Commons in the File namespace; return a list of File: titles."""
    data = _commons_get({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": "6",            # File namespace
        "srlimit": str(limit),
    })
    hits = data.get("query", {}).get("search", []) or []
    titles = [h.get("title", "") for h in hits if h.get("title")]
    return [t for t in titles if t.startswith("File:")]


def commons_imageinfo(titles: list[str]) -> dict[str, dict]:
    """Fetch imageinfo (incl. extmetadata) for a batch of File: titles."""
    if not titles:
        return {}
    out: dict[str, dict] = {}
    # Batch in groups of 25 to stay polite.
    for i in range(0, len(titles), 25):
        batch = titles[i:i + 25]
        data = _commons_get({
            "action": "query",
            "titles": "|".join(batch),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiextmetadatafilter": (
                "License|LicenseShortName|LicenseUrl|UsageTerms|"
                "Artist|Credit|ObjectName|ImageDescription|"
                "DateTimeOriginal"
            ),
        })
        pages = data.get("query", {}).get("pages", []) or []
        for page in pages:
            title = page.get("title", "")
            ii = (page.get("imageinfo") or [{}])[0]
            if title and ii:
                out[title] = ii
    return out


def _ext_meta(ii: dict, key: str) -> str:
    em = ii.get("extmetadata") or {}
    val = (em.get(key) or {}).get("value", "")
    return _strip_html(str(val)) if val else ""


def parse_candidate(title: str, ii: dict) -> Optional[dict]:
    """Return a normalized candidate dict, or None if it should be dropped."""
    url = ii.get("url", "")
    mime = ii.get("mime", "") or ""
    width = int(ii.get("width") or 0)
    height = int(ii.get("height") or 0)

    # We don't want SVGs, GIFs, or unknown bitmaps as editorial photographs.
    allowed_mime = ("image/jpeg", "image/png", "image/webp")
    if mime not in allowed_mime:
        return None
    if not url:
        return None

    short_name = _ext_meta(ii, "LicenseShortName")
    license_field = _ext_meta(ii, "License")
    license_url = _ext_meta(ii, "LicenseUrl")
    artist = _ext_meta(ii, "Artist")
    credit = _ext_meta(ii, "Credit")
    object_name = _ext_meta(ii, "ObjectName")
    description = _ext_meta(ii, "ImageDescription")

    license_label = short_name or license_field or "Unknown"

    return {
        "title": title,
        "url": url,
        "mime": mime,
        "width": width,
        "height": height,
        "license_short": short_name,
        "license_field": license_field,
        "license_label": license_label,
        "license_url": license_url,
        "artist": artist,
        "credit": credit,
        "object_name": object_name,
        "description": description,
        "description_url": (
            ii.get("descriptionurl")
            or f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scoring & filtering
# ─────────────────────────────────────────────────────────────────────────────


def filter_and_score(candidates: list[dict], sidecar: dict
                     ) -> tuple[list[dict], list[dict]]:
    """Return (kept, rejected) lists. Each rejected entry has a `reason`."""
    blob = " ".join([
        sidecar.get("title", "") or "",
        sidecar.get("dek", "") or "",
        sidecar.get("signal", "") or "",
    ]).lower()

    kept: list[dict] = []
    rejected: list[dict] = []

    for c in candidates:
        # 1. License gate.
        if not _is_allowed_license(c["license_short"], c["license_field"]):
            rejected.append({**c, "reason":
                             f"license not in allowlist ({c['license_label']!r})"})
            continue

        # 2. Resolution floor.
        if c["width"] < 800 or c["height"] < 500:
            rejected.append({**c, "reason":
                             f"resolution too low ({c['width']}x{c['height']})"})
            continue

        title_l = (c["title"] + " " + (c["description"] or "")).lower()

        # 3. Sensational imagery — reject outright.
        if any(t in title_l for t in SENSATIONAL_TITLE_TERMS):
            rejected.append({**c, "reason": "sensational subject in title/description"})
            continue

        # 4. Low-value imagery (logos, maps, diagrams) — reject unless
        #    the article specifically discusses that kind of artifact.
        article_explicitly_about_map = any(
            k in blob for k in ("map of", "geography", "border")
        )
        article_explicitly_about_emblem = any(
            k in blob for k in ("logo", "emblem", "insignia", "seal of")
        )
        is_map = any(k in title_l for k in ("map of", "locator map", "diagram",
                                            "schematic", "infographic"))
        is_emblem = any(k in title_l for k in ("logo", "icon", "emblem",
                                               "coat of arms", "crest",
                                               "seal of"))
        if is_map and not article_explicitly_about_map:
            rejected.append({**c, "reason": "map/diagram, not directly relevant"})
            continue
        if is_emblem and not article_explicitly_about_emblem:
            rejected.append({**c, "reason": "logo/emblem, not directly relevant"})
            continue

        # 5. Weapon glamour shots — reject unless article is specifically
        #    about that weapon system.
        weapon_terms = ("missile", "icbm", "warhead", "tank prototype",
                        "fighter jet")
        is_weapon_glamour = any(w in title_l for w in weapon_terms)
        article_about_weapon = any(w in blob for w in weapon_terms)
        if is_weapon_glamour and not article_about_weapon:
            rejected.append({**c, "reason": "weapon glamour shot, not topic-specific"})
            continue

        # ── Score ────────────────────────────────────────────────────────
        score = 0.0

        # License preference.
        ll = c["license_label"].lower()
        if "public domain" in ll or "cc0" in ll:
            score += 4
        elif "cc by-sa" in ll or "cc-by-sa" in ll:
            score += 2
        elif "cc by" in ll or "cc-by" in ll:
            score += 3

        # Resolution.
        if c["width"] >= 1600:
            score += 3
        elif c["width"] >= 1200:
            score += 2
        elif c["width"] >= 800:
            score += 1

        # Landscape preference.
        if c["height"] and c["width"] >= c["height"]:
            score += 2
        elif c["height"] and c["width"] / c["height"] >= 0.85:
            score += 1

        # Topical keyword overlap with article.
        article_tokens = set(_filter_sensational(_tokens(blob)))
        title_tokens = set(_filter_sensational(_tokens(c["title"])))
        desc_tokens = set(_filter_sensational(_tokens(c["description"] or "")))
        overlap = len(article_tokens & (title_tokens | desc_tokens))
        score += min(overlap, 5)

        # Bonus for institutional context.
        institutional_terms = ("national defense university", "academy",
                               "ministry of national defense", "parade",
                               "honor guard", "ceremonial",
                               "people's liberation army")
        if any(t in title_l for t in institutional_terms):
            score += 1.5

        # Mild penalty for low-value-leaning titles even if not rejected.
        if any(t in title_l for t in LOW_VALUE_TITLE_TERMS):
            score -= 1

        c_scored = {**c, "score": round(score, 2)}
        kept.append(c_scored)

    kept.sort(key=lambda x: x["score"], reverse=True)
    return kept, rejected


# ─────────────────────────────────────────────────────────────────────────────
# Selection rationale + caption + credit
# ─────────────────────────────────────────────────────────────────────────────


SELECTION_THRESHOLD = 6.0  # combined score floor for picking an outside image


def selection_note(candidate: dict) -> str:
    bits = []
    if candidate.get("object_name"):
        bits.append(f"institutional/contextual subject ({candidate['object_name']})")
    bits.append(f"license verified: {candidate['license_label']}")
    bits.append(f"{candidate['width']}×{candidate['height']} px")
    if candidate["height"] and candidate["width"] >= candidate["height"]:
        bits.append("landscape orientation")
    return "Selected because: " + "; ".join(bits) + "."


def default_caption(sidecar: dict) -> str:
    return (
        "Visual context for this week's issue. The image is included to "
        "illustrate the institutional setting around Chinese military "
        "politics, not as evidence of the specific events discussed."
    )


def build_credit(candidate: dict) -> str:
    artist = candidate.get("artist") or candidate.get("credit") or "Unknown"
    license_label = candidate["license_label"]
    return f"{artist} · {license_label} · via Wikimedia Commons"


# ─────────────────────────────────────────────────────────────────────────────
# Main per-post flow
# ─────────────────────────────────────────────────────────────────────────────


def process_post(post_date: str, apply: bool, no_rerender: bool) -> int:
    json_path = POSTS_DIR / f"{post_date}.json"
    if not json_path.exists():
        print(f"ERROR: no sidecar at {json_path.relative_to(ROOT)}")
        return 2
    sidecar = json.loads(json_path.read_text(encoding="utf-8"))

    print(f"\n=== PLA Watch media audit · {post_date} ===")
    print(f"Title: {sidecar.get('title', '')}")
    print(f"Sources seen: {sidecar.get('sources_seen', [])}")

    queries = build_queries(sidecar)
    print(f"\nQueries (in order): {queries}")

    # Search Commons for each query, dedupe titles, then fetch imageinfo once.
    all_titles: list[str] = []
    seen_titles: set[str] = set()
    per_query_hits: list[tuple[str, list[str]]] = []
    for q in queries:
        try:
            titles = commons_search(q, limit=10)
        except Exception as exc:
            print(f"  · [{q}] search failed: {exc!r}")
            per_query_hits.append((q, []))
            continue
        per_query_hits.append((q, titles))
        for t in titles:
            if t not in seen_titles:
                seen_titles.add(t)
                all_titles.append(t)

    print("\nSearch results per query:")
    for q, titles in per_query_hits:
        print(f"  [{q}] → {len(titles)} hits")

    if not all_titles:
        print("\nNo Commons results across all queries. No outside image will be used.")
        return 0

    # Cap total candidates before metadata fetch.
    all_titles = all_titles[:30]

    try:
        info_map = commons_imageinfo(all_titles)
    except Exception as exc:
        print(f"\nERROR: imageinfo fetch failed: {exc!r}")
        return 1

    candidates: list[dict] = []
    for t in all_titles:
        ii = info_map.get(t)
        if not ii:
            continue
        c = parse_candidate(t, ii)
        if c is not None:
            candidates.append(c)

    print(f"\n{len(candidates)} usable file records returned (after MIME filter).")

    kept, rejected = filter_and_score(candidates, sidecar)

    print(f"\nRejected ({len(rejected)}):")
    for r in rejected[:25]:
        print(f"  - {r['title']}  · {r.get('license_label','?')}  · {r['reason']}")
    if len(rejected) > 25:
        print(f"  … and {len(rejected) - 25} more")

    print(f"\nKept candidates ({len(kept)}), highest-scoring first:")
    for c in kept[:10]:
        print(f"  · score={c['score']}  {c['width']}x{c['height']}  "
              f"{c['license_label']}  ::  {c['title']}")

    if not kept:
        print("\nNo candidate cleared license + sanity filters. "
              "No outside image will be used.")
        return 0

    top = kept[0]
    if top["score"] < SELECTION_THRESHOLD:
        print(f"\nTop candidate score {top['score']} below threshold "
              f"{SELECTION_THRESHOLD}. No outside image will be used.")
        return 0

    print("\n--- Selected candidate ---")
    print(f"  Title:       {top['title']}")
    print(f"  Source page: {top['description_url']}")
    print(f"  Image URL:   {top['url']}")
    print(f"  License:     {top['license_label']}  ({top.get('license_url') or '—'})")
    print(f"  Artist:      {top.get('artist') or '—'}")
    print(f"  Dimensions:  {top['width']}x{top['height']}")
    print(f"  Score:       {top['score']}")
    note = selection_note(top)
    print(f"  Reason:      {note}")

    if not apply:
        print("\n[dry-run] Not downloading, not modifying any files.")
        return 0

    # ── Apply mode ───────────────────────────────────────────────────────
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext = _ext_for_mime(top["mime"], top["url"])
    image_path = MEDIA_DIR / f"{post_date}-auto-image{ext}"
    meta_path = MEDIA_DIR / f"{post_date}-auto-image.json"

    print(f"\nDownloading → {image_path.relative_to(ROOT)}")
    headers = {"User-Agent": USER_AGENT}
    with requests.get(top["url"], headers=headers, stream=True,
                      timeout=HTTP_TIMEOUT) as r:
        r.raise_for_status()
        with image_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
    size = image_path.stat().st_size
    if size <= 0:
        print("ERROR: downloaded file is empty; aborting without JSON update.")
        try:
            image_path.unlink()
        except FileNotFoundError:
            pass
        return 1
    print(f"  · saved {size} bytes")

    selected_query = next(
        (q for q, titles in per_query_hits if top["title"] in titles),
        queries[0] if queries else "",
    )

    metadata = {
        "original_title": top["title"],
        "creator": top.get("artist") or "",
        "credit": build_credit(top),
        "license": top["license_label"],
        "license_url": top.get("license_url") or "",
        "source_url": top["description_url"],
        "image_url": top["url"],
        "downloaded_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "search_query_used": selected_query,
        "reason_selected": note,
        "local_path": str(image_path.relative_to(ROOT)),
        "width": top["width"],
        "height": top["height"],
        "mime": top["mime"],
    }
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    print(f"  · wrote {meta_path.relative_to(ROOT)}")

    # Update sidecar JSON: replace any existing auto-image entry, keep others.
    media_items = list(sidecar.get("media_items") or [])
    media_items = [
        m for m in media_items
        if not (isinstance(m, dict) and m.get("auto_selected") is True)
    ]
    alt_text = (top.get("object_name")
                or top.get("description")
                or "Visual context image (Wikimedia Commons)")
    if len(alt_text) > 240:
        alt_text = alt_text[:237] + "…"
    media_items.append({
        "type": "image",
        "src": f"../media/{image_path.name}",
        "alt": alt_text,
        "caption": default_caption(sidecar),
        "credit": build_credit(top),
        "license": top["license_label"],
        "license_url": top.get("license_url") or "",
        "source_url": top["description_url"],
        "selection_note": note,
        "auto_selected": True,
    })
    sidecar["media_items"] = media_items
    json_path.write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  · updated {json_path.relative_to(ROOT)} with media_items entry")

    if not no_rerender:
        print("\nRe-rendering PLA Watch HTML (no API calls, no scraping)…")
        rerender = ROOT / "scripts" / "rerender_pla_watch.py"
        result = subprocess.run(
            [sys.executable, str(rerender), "--no-covers"],
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            print(f"WARN: rerender exited with code {result.returncode}")
            return result.returncode

    print("\nDone.")
    print(f"  · image:    {image_path.relative_to(ROOT)}")
    print(f"  · metadata: {meta_path.relative_to(ROOT)}")
    print(f"  · sidecar:  {json_path.relative_to(ROOT)}")
    return 0


def _parse_args():
    p = argparse.ArgumentParser(
        description="Guarded automatic media selection for The PLA Watch."
    )
    p.add_argument("--post-date", required=True,
                   help="Issue date in YYYY-MM-DD form (matches sidecar filename).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true",
                   help="(default) Print candidate audit; do not download or "
                        "modify files.")
    g.add_argument("--apply", action="store_true",
                   help="Download the selected image, write metadata, update "
                        "the post JSON, and re-render HTML.")
    p.add_argument("--no-rerender", action="store_true",
                   help="With --apply, skip the rerender_pla_watch.py step.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        dt.date.fromisoformat(args.post_date)
    except ValueError:
        print(f"ERROR: --post-date must be YYYY-MM-DD (got {args.post_date!r})")
        return 2
    apply = args.apply and not args.dry_run
    return process_post(args.post_date, apply=apply, no_rerender=args.no_rerender)


if __name__ == "__main__":
    sys.exit(main())
