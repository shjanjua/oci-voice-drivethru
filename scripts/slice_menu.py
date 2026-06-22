#!/usr/bin/env python3
"""Slice the AI-generated master menu image into per-item transparent PNGs.

The master is a 2-row x 5-column grid of photographic coffee/pastry items on a
transparent (alpha) background. The items are separated by transparent gaps, but
their saucers cross the even W/5 grid lines — so we slice at the *detected* gaps
(content-aware via the alpha column projection), not on a fixed grid. Each item is
then tightened to its alpha bounding box, centred on a square transparent canvas,
and written to frontend/menu/<item_id>.png at a uniform size.

Usage:
    uv run python scripts/slice_menu.py ~/Downloads/voicedrivethrumenuimages.png [--debug]

--debug also writes frontend/menu/_contact_sheet.png so you can eyeball that each
item id lines up with the right crop.
"""
from __future__ import annotations

import pathlib
import sys

from PIL import Image

# Canonical order — MUST match the master image (row-major, 2 rows x 5 cols).
ITEMS = [
    ["flat_white", "cappuccino", "americano", "latte", "cortado"],
    ["hot_chocolate", "chocolate_brownie", "cookie", "banana_bread", "muffin"],
]

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "frontend" / "menu"
OUT_SIZE = 360          # uniform square output (px)
QUALITY = 82            # WebP quality (photos with alpha → ~25-50KB each)
PAD = 0.06              # transparent breathing space around each item
ALPHA_THR = 24          # alpha above this counts as "ink" for the projections
MIN_RUN = 8             # ignore column runs narrower than this (anti-aliasing specks)
GAP = 6                 # this many consecutive empty columns closes an item run


def _col_ink(alpha: Image.Image, y0: int, y1: int, step: int = 2) -> list[int]:
    """Per-column count of opaque pixels within the horizontal strip [y0, y1)."""
    strip = alpha.crop((0, y0, alpha.width, y1))
    px = strip.load()
    w, h = strip.size
    cols = [0] * w
    for x in range(w):
        s = 0
        for y in range(0, h, step):
            if px[x, y] > ALPHA_THR:
                s += 1
        cols[x] = s
    return cols


def _runs(cols: list[int]) -> list[tuple[int, int]]:
    """Return [(x0, x1), ...] spans of non-empty columns (end-exclusive)."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for x, v in enumerate(cols):
        if v > 0:
            if start is None:
                start = x
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= GAP:
                out.append((start, x - gap + 1))
                start = None
    if start is not None:
        out.append((start, len(cols)))
    return [(s, e) for s, e in out if e - s >= MIN_RUN]


def _row_split(alpha: Image.Image) -> int:
    """y where the two rows separate: the emptiest row in the middle band."""
    w, h = alpha.size
    px = alpha.load()
    lo, hi = int(h * 0.45), int(h * 0.55)
    best_y, best = lo, None
    for y in range(lo, hi):
        s = sum(1 for x in range(0, w, 4) if px[x, y] > ALPHA_THR)
        if best is None or s < best:
            best, best_y = s, y
    return best_y


def slice_master(path: pathlib.Path, debug: bool = False) -> None:
    im = Image.open(path).convert("RGBA")
    alpha = im.getchannel("A")
    ysplit = _row_split(alpha)
    strips = [(0, ysplit), (ysplit, im.height)]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    crops: list[tuple[str, Image.Image]] = []
    for r, (y0, y1) in enumerate(strips):
        runs = _runs(_col_ink(alpha, y0, y1))
        if len(runs) != 5:
            raise SystemExit(
                f"row {r}: expected 5 item runs, found {len(runs)}: {runs}\n"
                f"Check the master layout or tune ALPHA_THR/GAP/MIN_RUN."
            )
        for c, (x0, x1) in enumerate(runs):
            item_id = ITEMS[r][c]
            region = im.crop((x0, y0, x1, y1))
            # Tighten to the SOLID item: the master has a faint sub-threshold alpha
            # halo, so measure the bbox from a thresholded mask (not raw alpha>0).
            solid = region.getchannel("A").point(lambda p: 255 if p > ALPHA_THR else 0)
            bbox = solid.getbbox()
            if bbox is None:
                raise SystemExit(f"EMPTY region for {item_id} (row {r}, col {c})")
            region = region.crop(bbox)
            # Drop the faint halo to fully transparent so no grey haze composites in,
            # but keep real anti-aliased edges (alpha >= threshold) intact.
            rch, gch, bch, ach = region.split()
            ach = ach.point(lambda p: 0 if p < ALPHA_THR else p)
            region = Image.merge("RGBA", (rch, gch, bch, ach))
            w, h = region.size
            side = int(round(max(w, h) * (1 + PAD)))
            canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            canvas.paste(region, ((side - w) // 2, (side - h) // 2), region)  # own alpha = mask
            out = canvas.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
            out.save(OUT_DIR / f"{item_id}.webp", quality=QUALITY, method=6)
            crops.append((item_id, out))
            kb = (OUT_DIR / f"{item_id}.webp").stat().st_size // 1024
            print(f"  {item_id:>18}: src x[{x0:4d},{x1:4d}] item {w}x{h} -> {OUT_SIZE}px webp {kb}KB")

    print(f"\nWrote {len(crops)} PNGs to {OUT_DIR} (row split y={ysplit})")

    if debug:
        cols, cell = 5, OUT_SIZE // 2
        rows = (len(crops) + cols - 1) // cols
        sheet = Image.new("RGBA", (cols * cell, rows * cell), (243, 232, 210, 255))
        for i, (_id, img) in enumerate(crops):
            sheet.alpha_composite(img.resize((cell, cell), Image.LANCZOS),
                                  ((i % cols) * cell, (i // cols) * cell))
        debug_path = pathlib.Path("/tmp/menu_contact_sheet.png")  # not a served asset
        sheet.convert("RGB").save(debug_path)
        print(f"Wrote debug contact sheet -> {debug_path}")


if __name__ == "__main__":
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not pos:
        raise SystemExit("usage: slice_menu.py <master.png> [--debug]")
    master = pathlib.Path(pos[0]).expanduser()
    if not master.exists():
        raise SystemExit(f"master image not found: {master}")
    slice_master(master, debug="--debug" in sys.argv)
