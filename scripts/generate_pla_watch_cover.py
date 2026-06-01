"""
Editorial cover-image generator for The PLA Watch.

Primary renderer: HTML/CSS → PNG via Playwright (deterministic, no AI images).
Fallback renderer: Python Pillow (PIL) if Playwright is unavailable.

Design: cool blue-gray think-tank palette with subtle grid/radar decorative
elements. Fully deterministic — same sidecar input always produces the same
cover.

Output paths
------------
Full cover:  output/the-pla-watch/covers/{week_ending}.png   (1200×630)
Thumbnail:   output/the-pla-watch/covers/{week_ending}-thumb.png  (600×315)

Usage
-----
    python scripts/generate_pla_watch_cover.py [--date YYYY-MM-DD] [--all]
                                                [--force]

Defaults to the newest sidecar in output/the-pla-watch/posts/. Skips writing
if the cover PNG already exists, unless --force is given.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import sys
import tempfile
from datetime import date as date_cls
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "output" / "the-pla-watch" / "posts"
COVERS_DIR = ROOT / "output" / "the-pla-watch" / "covers"
MEDIA_DIR = ROOT / "output" / "the-pla-watch" / "media"
CURATED_IMAGE_DIRS = [
    ROOT / "assets" / "pla-watch" / "covers",
    ROOT / "assets" / "pla-watch" / "media",
    ROOT / "output" / "the-pla-watch" / "curated-media",
]

WIDTH, HEIGHT = 1200, 630
THUMB_WIDTH, THUMB_HEIGHT = 600, 315
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# ── Date formatting ───────────────────────────────────────────────────────────

_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _fmt_long(iso: str) -> str:
    try:
        d = date_cls.fromisoformat(iso)
        return f"{_MONTHS[d.month]} {d.day}, {d.year}"
    except Exception:
        return iso


def _fmt_short(iso: str) -> str:
    try:
        d = date_cls.fromisoformat(iso)
        return f"{_MONTHS[d.month][:3]} {d.day}"
    except Exception:
        return iso


def _format_coverage(start: str, end: str) -> str:
    if not start or not end:
        return ""
    if start == end:
        return _fmt_long(start)
    try:
        ds, de = date_cls.fromisoformat(start), date_cls.fromisoformat(end)
        if ds.year == de.year and ds.month == de.month:
            return f"{_MONTHS[ds.month]} {ds.day}–{de.day}, {ds.year}"
        if ds.year == de.year:
            return (f"{_MONTHS[ds.month][:3]} {ds.day}–"
                    f"{_MONTHS[de.month][:3]} {de.day}, {ds.year}")
        return f"{_fmt_short(start)} {ds.year}–{_fmt_short(end)} {de.year}"
    except Exception:
        return f"{start}–{end}"


# ── HTML/CSS template ─────────────────────────────────────────────────────────

# Sentinel strings — unique enough to never appear in sidecar content.
_PLACEHOLDER_TITLE = "___COVER_ISSUE_TITLE___"
_PLACEHOLDER_COVERAGE = "___COVER_COVERAGE___"
_PLACEHOLDER_WEEK_ENDING = "___COVER_WEEK_ENDING___"
_PLACEHOLDER_N_ARTICLES = "___COVER_N_ARTICLES___"
_PLACEHOLDER_N_SIGNIFICANT = "___COVER_N_SIGNIFICANT___"
_PLACEHOLDER_EDITION_STAT = "___COVER_EDITION_STAT___"
_PLACEHOLDER_BACKGROUND = "___COVER_BACKGROUND___"

_HTML_TEMPLATE = f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  width: 1200px;
  height: 630px;
  overflow: hidden;
  background: #16253d;
  font-family: Georgia, 'Times New Roman', serif;
}}
.canvas {{
  position: relative;
  width: 1200px;
  height: 630px;
  overflow: hidden;
  background: #16253d;
}}
.photo-bg {{
  position: absolute;
  inset: 0;
  background-image: {_PLACEHOLDER_BACKGROUND};
  background-size: cover;
  background-position: center;
  transform: scale(1.01);
  filter: brightness(0.82) saturate(0.90);
}}
.photo-treatment {{
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg,
      rgba(5, 12, 24, 0.94) 0%,
      rgba(7, 17, 32, 0.84) 38%,
      rgba(7, 17, 32, 0.38) 72%,
      rgba(7, 17, 32, 0.14) 100%),
    linear-gradient(180deg,
      rgba(5, 12, 24, 0.22) 0%,
      rgba(5, 12, 24, 0.08) 48%,
      rgba(5, 12, 24, 0.72) 100%),
    radial-gradient(ellipse 720px 500px at 28% 46%,
      rgba(5, 12, 24, 0.52) 0%,
      transparent 72%);
}}
.title-shield {{
  position: absolute;
  left: 42px;
  top: 165px;
  width: 790px;
  height: 245px;
  background:
    linear-gradient(90deg,
      rgba(5, 12, 24, 0.50) 0%,
      rgba(5, 12, 24, 0.34) 54%,
      transparent 100%),
    radial-gradient(ellipse 620px 230px at 22% 55%,
      rgba(5, 12, 24, 0.42),
      transparent 74%);
  pointer-events: none;
}}
/* Layers rendered back-to-front */
.grid-svg {{
  position: absolute;
  inset: 0;
  pointer-events: none;
}}
/* Mist/horizon gradient — fades in from the lower third */
.mist {{
  position: absolute;
  left: 0; right: 0;
  bottom: 0;
  height: 220px;
  background: linear-gradient(to top, rgba(6, 14, 27, 0.62), transparent);
  pointer-events: none;
}}
/* Radar rings — lower right */
.radar {{
  position: absolute;
  right: -90px;
  bottom: -90px;
  width: 480px;
  height: 480px;
  border-radius: 50%;
  border: 1px solid rgba(100,165,235,0.04);
  pointer-events: none;
}}
.radar::before {{
  content: '';
  position: absolute;
  inset: 76px;
  border-radius: 50%;
  border: 1px solid rgba(100,165,235,0.03);
}}
.radar::after {{
  content: '';
  position: absolute;
  inset: 156px;
  border-radius: 50%;
  border: 1px solid rgba(100,165,235,0.025);
}}
/* Outer frame */
.frame-outer {{
  position: absolute;
  inset: 12px;
  border: 1px solid rgba(255,255,255,0.08);
  pointer-events: none;
}}
/* Inner double-border — gives the think-tank report feel */
.frame-inner {{
  position: absolute;
  inset: 20px;
  border: 1px solid rgba(255,255,255,0.045);
  pointer-events: none;
}}
/* Accent bar — just inside outer frame */
.accent-bar {{
  position: absolute;
  top: 12px;
  left: 12px;
  right: 12px;
  height: 2px;
  background: linear-gradient(90deg,
    rgba(120,175,230,0.68) 0%,
    rgba(120,175,230,0.22) 48%,
    transparent 100%);
}}
/* Crosshair corners */
.ch-tl, .ch-tr {{
  position: absolute;
  top: 32px;
  width: 18px;
  height: 18px;
  pointer-events: none;
}}
.ch-tl {{ left: 32px; }}
.ch-tr {{ right: 32px; }}
.ch-tl::before, .ch-tr::before {{
  content: '';
  position: absolute;
  top: 50%;
  left: 0; right: 0;
  height: 1px;
  background: rgba(150,185,215,0.22);
  transform: translateY(-50%);
}}
.ch-tl::after, .ch-tr::after {{
  content: '';
  position: absolute;
  left: 50%;
  top: 0; bottom: 0;
  width: 1px;
  background: rgba(150,185,215,0.22);
  transform: translateX(-50%);
}}
/* Main content column */
.content {{
  position: absolute;
  top: 48px;
  left: 64px;
  right: 64px;
  bottom: 44px;
  display: flex;
  flex-direction: column;
}}
.eyebrow {{
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.34em;
  text-transform: uppercase;
  color: #a9c4d8;
  margin-bottom: 11px;
  display: flex;
  align-items: center;
  gap: 10px;
}}
.eyebrow-line {{
  width: 28px;
  height: 1px;
  background: rgba(169,196,216,0.52);
  display: inline-block;
  flex-shrink: 0;
}}
.pub-title {{
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 68px;
  font-weight: 700;
  line-height: 0.98;
  letter-spacing: -0.018em;
  color: #e8f1f7;
  margin-bottom: 12px;
  /* Subtle text shadow adds depth against the gradient */
  text-shadow: 0 2px 24px rgba(0,0,0,0.45);
}}
.subtitle {{
  font-family: Georgia, 'Times New Roman', serif;
  font-style: italic;
  font-size: 14.5px;
  color: #b7c9d8;
  margin-bottom: 20px;
  letter-spacing: 0.01em;
  line-height: 1.42;
  max-width: 640px;
}}
.divider {{
  width: 100%;
  height: 1px;
  background: linear-gradient(90deg,
    rgba(255,255,255,0.14) 0%,
    rgba(255,255,255,0.05) 65%,
    transparent 100%);
  margin-bottom: 20px;
}}
.issue-title {{
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 33px;
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -0.014em;
  color: #f0f5f8;
  flex: 1;
  max-width: 720px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  text-shadow: 0 2px 18px rgba(0,0,0,0.58);
}}
.coverage {{
  font-family: Georgia, 'Times New Roman', serif;
  font-style: italic;
  font-size: 13px;
  color: rgba(183,201,216,0.70);
  margin-top: 9px;
  letter-spacing: 0.01em;
}}
.bottom-strip {{
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  border-top: 1px solid rgba(255,255,255,0.12);
  padding: 12px 18px 12px;
  margin-top: auto;
  margin-left: -18px;
  margin-right: -18px;
  background: rgba(5, 12, 24, 0.58);
  box-shadow: 0 -18px 44px rgba(5, 12, 24, 0.32);
}}
.stats {{
  display: flex;
  gap: 34px;
  align-items: flex-start;
}}
.stat {{
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 112px;
}}
.stat-label {{
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.045em;
  color: #a9c4d8;
  text-shadow: 0 1px 8px rgba(0,0,0,0.55);
}}
.stat-value {{
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 22px;
  font-weight: 700;
  color: #e8f1f7;
  line-height: 1;
  text-shadow: 0 1px 10px rgba(0,0,0,0.62);
}}
.stat-value.date-val {{
  font-size: 16px;
  font-weight: 700;
  font-style: normal;
  letter-spacing: 0.02em;
}}
.stat-value.text-val {{
  font-size: 15px;
  font-style: normal;
  color: #e8f1f7;
}}
.footer {{
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 10px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: rgba(183,201,216,0.72);
  text-align: right;
  line-height: 1.4;
}}
</style>
</head>
<body>
<div class="canvas">
  <div class="photo-bg"></div>
  <div class="photo-treatment"></div>
  <div class="title-shield"></div>
  <svg class="grid-svg" xmlns="http://www.w3.org/2000/svg" width="1200" height="630">
    <defs>
      <pattern id="grid" width="96" height="96" patternUnits="userSpaceOnUse">
        <path d="M 96 0 L 0 0 0 96" fill="none"
              stroke="rgba(125,185,240,0.28)" stroke-width="0.28"/>
      </pattern>
      <linearGradient id="gridFade" x1="1" y1="0" x2="0.45" y2="0.65">
        <stop offset="0%" stop-color="white" stop-opacity="0.62"/>
        <stop offset="100%" stop-color="white" stop-opacity="0"/>
      </linearGradient>
      <mask id="gridMask">
        <rect x="760" y="34" width="390" height="235" fill="url(#gridFade)"/>
      </mask>
    </defs>
    <rect x="760" y="34" width="390" height="235" fill="url(#grid)" opacity="0.035" mask="url(#gridMask)"/>

    <!-- Diagonal bearing lines: subtle, angled, top-right quadrant -->
    <line x1="800" y1="0" x2="1200" y2="360"
          stroke="rgba(150,185,215,0.026)" stroke-width="1"/>
    <line x1="900" y1="0" x2="1200" y2="240"
          stroke="rgba(150,185,215,0.018)" stroke-width="1"/>

    <!-- Coastline contour paths — irregular smooth curves, lower-right -->
    <path d="M 620 580 C 680 555, 760 570, 840 548 S 960 510, 1060 530 S 1160 555, 1200 540"
          fill="none" stroke="rgba(150,185,215,0.035)" stroke-width="1.2"/>
    <path d="M 700 610 C 780 590, 860 608, 940 588 S 1080 552, 1160 572 S 1200 590, 1200 590"
          fill="none" stroke="rgba(150,185,215,0.025)" stroke-width="1"/>
    <path d="M 560 560 C 640 538, 730 552, 820 530 S 960 490, 1040 508 S 1130 528, 1200 510"
          fill="none" stroke="rgba(150,185,215,0.02)" stroke-width="1"/>
  </svg>

  <div class="mist"></div>
  <div class="radar"></div>
  <div class="ch-tl"></div>
  <div class="ch-tr"></div>
  <div class="frame-outer"></div>
  <div class="frame-inner"></div>
  <div class="accent-bar"></div>

  <div class="content">
    <div class="eyebrow">
      <span class="eyebrow-line"></span>
      China Mil Watch
    </div>
    <div class="pub-title">THE PLA WATCH</div>
    <div class="subtitle">Weekly Briefing on Chinese Military and Security Developments</div>
    <div class="divider"></div>
    <div class="issue-title">{_PLACEHOLDER_TITLE}</div>
    <div class="coverage">{_PLACEHOLDER_COVERAGE}</div>
    <div class="bottom-strip">
      <div class="stats">
        <div class="stat">
          <div class="stat-label">Week ending</div>
          <div class="stat-value date-val">{_PLACEHOLDER_WEEK_ENDING}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Articles</div>
          <div class="stat-value">{_PLACEHOLDER_N_ARTICLES}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Significant</div>
          <div class="stat-value">{_PLACEHOLDER_N_SIGNIFICANT}</div>
        </div>
        {_PLACEHOLDER_EDITION_STAT}
      </div>
      <div class="footer">China Mil Watch</div>
    </div>
  </div>
</div>
</body>
</html>"""


