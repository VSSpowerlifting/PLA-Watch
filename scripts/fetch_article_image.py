"""
Fetch a cover background image from PLA Watch source article URLs.

For each URL in order, tries:
  1. og:image / twitter:image meta tags in the article HTML.
  2. First content JPG/PNG in the article body (≥800×400 px; skips nav,
     template, sharing, and GIF images).

Downloads the best candidate to:
  output/the-pla-watch/media/{post_date}-source-image{ext}
Writes a JSON metadata sidecar at:
  output/the-pla-watch/media/{post_date}-source-image.json

In --apply mode also adds a media_items entry to the post JSON so that
generate_pla_watch_cover.py picks it up automatically on the next run.

Uses only stdlib urllib + Pillow (already required for cover generation).
Never calls the Anthropic API. Never runs pipeline.py.

Usage
-----
    python scripts/fetch_article_image.py --post-date 2026-05-16 --dry-run \\
        --url http://www.81.cn/hj_208557/16459619.html

    python scripts/fetch_article_image.py --post-date 2026-05-16 --apply \\
        --url http://www.81.cn/hj_208557/16459619.html \\
        --url http://www.81.cn/yw_208727/16460297.html

Public API (importable)
-----------------------
    from scripts.fetch_article_image import fetch_best_image
    path = fetch_best_image(
        urls=["http://www.81.cn/hj_208557/16459619.html"],
        post_date="2026-05-16",
        dry_run=False,
    )
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "output" / "the-pla-watch" / "posts"
MEDIA_DIR = ROOT / "output" / "the-pla-watch" / "media"

USER_AGENT = (
    "ChinaMilWatch-PLAWatch-CoverFetcher/0.1 "
    "(https://chinamilwatch.org; contact via site)"
)
HTTP_TIMEOUT = 20
MIN_WIDTH = 800
MIN_HEIGHT = 400
MAX_SIDE = 1600  # resize to this width if larger

# URL path fragments that indicate nav/logo/decoration rather than content.
_SKIP_PATH_FRAGMENTS = (
    "/template/",
    "/img/share",
    "/img/logo",
    "/img/icon",
    "/themes/",
    "/static/",
    "/common/",
)
_SKIP_EXTENSIONS = {".gif", ".svg", ".ico", ".bmp"}


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        charset = "utf-8"
        ct = r.headers.get("content-type", "")
        m = re.search(r"charset=([\w-]+)", ct, re.I)
        if m:
            charset = m.group(1)
        raw = r.read()
    return raw.decode(charset, errors="replace")


def _extract_og_image(html: str, base_url: str) -> Optional[str]:
    """Return the og:image or twitter:image URL, absolute, or None."""
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I)
        if m:
            return urllib.parse.urljoin(base_url, m.group(1).strip())
    return None


def _extract_content_images(html: str, base_url: str) -> list[str]:
    """Return candidate content image URLs (absolute), ordered by appearance."""
    srcs: list[str] = []
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I):
        raw = m.group(1).strip()
        if not raw or raw.startswith("data:"):
            continue
        url = urllib.parse.urljoin(base_url, raw)
        parsed = urllib.parse.urlparse(url)
        path_lower = parsed.path.lower()
        if Path(path_lower).suffix in _SKIP_EXTENSIONS:
            continue
        if any(frag in path_lower for frag in _SKIP_PATH_FRAGMENTS):
            continue
        srcs.append(url)
    return srcs


def _probe_image(url: str) -> Optional[tuple[bytes, int, int]]:
    """Download image, return (bytes, width, height) or None if unusable."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = r.read()
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        return data, w, h
    except Exception:
        return None


def _resize_and_save(data: bytes, out_path: Path, max_width: int = MAX_SIDE) -> Path:
    """Resize to max_width if wider, save as progressive JPEG."""
    from PIL import Image  # type: ignore
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if w > max_width:
        new_h = int(h * max_width / w)
        img = img.resize((max_width, new_h), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="JPEG", quality=82, progressive=True, optimize=True)
    return out_path


# ── Per-URL fetch ─────────────────────────────────────────────────────────────

