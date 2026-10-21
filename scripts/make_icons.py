"""Draw the Pantry Chef app icon (a cooking pot with steam) as PNGs.

Run once from the project root after changing the design:
    python scripts/make_icons.py

Needs Pillow (in requirements-dev.txt). The PNGs it writes are committed,
so the app itself doesn't need Pillow.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"

BACKGROUND = (47, 125, 79)  # matches --accent / theme_color
POT = (250, 248, 244)  # cream
SUPERSAMPLE = 4  # draw big, then shrink, for smooth edges


def draw_icon(size: int, art_scale: float) -> Image.Image:
    """Return a square icon. art_scale is how much of the canvas the pot
    drawing may use (smaller for "maskable" icons, which get cropped)."""
    big = size * SUPERSAMPLE
    img = Image.new("RGB", (big, big), BACKGROUND)
    d = ImageDraw.Draw(img)

    # Work in a 100x100 design grid, centered and scaled to fit art_scale.
    unit = big * art_scale / 100
    offset = big * (1 - art_scale) / 2

    def box(x0, y0, x1, y1):
        return [offset + x0 * unit, offset + y0 * unit, offset + x1 * unit, offset + y1 * unit]

    line = max(1, round(5 * unit))

    # Pot body
    d.rounded_rectangle(box(18, 46, 82, 86), radius=12 * unit, fill=POT)
    # Rim (slightly wider than the body)
    d.rounded_rectangle(box(12, 40, 88, 50), radius=5 * unit, fill=POT)
    # Side handles
    d.rounded_rectangle(box(5, 52, 18, 60), radius=4 * unit, fill=POT)
    d.rounded_rectangle(box(82, 52, 95, 60), radius=4 * unit, fill=POT)
    # Three wavy steam lines above the pot: a sine wave drawn as a thick
    # polyline, with round dots on the ends for soft caps.
    for x in (34, 50, 66):
        points = []
        for i in range(41):
            t = i / 40  # 0..1 from bottom to top
            px = x + 3.5 * math.sin(t * 2 * math.pi)
            py = 33 - t * 23
            points.append((offset + px * unit, offset + py * unit))
        d.line(points, fill=POT, width=line, joint="curve")
        for px, py in (points[0], points[-1]):
            r = line / 2
            d.ellipse([px - r, py - r, px + r, py + r], fill=POT)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "icon-192.png": draw_icon(192, 0.72),
        "icon-512.png": draw_icon(512, 0.72),
        # Maskable icons can be cropped to a circle; keep art in the middle 80%.
        "icon-maskable-512.png": draw_icon(512, 0.58),
        # iOS home screen icon. iOS rounds the corners itself.
        "apple-touch-icon.png": draw_icon(180, 0.72),
    }
    for name, img in outputs.items():
        img.save(OUT_DIR / name, optimize=True)
        print("wrote", OUT_DIR / name)


if __name__ == "__main__":
    main()