def _local_image_path(raw_path: str) -> Optional[Path]:
    if not raw_path:
        return None
    parsed = urlparse(raw_path)
    if parsed.scheme and parsed.scheme != "file":
        return None
    candidate = Path(parsed.path if parsed.scheme == "file" else raw_path)
    if not candidate.is_absolute():
        if raw_path.startswith("../media/"):
            candidate = MEDIA_DIR / raw_path.removeprefix("../media/")
        else:
            candidate = ROOT / raw_path
    if candidate.suffix.lower() in IMAGE_EXTS and candidate.exists():
        return candidate.resolve()
    return None


def _iter_sidecar_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _iter_sidecar_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_sidecar_strings(nested)


def resolve_background_image(
    sidecar: dict,
    exclude_path: Optional[Path] = None,
) -> Optional[Path]:
    """Resolve an edition-specific local image from the sidecar.

    Only checks images that belong to this edition:
      1. media_items entries (src/local_path/path/optimized_path).
      2. Top-level image keys (visual_image, context_image, …).
      3. Any ../media/ path found anywhere in the sidecar strings.

    Curated fallbacks and prior-week media are intentionally excluded here;
    generate_one() handles those in priority order after auto-fetch.

    An image that resolves to *exclude_path* is silently skipped so that the
    previous issue's cover background is never accidentally reused.
    """
    # If generate_one() pre-resolved the path (with dedup exclusion), use it directly.
    pre_resolved = sidecar.get("_resolved_bg_path")
    if pre_resolved:
        p = Path(pre_resolved)
        if p.exists():
            print(f"[cover] using pre-resolved background: {p.name}")
            return p

    issue_date = sidecar.get("date") or sidecar.get("week_ending") or "?"

    def _accept(img: Optional[Path], label: str) -> Optional[Path]:
        if img is None:
            return None
        if exclude_path and img.resolve() == exclude_path.resolve():
            print(f"[cover:{issue_date}] skip {label}: same as previous issue image ({img.name})")
            return None
        print(f"[cover:{issue_date}] selected background via {label}: {img.name}")
        return img

    media_items = sidecar.get("media_items") or []
    if isinstance(media_items, list):
        for item in media_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") and item.get("type") != "image":
                continue
            for key in ("src", "local_path", "path", "optimized_path"):
                img = _accept(_local_image_path(str(item.get(key) or "")),
                              f"media_items[{key}]")
                if img:
                    return img

    for key in ("visual_image", "context_image", "media_image", "image", "local_path"):
        img = _accept(_local_image_path(str(sidecar.get(key) or "")), f"sidecar[{key}]")
        if img:
            return img

    for raw_path in _iter_sidecar_strings(sidecar):
        if "output/the-pla-watch/media/" not in raw_path and "../media/" not in raw_path:
            continue
        img = _accept(_local_image_path(raw_path), f"sidecar string ({raw_path})")
        if img:
            return img

    return None


