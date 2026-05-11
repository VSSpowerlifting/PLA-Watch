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

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "output" / "the-pla-watch" / "posts"
COVERS_DIR = ROOT / "output" / "the-pla-watch" / "covers"

WIDTH, HEIGHT = 1200, 630
THUMB_WIDTH, THUMB_HEIGHT = 600, 315

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
  background: #182840;
  font-family: Georgia, 'Times New Roman', serif;
}}
.canvas {{
  position: relative;
  width: 1200px;
  height: 630px;
  overflow: hidden;
  background: linear-gradient(150deg, #182840 0%, #1f3352 55%, #182a3f 100%);
}}
.grid-svg {{
  position: absolute;
  inset: 0;
  pointer-events: none;
}}
.radar {{
  position: absolute;
  right: -110px;
  bottom: -110px;
  width: 520px;
  height: 520px;
  border-radius: 50%;
  border: 1px solid rgba(100,165,235,0.1);
  pointer-events: none;
}}
.radar::before {{
  content: '';
  position: absolute;
  inset: 80px;
  border-radius: 50%;
  border: 1px solid rgba(100,165,235,0.07);
}}
.radar::after {{
  content: '';
  position: absolute;
  inset: 165px;
  border-radius: 50%;
  border: 1px solid rgba(100,165,235,0.05);
}}
.ch-tl, .ch-tr {{
  position: absolute;
  top: 30px;
  width: 20px;
  height: 20px;
  pointer-events: none;
}}
.ch-tl {{ left: 30px; }}
.ch-tr {{ right: 30px; }}
.ch-tl::before, .ch-tr::before {{
  content: '';
  position: absolute;
  top: 50%;
  left: 0; right: 0;
  height: 1px;
  background: rgba(100,165,235,0.35);
  transform: translateY(-50%);
}}
.ch-tl::after, .ch-tr::after {{
  content: '';
  position: absolute;
  left: 50%;
  top: 0; bottom: 0;
  width: 1px;
  background: rgba(100,165,235,0.35);
  transform: translateX(-50%);
}}
.frame {{
  position: absolute;
  inset: 14px;
  border: 1px solid rgba(255,255,255,0.09);
  pointer-events: none;
}}
.accent-bar {{
  position: absolute;
  top: 14px;
  left: 14px;
  right: 14px;
  height: 3px;
  background: linear-gradient(90deg,
    rgba(90,155,225,0.9) 0%,
    rgba(90,155,225,0.3) 70%,
    transparent 100%);
}}
.content {{
  position: absolute;
  top: 46px;
  left: 64px;
  right: 64px;
  bottom: 44px;
  display: flex;
  flex-direction: column;
}}
.eyebrow {{
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.32em;
  text-transform: uppercase;
  color: rgba(120,180,240,0.75);
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 10px;
}}
.eyebrow-line {{
  width: 26px;
  height: 1px;
  background: rgba(100,165,235,0.5);
  display: inline-block;
  flex-shrink: 0;
}}
.pub-title {{
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 76px;
  font-weight: 700;
  line-height: 0.95;
  letter-spacing: -0.025em;
  color: #FFFFFF;
  margin-bottom: 13px;
}}
.subtitle {{
  font-family: Georgia, 'Times New Roman', serif;
  font-style: italic;
  font-size: 15px;
  color: rgba(185,215,245,0.68);
  margin-bottom: 20px;
  letter-spacing: 0.01em;
  line-height: 1.4;
}}
.divider {{
  width: 100%;
  height: 1px;
  background: rgba(255,255,255,0.1);
  margin-bottom: 20px;
}}
.issue-title {{
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 36px;
  font-weight: 700;
  line-height: 1.18;
  letter-spacing: -0.012em;
  color: rgba(255,255,255,0.95);
  flex: 1;
  max-width: 730px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}}
.coverage {{
  font-family: Georgia, 'Times New Roman', serif;
  font-style: italic;
  font-size: 14px;
  color: rgba(170,205,240,0.58);
  margin-top: 8px;
}}
.bottom-strip {{
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  border-top: 1px solid rgba(255,255,255,0.1);
  padding-top: 14px;
  margin-top: auto;
}}
.stats {{
  display: flex;
  gap: 32px;
  align-items: flex-end;
}}
.stat {{
  display: flex;
  flex-direction: column;
  gap: 3px;
}}
.stat-label {{
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 8.5px;
  font-weight: 700;
  letter-spacing: 0.26em;
  text-transform: uppercase;
  color: rgba(120,180,240,0.62);
}}
.stat-value {{
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 26px;
  font-weight: 700;
  color: #FFFFFF;
  line-height: 1;
}}
.stat-value.date-val {{
  font-size: 16px;
  font-weight: 700;
  font-style: normal;
  letter-spacing: 0.02em;
}}
.stat-value.text-val {{
  font-size: 14px;
  font-style: italic;
  color: rgba(195,220,245,0.82);
}}
.footer {{
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: rgba(140,190,235,0.5);
  text-align: right;
  line-height: 1.3;
}}
</style>
</head>
<body>
<div class="canvas">
  <svg class="grid-svg" xmlns="http://www.w3.org/2000/svg" width="1200" height="630">
    <defs>
      <pattern id="grid" width="60" height="60" patternUnits="userSpaceOnUse">
        <path d="M 60 0 L 0 0 0 60" fill="none"
              stroke="rgba(100,165,235,0.5)" stroke-width="0.45"/>
      </pattern>
    </defs>
    <rect width="1200" height="630" fill="url(#grid)" opacity="0.14"/>
    <line x1="780" y1="0" x2="1200" y2="380"
          stroke="rgba(90,155,225,0.055)" stroke-width="1"/>
    <line x1="880" y1="0" x2="1200" y2="280"
          stroke="rgba(90,155,225,0.035)" stroke-width="1"/>
  </svg>
  <div class="radar"></div>
  <div class="ch-tl"></div>
  <div class="ch-tr"></div>
  <div class="frame"></div>
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
          <div class="stat-label">Week Ending</div>
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


def _build_html(sidecar: dict) -> str:
    """Fill the HTML template with escaped sidecar data."""
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

    return (
        _HTML_TEMPLATE
        .replace(_PLACEHOLDER_TITLE, html_lib.escape(title))
        .replace(_PLACEHOLDER_COVERAGE, coverage_html)
        .replace(_PLACEHOLDER_WEEK_ENDING,
                 html_lib.escape(week_ending or "—"))
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
    from PIL import Image, ImageDraw  # type: ignore

    img = Image.new("RGB", (WIDTH, HEIGHT), _BG)

    # Gradient-ish background: lighten the right half slightly
    gradient = Image.new("RGB", (WIDTH, HEIGHT), _BG)
    for x in range(WIDTH):
        blend = int(x / WIDTH * 18)
        c = tuple(min(255, _BG[i] + blend) for i in range(3))
        for y in range(HEIGHT):
            gradient.putpixel((x, y), c)
    img.paste(gradient)

    draw = ImageDraw.Draw(img)

    # Subtle grid lines
    for x in range(0, WIDTH, 60):
        draw.line([(x, 0), (x, HEIGHT)], fill=(100, 165, 235, 28), width=1)
    for y in range(0, HEIGHT, 60):
        draw.line([(0, y), (WIDTH, y)], fill=(100, 165, 235, 28), width=1)

    # Radar circles (lower-right)
    for r in [260, 180, 100]:
        draw.ellipse(
            [(WIDTH - 110 - r, HEIGHT - 110 - r),
             (WIDTH - 110 + r, HEIGHT - 110 + r)],
            outline=(100, 165, 235, 25),
        )

    # Frame border
    draw.rectangle([(14, 14), (WIDTH - 14, HEIGHT - 14)],
                   outline=(255, 255, 255, 22), width=1)

    # Blue accent bar (top inside frame)
    draw.rectangle([(14, 14), (WIDTH - 14, 17)],
                   fill=(90, 155, 225, 200))

    # Crosshair TL
    cx, cy = 30, 30
    draw.line([(cx - 10, cy), (cx + 10, cy)],
              fill=(100, 165, 235, 90), width=1)
    draw.line([(cx, cy - 10), (cx, cy + 10)],
              fill=(100, 165, 235, 90), width=1)
    # Crosshair TR
    cx2, cy2 = WIDTH - 30, 30
    draw.line([(cx2 - 10, cy2), (cx2 + 10, cy2)],
              fill=(100, 165, 235, 90), width=1)
    draw.line([(cx2, cy2 - 10), (cx2, cy2 + 10)],
              fill=(100, 165, 235, 90), width=1)

    draw = ImageDraw.Draw(img)
    margin_x = 64
    inner_w = WIDTH - 2 * margin_x
    y = 50

    # Eyebrow
    eye_font = _find_font(_SANS_BOLD, 13)
    eye_text = "  ".join("CHINA MIL WATCH")
    draw.text((margin_x, y), eye_text, font=eye_font,
              fill=tuple(int(c * 0.75) for c in _MUTED))
    y += 32

    # Publication title
    title_font = _find_font(_SERIF_BOLD, 80)
    draw.text((margin_x, y), "THE PLA WATCH", font=title_font, fill=_WHITE)
    y += 90

    # Subtitle
    sub_font = _find_font(_SERIF_ITALIC, 16)
    draw.text((margin_x, y),
              "Weekly Briefing on Chinese Military and Security Developments",
              font=sub_font, fill=(*_TXT2[:3], 172))
    y += 34

    # Hairline
    draw.line([(margin_x, y), (WIDTH - margin_x, y)],
              fill=(255, 255, 255, 25), width=1)
    y += 22

    # Issue title
    title = sidecar.get("title", "").strip()
    for pfx in ("The PLA Watch:", "The PLA Watch —", "PLA Watch:"):
        if title.lower().startswith(pfx.lower()):
            title = title[len(pfx):].strip()
            break
    hed_font = _find_font(_SERIF_BOLD, 38)
    hed_lines = _wrap(draw, title, hed_font, inner_w - 40)
    hed_lines = _truncate_lines(draw, hed_lines, hed_font, inner_w - 40, 3)
    for line in hed_lines:
        draw.text((margin_x, y), line, font=hed_font, fill=_WHITE)
        y += 48
    y += 6

    # Coverage
    week_start = sidecar.get("week_start", "")
    week_ending = sidecar.get("week_ending", "") or sidecar.get("date", "")
    cov = _format_coverage(week_start, week_ending)
    if cov:
        cov_font = _find_font(_SERIF_ITALIC, 15)
        draw.text((margin_x, y), f"Coverage: {cov}", font=cov_font,
                  fill=(*_MUTED[:3], 148))
        y += 26

    # Bottom strip
    strip_y = HEIGHT - 96
    draw.line([(margin_x, strip_y), (WIDTH - margin_x, strip_y)],
              fill=(255, 255, 255, 25), width=1)

    lbl_font = _find_font(_SANS_BOLD, 9)
    num_font = _find_font(_SERIF_BOLD, 28)
    sm_font = _find_font(_SERIF_ITALIC, 15)
    date_font = _find_font(_SERIF_BOLD, 17)

    cells = [
        ("WEEK ENDING", week_ending or "—", "date"),
        ("ARTICLES", str(sidecar.get("n_articles", 0)), "num"),
        ("SIGNIFICANT", str(sidecar.get("n_significant", 0)), "num"),
    ]
    edition_label = (sidecar.get("edition_label") or "").strip()
    if edition_label:
        cells.append(("EDITION", edition_label, "text"))

    col_w = inner_w / len(cells)
    label_y = strip_y + 16
    value_y = strip_y + 34
    for i, (lbl, val, kind) in enumerate(cells):
        cx = margin_x + int(i * col_w)
        spaced = "  ".join(lbl)
        draw.text((cx, label_y), spaced, font=lbl_font,
                  fill=(*_MUTED[:3], 160))
        if kind == "num":
            draw.text((cx, value_y), val, font=num_font, fill=_WHITE)
            nw, nh = _measure(draw, val, num_font)
            draw.rectangle([(cx, value_y + nh + 5),
                             (cx + 22, value_y + nh + 7)],
                            fill=_ACCENT)
        elif kind == "date":
            draw.text((cx, value_y + 6), val, font=date_font, fill=_WHITE)
        else:
            draw.text((cx, value_y + 8), val, font=sm_font, fill=_TXT2)

    # Footer
    foot_font = _find_font(_SANS_REG, 12)
    draw.text((margin_x, HEIGHT - 30), "China Mil Watch",
              font=foot_font, fill=(*_MUTED[:3], 128))

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


def generate_one(json_path: Path, force: bool = False) -> Optional[Path]:
    sidecar = _load_sidecar(json_path)
    sidecar_date = sidecar.get("date") or json_path.stem
    out_path = _cover_path_for(sidecar_date)
    thumb_path = _thumb_path_for(sidecar_date)

    if out_path.exists() and not force:
        print(f"[skip] {out_path.relative_to(ROOT)} already exists "
              f"(use --force to overwrite)")
    else:
        render_cover(sidecar, out_path)
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
