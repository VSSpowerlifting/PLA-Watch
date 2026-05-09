"""
Editorial cover-image generator for The PLA Watch.

Reads a PLA Watch sidecar JSON file (output/the-pla-watch/posts/YYYY-MM-DD.json)
and renders a 1200x630 PNG cover suitable for use as the article hero image and
Open Graph / Twitter Card preview.

Style intent
------------
Editorial publication feel — off-white paper background, black/dark serif
headline, a thin China Mil Watch red rule, tabular numerals, and a subtle
"PLA WATCH" watermark. No photos, no icons, no flags or military imagery.

Usage
-----
    python scripts/generate_pla_watch_cover.py [--date YYYY-MM-DD] [--all]
                                                [--force]

Defaults to the newest sidecar in output/the-pla-watch/posts/. Skips writing
if the target PNG already exists, unless --force is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as date_cls
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "output" / "the-pla-watch" / "posts"
MEDIA_DIR = ROOT / "output" / "the-pla-watch" / "media"

# ── Canvas ────────────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1200, 630

# ── Palette (matches site CSS variables) ──────────────────────────────────────
BG          = (244, 239, 230)   # --color-bg, warm off-white
PAPER_RULE  = (220, 215, 204)   # --color-border
PAPER_SOFT  = (236, 231, 220)   # --color-border-soft
TEXT_PRIMARY    = (22, 22, 22)
TEXT_SECONDARY  = (74, 74, 74)
TEXT_MUTED      = (122, 122, 122)
BRAND_RED       = (179, 19, 43)   # --color-brand
WATERMARK_RED   = (179, 19, 43)   # subtle, low-opacity overlay


# ── Font discovery ────────────────────────────────────────────────────────────
def _find_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    """
    Try each font path in order and return the first that loads.
    Falls back to PIL's default bitmap font as a last resort so the script
    never hard-fails on a machine with unusual font layout.
    """
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


SERIF_BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/Library/Fonts/Georgia Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
]
SERIF_ITALIC_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
    "/Library/Fonts/Georgia Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
]
SERIF_BOLD_ITALIC_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf",
    "/Library/Fonts/Georgia Bold Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
]
SERIF_REGULAR_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]
SANS_BOLD_CANDIDATES = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
SANS_REGULAR_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


# ── Text helpers ──────────────────────────────────────────────────────────────
def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Greedy word-wrap using the rendered width of each candidate line."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = current + " " + word
        w, _ = _measure(draw, candidate, font)
        if w <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _truncate_lines(draw, lines, font, max_width, max_lines):
    """If wrapped headline exceeds max_lines, truncate with an ellipsis."""
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    while last:
        candidate = last.rstrip(" ,;:.-") + "…"
        w, _ = _measure(draw, candidate, font)
        if w <= max_width:
            kept[-1] = candidate
            return kept
        last = last[:-1]
    kept[-1] = "…"
    return kept


# ── Date / context formatting ─────────────────────────────────────────────────
_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _format_long_date(iso: str) -> str:
    try:
        d = date_cls.fromisoformat(iso)
        return f"{_MONTHS[d.month]} {d.day}, {d.year}"
    except Exception:
        return iso


def _format_short_date(iso: str) -> str:
    try:
        d = date_cls.fromisoformat(iso)
        return f"{_MONTHS[d.month][:3]} {d.day}"
    except Exception:
        return iso


def _format_coverage(start: str, end: str) -> str:
    if not start or not end:
        return ""
    if start == end:
        return _format_long_date(start)
    try:
        ds, de = date_cls.fromisoformat(start), date_cls.fromisoformat(end)
        if ds.year == de.year and ds.month == de.month:
            return f"{_MONTHS[ds.month]} {ds.day}–{de.day}, {ds.year}"
        if ds.year == de.year:
            return (f"{_MONTHS[ds.month][:3]} {ds.day} – "
                    f"{_MONTHS[de.month][:3]} {de.day}, {ds.year}")
        return f"{_format_short_date(start)} {ds.year} – {_format_short_date(end)} {de.year}"
    except Exception:
        return f"{start} → {end}"


# ── Compose ───────────────────────────────────────────────────────────────────
def render_cover(sidecar: dict, out_path: Path) -> Path:
    """Render the cover PNG for one PLA Watch sidecar dict, write to out_path."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Subtle paper grain — vertical hairline texture, very low contrast.
    grain = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grain)
    for y in range(0, HEIGHT, 6):
        gdraw.line([(0, y), (WIDTH, y)], fill=(22, 22, 22, 5), width=1)
    img.paste(grain, (0, 0), grain)
    draw = ImageDraw.Draw(img)

    # Margins / grid
    margin_x = 72
    inner_w = WIDTH - 2 * margin_x

    # Top brand rule (matches the site's masthead red bar)
    draw.rectangle([(0, 0), (WIDTH, 6)], fill=BRAND_RED)

    # ── Watermark — large faded "PLA WATCH" lower-right ──────────────────────
    wm_font = _find_font(SERIF_BOLD_ITALIC_CANDIDATES, 220)
    wm_layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    wm_draw = ImageDraw.Draw(wm_layer)
    wm_text = "PLA WATCH"
    wm_w, wm_h = _measure(wm_draw, wm_text, wm_font)
    wm_x = WIDTH - wm_w + 30
    wm_y = HEIGHT - wm_h + 60
    wm_draw.text((wm_x, wm_y), wm_text, font=wm_font,
                 fill=(*WATERMARK_RED, 22))
    img.paste(wm_layer, (0, 0), wm_layer)
    draw = ImageDraw.Draw(img)

    # ── Masthead block ───────────────────────────────────────────────────────
    eyebrow_font = _find_font(SANS_BOLD_CANDIDATES, 18)
    eyebrow = "THE PLA WATCH"
    # Letter-spaced rendering (PIL does not natively support tracking)
    spaced = "  ".join(list(eyebrow))
    y = 64
    draw.rectangle([(margin_x, y + 8), (margin_x + 36, y + 10)], fill=BRAND_RED)
    draw.text((margin_x + 50, y), spaced, font=eyebrow_font, fill=BRAND_RED)
    y += 38

    # Nameplate — italic "The" + bold "PLA Watch"
    the_font = _find_font(SERIF_ITALIC_CANDIDATES, 64)
    plaw_font = _find_font(SERIF_BOLD_ITALIC_CANDIDATES, 64)
    the_text = "The"
    plaw_text = "PLA Watch"
    the_w, _ = _measure(draw, the_text, the_font)
    draw.text((margin_x, y), the_text, font=the_font, fill=TEXT_MUTED)
    draw.text((margin_x + the_w + 18, y), plaw_text, font=plaw_font,
              fill=TEXT_PRIMARY)
    nameplate_h = 64
    y += nameplate_h + 6

    # Tagline
    tagline_font = _find_font(SANS_REGULAR_CANDIDATES, 18)
    tagline = "A weekly publication of China Mil Watch"
    draw.text((margin_x, y), tagline, font=tagline_font, fill=TEXT_SECONDARY)
    y += 34

    # Hairline rule across the full content column
    draw.line([(margin_x, y), (WIDTH - margin_x, y)],
              fill=PAPER_RULE, width=1)
    y += 30

    # ── Issue title (headline) ───────────────────────────────────────────────
    title = sidecar.get("title", "").strip()
    # Drop the prefix "The PLA Watch:" if present — the masthead already shows it.
    for prefix in ("The PLA Watch:", "The PLA Watch —", "PLA Watch:"):
        if title.lower().startswith(prefix.lower()):
            title = title[len(prefix):].strip()
            break

    title_font = _find_font(SERIF_BOLD_CANDIDATES, 60)
    title_lines = _wrap(draw, title, title_font, inner_w)
    title_lines = _truncate_lines(draw, title_lines, title_font, inner_w, 3)

    line_gap = 10
    line_h = title_font.size + line_gap
    for line in title_lines:
        draw.text((margin_x, y), line, font=title_font, fill=TEXT_PRIMARY)
        y += line_h
    y += 14

    # Coverage window (under the headline)
    week_start = sidecar.get("week_start", "")
    week_ending = sidecar.get("week_ending", "") or sidecar.get("date", "")
    coverage_font = _find_font(SERIF_ITALIC_CANDIDATES, 24)
    coverage_text = _format_coverage(week_start, week_ending)
    if coverage_text:
        draw.text((margin_x, y), f"Coverage: {coverage_text}",
                  font=coverage_font, fill=TEXT_SECONDARY)
        y += 36

    # ── Bottom strip — issue date, stats, footer ─────────────────────────────
    strip_y = HEIGHT - 110
    draw.line([(margin_x, strip_y), (WIDTH - margin_x, strip_y)],
              fill=PAPER_RULE, width=1)

    label_font = _find_font(SANS_BOLD_CANDIDATES, 13)
    num_font = _find_font(SERIF_BOLD_CANDIDATES, 34)

    n_articles = sidecar.get("n_articles", 0)
    n_significant = sidecar.get("n_significant", 0)
    issue_date = _format_long_date(sidecar.get("date", "")
                                   or sidecar.get("week_ending", ""))

    cells: list[tuple[str, str]] = [
        ("ISSUE", issue_date or "—"),
        ("ARTICLES", str(n_articles)),
        ("SIGNIFICANT", str(n_significant)),
    ]
    edition_label = (sidecar.get("edition_label") or "").strip()
    if edition_label:
        cells.append(("EDITION", edition_label))

    cell_y_label = strip_y + 18
    cell_y_value = strip_y + 38
    col_w = inner_w / len(cells)
    for i, (label, value) in enumerate(cells):
        cx = margin_x + int(i * col_w)
        # Letter-spaced label
        spaced_label = "  ".join(list(label))
        draw.text((cx, cell_y_label), spaced_label,
                  font=label_font, fill=TEXT_MUTED)

        if label == "ISSUE":
            v_font = _find_font(SERIF_BOLD_CANDIDATES, 22)
            draw.text((cx, cell_y_value + 4), value,
                      font=v_font, fill=TEXT_PRIMARY)
        elif label == "EDITION":
            v_font = _find_font(SERIF_ITALIC_CANDIDATES, 22)
            # Truncate label if too wide for the cell.
            avail = int(col_w) - 16
            v = value
            while v and _measure(draw, v, v_font)[0] > avail:
                v = v[:-1]
            if v != value:
                v = (v.rstrip() + "…") if v else "…"
            draw.text((cx, cell_y_value + 4), v,
                      font=v_font, fill=TEXT_SECONDARY)
        else:
            draw.text((cx, cell_y_value), value,
                      font=num_font, fill=TEXT_PRIMARY)
            # Tiny accent rule under numerical values
            draw.rectangle(
                [(cx, cell_y_value + num_font.size + 6),
                 (cx + 28, cell_y_value + num_font.size + 8)],
                fill=BRAND_RED,
            )

    # Bottom-left footer line
    foot_font = _find_font(SANS_REGULAR_CANDIDATES, 14)
    draw.text((margin_x, HEIGHT - 32),
              "chinamilwatch.org · The PLA Watch",
              font=foot_font, fill=TEXT_MUTED)

    # Bottom-right small "Vol. I" issue label
    vol_label = "VOL. I"
    vol_w, _ = _measure(draw, vol_label, foot_font)
    draw.text((WIDTH - margin_x - vol_w, HEIGHT - 32),
              vol_label, font=foot_font, fill=TEXT_MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    return out_path


# ── CLI ───────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Generate PLA Watch issue cover images.")
    p.add_argument("--date", help="Sidecar date YYYY-MM-DD. Defaults to newest.")
    p.add_argument("--all", action="store_true",
                   help="Generate covers for every sidecar JSON in posts/.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite an existing cover PNG.")
    return p.parse_args()


def _load_sidecar(json_path: Path) -> dict:
    return json.loads(json_path.read_text(encoding="utf-8"))


def _cover_path_for(sidecar_date: str) -> Path:
    return MEDIA_DIR / f"{sidecar_date}-cover.png"


def generate_one(json_path: Path, force: bool = False) -> Optional[Path]:
    sidecar = _load_sidecar(json_path)
    sidecar_date = sidecar.get("date") or json_path.stem
    out_path = _cover_path_for(sidecar_date)
    if out_path.exists() and not force:
        print(f"[skip] {out_path.relative_to(ROOT)} already exists "
              f"(use --force to overwrite)")
        return out_path
    render_cover(sidecar, out_path)
    print(f"[wrote] {out_path.relative_to(ROOT)}")
    return out_path


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