def _build_html(sidecar: dict) -> str:
    """Fill the HTML template with escaped sidecar data."""
    # cover_title overrides the derived title when set (allows a shorter cover
    # headline independent of the full publication title).
    title = (sidecar.get("cover_title") or "").strip()
    if not title:
        title = sidecar.get("title", "").strip()
        for prefix in ("The PLA Watch:", "The PLA Watch —", "PLA Watch:"):
            if title.lower().startswith(prefix.lower()):
                title = title[len(prefix):].strip()
                break

    week_start = sidecar.get("week_start", "")
    week_ending = sidecar.get("week_ending", "") or sidecar.get("date", "")
    coverage = _format_coverage(week_start, week_ending)
    coverage_html = (f"Coverage: {html_lib.escape(coverage)}"
                     if coverage else "")

    edition_label = (sidecar.get("edition_label") or "").strip()
    edition_stat_html = ""
    if edition_label:
        edition_stat_html = (
            '<div class="stat">'
            '<div class="stat-label">Edition</div>'
            f'<div class="stat-value text-val">{html_lib.escape(edition_label)}</div>'
            "</div>"
        )

    bg_path = resolve_background_image(sidecar)
    bg_css = (
        f"url('{html_lib.escape(bg_path.as_uri(), quote=True)}')"
        if bg_path else
        "radial-gradient(ellipse 900px 560px at 38% 45%, rgba(34, 58, 95, 0.85) 0%, transparent 100%), linear-gradient(160deg, #111e30 0%, #1a2e4a 40%, #16253d 70%, #111e30 100%)"
    )

    return (
        _HTML_TEMPLATE
        .replace(_PLACEHOLDER_BACKGROUND, bg_css)
        .replace(_PLACEHOLDER_TITLE, html_lib.escape(title))
        .replace(_PLACEHOLDER_COVERAGE, coverage_html)
        .replace(_PLACEHOLDER_WEEK_ENDING,
                 html_lib.escape(_fmt_long(week_ending) if week_ending else "—"))
        .replace(_PLACEHOLDER_N_ARTICLES,
                 html_lib.escape(str(sidecar.get("n_articles", 0))))
        .replace(_PLACEHOLDER_N_SIGNIFICANT,
                 html_lib.escape(str(sidecar.get("n_significant", 0))))
        .replace(_PLACEHOLDER_EDITION_STAT, edition_stat_html)
    )