def _try_url(article_url: str, log_prefix: str) -> Optional[dict]:
    """
    Try to find a usable cover image in one article URL.
    Returns a dict with keys: image_url, data, width, height, source_label.
    """
    print(f"{log_prefix}  fetching article: {article_url}")
    try:
        html = _fetch_html(article_url)
    except Exception as exc:
        print(f"{log_prefix}    WARN: could not fetch article ({exc!r})")
        return None

    # 1. og:image / twitter:image
    og_url = _extract_og_image(html, article_url)
    if og_url:
        print(f"{log_prefix}    og:image → {og_url}")
        result = _probe_image(og_url)
        if result:
            data, w, h = result
            if w >= MIN_WIDTH and h >= MIN_HEIGHT:
                print(f"{log_prefix}    ✓ og:image usable ({w}×{h})")
                return {
                    "image_url": og_url,
                    "data": data,
                    "width": w,
                    "height": h,
                    "source_label": "og:image",
                }
            else:
                print(f"{log_prefix}    og:image too small ({w}×{h}), trying body images")
        else:
            print(f"{log_prefix}    og:image could not be probed, trying body images")

    # 2. Content images
    candidates = _extract_content_images(html, article_url)
    print(f"{log_prefix}    found {len(candidates)} body image candidate(s)")
    for img_url in candidates:
        result = _probe_image(img_url)
        if not result:
            continue
        data, w, h = result
        if w < MIN_WIDTH or h < MIN_HEIGHT:
            print(f"{log_prefix}    skip {img_url[-50:]} ({w}×{h}, too small)")
            continue
        print(f"{log_prefix}    ✓ body image usable ({w}×{h}): {img_url[-60:]}")
        return {
            "image_url": img_url,
            "data": data,
            "width": w,
            "height": h,
            "source_label": "article body image",
        }

    print(f"{log_prefix}    no usable image found in this article")
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_best_image(
    urls: list[str],
    post_date: str,
    dry_run: bool = True,
    update_sidecar: bool = True,
) -> Optional[Path]:
    """
    Try each URL in order. Download the first usable image to MEDIA_DIR.
    Returns the local Path on success, None if nothing usable was found.

    In dry_run mode returns None without writing any files.
    If update_sidecar is True and a sidecar JSON exists, adds/replaces the
    auto-source-image entry in media_items.
    """
    prefix = f"[fetch-image:{post_date}]"

    for article_url in urls:
        result = _try_url(article_url, prefix)
        if result is None:
            continue

        image_url = result["image_url"]
        data = result["data"]
        w, h = result["width"], result["height"]
        source_label = result["source_label"]

        image_path = MEDIA_DIR / f"{post_date}-source-image.jpg"
        meta_path = MEDIA_DIR / f"{post_date}-source-image.json"

        if dry_run:
            print(f"{prefix}  [dry-run] would save {w}×{h} image → {image_path.relative_to(ROOT)}")
            print(f"{prefix}  [dry-run] source: {image_url}  ({source_label})")
            return None

        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        _resize_and_save(data, image_path)
        final_w, final_h = w, h
        if w > MAX_SIDE:
            final_w = MAX_SIDE
            final_h = int(h * MAX_SIDE / w)
        print(f"{prefix}  saved {image_path.relative_to(ROOT)} "
              f"({final_w}×{final_h} after resize)")

        metadata = {
            "article_url": article_url,
            "image_url": image_url,
            "source_label": source_label,
            "original_width": w,
            "original_height": h,
            "local_path": str(image_path.relative_to(ROOT)),
            "downloaded_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "note": (
                "Downloaded from PLA Daily / 81.cn source article for editorial "
                "cover background use. Image is under PRC government / PLA Daily "
                "copyright; used here as editorial visual context."
            ),
        }
        meta_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"{prefix}  wrote metadata → {meta_path.relative_to(ROOT)}")

        if update_sidecar:
            json_path = POSTS_DIR / f"{post_date}.json"
            if json_path.exists():
                sidecar = json.loads(json_path.read_text(encoding="utf-8"))
                items = [
                    m for m in (sidecar.get("media_items") or [])
                    if not (isinstance(m, dict) and m.get("source_label") == "article body image"
                            or isinstance(m, dict) and m.get("source_label") == "og:image")
                ]
                items.insert(0, {
                    "type": "image",
                    "src": f"../media/{image_path.name}",
                    "alt": "Source article image",
                    "caption": (
                        "Cover background image sourced from the lead source "
                        "article. Included for editorial visual context only."
                    ),
                    "source_url": article_url,
                    "image_url": image_url,
                    "source_label": source_label,
                    "auto_selected": True,
                })
                sidecar["media_items"] = items
                json_path.write_text(
                    json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                print(f"{prefix}  updated sidecar media_items → {json_path.relative_to(ROOT)}")

        return image_path

    print(f"{prefix}  no usable image found across {len(urls)} URL(s) — abstract fallback will be used")
    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Fetch a cover background image from source article URLs."
    )
    p.add_argument("--post-date", required=True,
                   help="Issue date YYYY-MM-DD (matches sidecar filename).")
    p.add_argument("--url", dest="urls", action="append", default=[],
                   metavar="URL",
                   help="Source article URL to try (repeat for multiple, tried in order).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=False,
                   help="Print what would be done; do not write files. (default)")
    g.add_argument("--apply", action="store_true",
                   help="Download and write files.")
    p.add_argument("--no-rerender", action="store_true",
                   help="Skip rerender_pla_watch.py after --apply.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    dry_run = not args.apply

    if not args.urls:
        # Fall back to source_trail URLs from the sidecar.
        json_path = POSTS_DIR / f"{args.post_date}.json"
        if json_path.exists():
            sidecar = json.loads(json_path.read_text(encoding="utf-8"))
            args.urls = [
                e["url"] for e in (sidecar.get("source_trail") or [])
                if e.get("url")
            ][:4]
            if args.urls:
                print(f"No --url given; using source_trail from sidecar: {args.urls}")
        if not args.urls:
            print("ERROR: no --url given and sidecar has no source_trail URLs")
            return 2

    result = fetch_best_image(
        urls=args.urls,
        post_date=args.post_date,
        dry_run=dry_run,
        update_sidecar=True,
    )

    if result and not dry_run and not args.no_rerender:
        print("\nRe-rendering PLA Watch covers and HTML…")
        rerender = ROOT / "scripts" / "rerender_pla_watch.py"
        proc = subprocess.run(
            [sys.executable, str(rerender), "--force-covers"],
            cwd=str(ROOT),
        )
        return proc.returncode

    return 0 if (result or dry_run) else 1


if __name__ == "__main__":
    sys.exit(main())