# ── Playwright renderer ───────────────────────────────────────────────────────

def _render_with_playwright(html_content: str, out_path: Path) -> bool:
    """Render HTML to PNG via Playwright. Returns True on success."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        print("WARN: playwright not importable; will use PIL fallback")
        return False
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".html", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(html_content)
            tmp_path = f.name

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
            page.goto(f"file://{tmp_path}", wait_until="domcontentloaded")
            page.screenshot(
                path=str(out_path),
                clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT},
                type="png",
            )
            browser.close()

        Path(tmp_path).unlink(missing_ok=True)
        return True
    except Exception as exc:
        print(f"WARN: Playwright rendering failed ({exc!r}); using PIL fallback")
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        return False


# ── PIL fallback renderer ─────────────────────────────────────────────────────

# Blue-gray palette
_BG       = (24, 40, 64)
_BG2      = (31, 51, 82)
_ACCENT   = (90, 155, 225)
_FRAME    = (255, 255, 255, 22)
_WHITE    = (255, 255, 255)
_TXT2     = (185, 215, 245)
_MUTED    = (130, 180, 230)

_SERIF_BOLD = [
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/Library/Fonts/Georgia Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
]
_SERIF_ITALIC = [
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
    "/Library/Fonts/Georgia Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
]
_SERIF_BOLD_ITALIC = [
    "/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf",
    "/Library/Fonts/Georgia Bold Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
]
_SERIF_REG = [
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]
_SANS_BOLD = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_SANS_REG = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _find_font(candidates, size):
    from PIL import ImageFont  # type: ignore
    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _measure(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap(draw, text, font, max_width):
    words = text.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        cand = cur + " " + w
        if _measure(draw, cand, font)[0] <= max_width:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _truncate_lines(draw, lines, font, max_width, max_lines):
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    while last:
        cand = last.rstrip(" ,;:.-") + "…"
        if _measure(draw, cand, font)[0] <= max_width:
            kept[-1] = cand
            return kept
        last = last[:-1]
    kept[-1] = "…"
    return kept


def _render_with_pil(sidecar: dict, out_path: Path) -> None:
    """PIL-based cover renderer — blue-gray palette fallback."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageOps  # type: ignore

    bg_path = resolve_background_image(sidecar)
    if bg_path:
        img = Image.open(bg_path).convert("RGB")
        img = ImageOps.fit(img, (WIDTH, HEIGHT), method=Image.LANCZOS)
        img = ImageEnhance.Brightness(img).enhance(0.82)
        img = ImageEnhance.Color(img).enhance(0.90)
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), _BG)
        gradient = Image.new("RGB", (WIDTH, HEIGHT), _BG)
        for x in range(WIDTH):
            blend = int(x / WIDTH * 18)
            c = tuple(min(255, _BG[i] + blend) for i in range(3))
            for y in range(HEIGHT):
                gradient.putpixel((x, y), c)
        img.paste(gradient)

    treatment = Image.new("RGBA", (WIDTH, HEIGHT), (8, 18, 34, 0))
    tdraw = ImageDraw.Draw(treatment)
    for x in range(WIDTH):
        alpha = int(240 - (x / WIDTH) * 204)
        tdraw.line([(x, 0), (x, HEIGHT)], fill=(5, 12, 24, max(36, alpha)))
    for y2 in range(HEIGHT):
        alpha = int(max(0, (y2 - 340) / (HEIGHT - 340)) * 150)
        if alpha:
            tdraw.line([(0, y2), (WIDTH, y2)], fill=(5, 12, 24, alpha))
    tdraw.ellipse(
        [(-220, -30), (820, 720)],
        fill=(5, 12, 24, 72),
    )
    img = Image.alpha_composite(img.convert("RGBA"), treatment).convert("RGB")

    draw = ImageDraw.Draw(img)

    # Faint technical texture, confined to the upper-right corner.
    for x in range(780, 1160, 96):
        draw.line([(x, 40), (x, 270)], fill=(150, 185, 215, 8), width=1)
    for y in range(48, 270, 96):
        draw.line([(780, y), (1160, y)], fill=(150, 185, 215, 8), width=1)

    # Radar circles (lower-right)
    for r in [260, 180, 100]:
        draw.ellipse(
            [(WIDTH - 110 - r, HEIGHT - 110 - r),
             (WIDTH - 110 + r, HEIGHT - 110 + r)],
            outline=(150, 185, 215, 7),
        )

    # Frame border
    draw.rectangle([(14, 14), (WIDTH - 14, HEIGHT - 14)],
                   outline=(255, 255, 255, 22), width=1)

    # Blue accent bar (top inside frame)
    draw.rectangle([(14, 14), (WIDTH - 14, 16)],
                   fill=(169, 196, 216, 135))

    # Crosshair TL
    cx, cy = 30, 30
    draw.line([(cx - 10, cy), (cx + 10, cy)],
              fill=(150, 185, 215, 52), width=1)
    draw.line([(cx, cy - 10), (cx, cy + 10)],
              fill=(150, 185, 215, 52), width=1)
    # Crosshair TR
    cx2, cy2 = WIDTH - 30, 30
    draw.line([(cx2 - 10, cy2), (cx2 + 10, cy2)],
              fill=(150, 185, 215, 52), width=1)
    draw.line([(cx2, cy2 - 10), (cx2, cy2 + 10)],
              fill=(150, 185, 215, 52), width=1)

    draw = ImageDraw.Draw(img)
    margin_x = 64
    inner_w = WIDTH - 2 * margin_x
    y = 50

    # Eyebrow
    eye_font = _find_font(_SANS_BOLD, 13)
    eye_text = "CHINA MIL WATCH"
    draw.text((margin_x, y), eye_text, font=eye_font,
              fill=(169, 196, 216))
    y += 32

    # Publication title
    title_font = _find_font(_SERIF_BOLD, 68)
    draw.text((margin_x, y), "THE PLA WATCH", font=title_font,
              fill=(232, 241, 247))
    y += 78

    # Subtitle
    sub_font = _find_font(_SERIF_ITALIC, 16)
    draw.text((margin_x, y),
              "Weekly Briefing on Chinese Military and Security Developments",
              font=sub_font, fill=(183, 201, 216))
    y += 36

    # Hairline
    draw.line([(margin_x, y), (WIDTH - margin_x, y)],
              fill=(255, 255, 255, 25), width=1)
    y += 24

    # Issue title
    title_shield = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    shield_draw = ImageDraw.Draw(title_shield)
    shield_top = y - 16
    shield_bottom = y + 150
    for x in range(margin_x - 22, margin_x + 790):
        rel = (x - (margin_x - 22)) / 812
        alpha = int(120 * max(0, 1 - rel))
        shield_draw.line(
            [(x, shield_top), (x, shield_bottom)],
            fill=(5, 12, 24, alpha),
        )
    shield_draw.ellipse(
        [(margin_x - 110, y - 70), (margin_x + 710, y + 210)],
        fill=(5, 12, 24, 54),
    )
    img = Image.alpha_composite(img.convert("RGBA"), title_shield).convert("RGB")
    draw = ImageDraw.Draw(img)

    title = (sidecar.get("cover_title") or "").strip()
    if not title:
        title = sidecar.get("title", "").strip()
        for pfx in ("The PLA Watch:", "The PLA Watch —", "PLA Watch:"):
            if title.lower().startswith(pfx.lower()):
                title = title[len(pfx):].strip()
                break
    hed_font = _find_font(_SERIF_BOLD, 36)
    hed_lines = _wrap(draw, title, hed_font, inner_w - 40)
    hed_lines = _truncate_lines(draw, hed_lines, hed_font, inner_w - 40, 3)
    for line in hed_lines:
        draw.text((margin_x, y), line, font=hed_font, fill=(240, 245, 248))
        y += 46
    y += 6

    # Coverage
    week_start = sidecar.get("week_start", "")
    week_ending = sidecar.get("week_ending", "") or sidecar.get("date", "")
    cov = _format_coverage(week_start, week_ending)
    if cov:
        cov_font = _find_font(_SERIF_ITALIC, 15)
        draw.text((margin_x, y), f"Coverage: {cov}", font=cov_font,
                  fill=(183, 201, 216))
        y += 26

    # Bottom strip
    strip_y = HEIGHT - 96
    draw.rectangle([(margin_x - 18, strip_y - 1), (WIDTH - margin_x + 18, HEIGHT - 34)],
                   fill=(5, 12, 24, 150))
    draw.line([(margin_x, strip_y), (WIDTH - margin_x, strip_y)],
              fill=(255, 255, 255, 34), width=1)

    lbl_font = _find_font(_SANS_REG, 12)
    num_font = _find_font(_SERIF_BOLD, 23)
    sm_font = _find_font(_SERIF_REG, 16)
    date_font = _find_font(_SERIF_BOLD, 16)

    cells = [
        ("Week ending", _fmt_long(week_ending) if week_ending else "—", "date"),
        ("Articles", str(sidecar.get("n_articles", 0)), "num"),
        ("Significant", str(sidecar.get("n_significant", 0)), "num"),
    ]
    edition_label = (sidecar.get("edition_label") or "").strip()
    if edition_label:
        cells.append(("Edition", edition_label, "text"))

    col_w = inner_w / len(cells)
    label_y = strip_y + 11
    value_y = strip_y + 31
    for i, (lbl, val, kind) in enumerate(cells):
        cx = margin_x + int(i * col_w)
        draw.text((cx, label_y), lbl, font=lbl_font,
                  fill=(169, 196, 216))
        if kind == "num":
            draw.text((cx, value_y), val, font=num_font, fill=(232, 241, 247))
        elif kind == "date":
            draw.text((cx, value_y + 4), val, font=date_font,
                      fill=(232, 241, 247))
        else:
            draw.text((cx, value_y + 5), val, font=sm_font,
                      fill=(232, 241, 247))

    # Footer
    foot_font = _find_font(_SANS_REG, 12)
    draw.text((margin_x, HEIGHT - 30), "China Mil Watch",
              font=foot_font, fill=(183, 201, 216))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)


# ── Public API ────────────────────────────────────────────────────────────────

def render_cover(sidecar: dict, out_path: Path) -> Path:
    """
    Render a 1200×630 cover PNG for one sidecar. Tries Playwright first;
    falls back to PIL if Playwright is unavailable or fails.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_content = _build_html(sidecar)
    if not _render_with_playwright(html_content, out_path):
        _render_with_pil(sidecar, out_path)
    return out_path


def render_thumbnail(cover_path: Path, thumb_path: Path) -> Path:
    """Resize the full cover to 600×315 for use as a card thumbnail."""
    from PIL import Image  # type: ignore
    img = Image.open(cover_path)
    thumb = img.resize((THUMB_WIDTH, THUMB_HEIGHT), Image.LANCZOS)
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    thumb.save(thumb_path, format="PNG", optimize=True)
    return thumb_path


# ── Path helpers ──────────────────────────────────────────────────────────────

def _cover_path_for(sidecar_date: str) -> Path:
    return COVERS_DIR / f"{sidecar_date}.png"


def _thumb_path_for(sidecar_date: str) -> Path:
    return COVERS_DIR / f"{sidecar_date}-thumb.png"


def _load_sidecar(json_path: Path) -> dict:
    return json.loads(json_path.read_text(encoding="utf-8"))


def _prev_issue_image(sidecar_date: str) -> Optional[Path]:
    """Return the resolved background image used by the chronologically prior issue, if any."""
    try:
        all_json = sorted(POSTS_DIR.glob("*.json"))
        dates = [p.stem for p in all_json]
        if sidecar_date not in dates:
            return None
        idx = dates.index(sidecar_date)
        if idx == 0:
            return None
        prev_sidecar = _load_sidecar(all_json[idx - 1])
        # Pass exclude_path=None: we only want to resolve, not exclude here.
        return resolve_background_image(prev_sidecar, exclude_path=None)
    except Exception:
        return None


def _auto_fetch_source_image(sidecar: dict, sidecar_date: str) -> Optional[Path]:
    """
    Attempt to fetch a cover background from the sidecar's source_trail URLs.
    Returns the cached local Path on success, None on failure or no URLs.

    Imports fetch_article_image lazily so this module remains usable without
    network access. Failures are caught and logged — never fatal.
    """
    urls = [
        e["url"] for e in (sidecar.get("source_trail") or [])
        if isinstance(e, dict) and e.get("url")
    ][:4]
    if not urls:
        return None
    try:
        from scripts.fetch_article_image import fetch_best_image  # type: ignore
        return fetch_best_image(urls=urls, post_date=sidecar_date, dry_run=False,
                                update_sidecar=True)
    except Exception as exc:
        print(f"[cover:{sidecar_date}] auto-fetch failed ({exc!r}); using fallback")
        return None


def _first_image_in_dirs(
    dirs: list,
    exclude_path: Optional[Path],
    issue_date: str,
    label: str,
) -> Optional[Path]:
    """Return the first valid image found across *dirs*, skipping *exclude_path*."""
    for directory in dirs:
        directory = Path(directory)
        if not directory.exists():
            continue
        for img in sorted(directory.iterdir()):
            if img.suffix.lower() not in IMAGE_EXTS:
                continue
            resolved = img.resolve()
            if exclude_path and resolved == exclude_path.resolve():
                print(f"[cover:{issue_date}] skip {label} ({img.name}): same as previous issue image")
                continue
            print(f"[cover:{issue_date}] selected background via {label}: {img.name}")
            return resolved
    return None


def generate_one(json_path: Path, force: bool = False,
                 fetch_source_image: bool = True) -> Optional[Path]:
    sidecar = _load_sidecar(json_path)
    sidecar_date = sidecar.get("date") or json_path.stem
    out_path = _cover_path_for(sidecar_date)
    thumb_path = _thumb_path_for(sidecar_date)

    # Determine the previous issue's background so we can refuse to reuse it.
    prev_img = _prev_issue_image(sidecar_date)

    if out_path.exists() and not force:
        print(f"[skip] {out_path.relative_to(ROOT)} already exists "
              f"(use --force to overwrite)")
    else:
        # Priority 1: edition-specific images (media_items, image keys, path strings).
        bg_path = resolve_background_image(sidecar, exclude_path=prev_img)
        bg_source = "edition_media" if bg_path else None

        # Priority 2: fetch og:image from this edition's source_trail article URLs.
        if bg_path is None and fetch_source_image:
            fetched = _auto_fetch_source_image(sidecar, sidecar_date)
            if fetched:
                sidecar = _load_sidecar(json_path)
                bg_path = resolve_background_image(sidecar, exclude_path=prev_img)
                bg_source = "source_trail_fetch" if bg_path else None

        # Priority 3: curated reusable cover images (static assets, never edition-specific).
        if bg_path is None:
            bg_path = _first_image_in_dirs(
                CURATED_IMAGE_DIRS, prev_img, sidecar_date, "curated_fallback"
            )
            bg_source = "curated_fallback" if bg_path else None

        # Priority 4: last-resort — prior-week images already in the media dir.
        # Sorted newest-first so the most recently fetched image is tried first.
        if bg_path is None and MEDIA_DIR.exists():
            bg_path = _first_image_in_dirs(
                [MEDIA_DIR], prev_img, sidecar_date, "media_dir_fallback"
            )
            bg_source = "media_dir_fallback" if bg_path else None

        if bg_path is None:
            bg_source = "abstract_gradient"
            print(f"[cover:{sidecar_date}] no local image found — using abstract fallback gradient")

        # Record the image source in the sidecar so callers can audit it.
        sidecar_on_disk = _load_sidecar(json_path)
        sidecar_on_disk["background_image_source"] = bg_source
        if bg_path:
            sidecar_on_disk["background_image_path"] = str(bg_path.relative_to(ROOT)
                                                            if bg_path.is_relative_to(ROOT)
                                                            else bg_path)
        json_path.write_text(
            json.dumps(sidecar_on_disk, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        sidecar = sidecar_on_disk

        sidecar_with_bg = dict(sidecar)
        if bg_path:
            sidecar_with_bg["_resolved_bg_path"] = str(bg_path)
        render_cover(sidecar_with_bg, out_path)
        if out_path.exists():
            print(f"[wrote] {out_path.relative_to(ROOT)}")
        else:
            print(f"WARN: cover render returned but file not found: {out_path}")
            return None

    # Always generate thumbnail if missing, even when the cover was skipped.
    if out_path.exists() and (not thumb_path.exists() or force):
        try:
            render_thumbnail(out_path, thumb_path)
            print(f"[wrote] {thumb_path.relative_to(ROOT)}")
        except Exception as exc:
            print(f"WARN: thumbnail generation failed ({exc!r})")

    return out_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Generate PLA Watch issue cover images.")
    p.add_argument("--date", help="Sidecar date YYYY-MM-DD. Defaults to newest.")
    p.add_argument("--all", action="store_true",
                   help="Generate covers for every sidecar JSON in posts/.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite an existing cover PNG.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not POSTS_DIR.exists():
        print(f"ERROR: posts dir not found: {POSTS_DIR}")
        return 1

    if args.all:
        paths = sorted(POSTS_DIR.glob("*.json"))
    elif args.date:
        paths = [POSTS_DIR / f"{args.date}.json"]
    else:
        candidates = sorted(POSTS_DIR.glob("*.json"), reverse=True)
        if not candidates:
            print("ERROR: no sidecar JSON files in posts/")
            return 1
        paths = [candidates[0]]

    for p in paths:
        if not p.exists():
            print(f"ERROR: missing sidecar: {p}")
            return 1
        generate_one(p, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
