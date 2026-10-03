"""Render the menu bar icon from the AgentOS mark's geometry.

The same four discs and three spokes as the app icon (`make-icon.py`), drawn
black on transparent so macOS treats it as a template image and tints it for
a light or dark menu bar. At 18 pt the app icon's hairline spokes vanish, so
spokes and discs are thickened for the small size. Output:

    desktop/resources/trayTemplate.png      18x18
    desktop/resources/trayTemplate@2x.png   36x36

    uv run python desktop/scripts/make-tray-icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SS = 8  # supersampling
INK = (0, 0, 0, 255)

# make-icon.py's mark, relative to its unit square: (centre, radius).
CENTER = ((0.5, 0.56), 0.117)
NODES = [((0.5, 0.195), 0.069), ((0.14, 0.80), 0.064), ((0.867, 0.80), 0.064)]

# Thickened for 18 pt, as fractions of the mark's unit square.
SPOKE = 0.085
CENTER_R = 0.15
NODE_R = 0.1
# How wide the mark is drawn, as a fraction of the canvas.
FILL = 0.9


def bounds() -> tuple[float, float, float, float]:
    pts = [(CENTER[0], CENTER_R)] + [(xy, NODE_R) for xy, _ in NODES]
    xs0 = min(x - r for (x, _), r in pts)
    xs1 = max(x + r for (x, _), r in pts)
    ys0 = min(y - r for (_, y), r in pts)
    ys1 = max(y + r for (_, y), r in pts)
    return xs0, ys0, xs1, ys1


def render(size: int) -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = bounds()
    unit = s * FILL / (x1 - x0)
    ox = (s - (x1 - x0) * unit) / 2 - x0 * unit
    oy = (s - (y1 - y0) * unit) / 2 - y0 * unit

    def pt(rel: tuple[float, float]) -> tuple[float, float]:
        return (ox + rel[0] * unit, oy + rel[1] * unit)

    def disc(rel: tuple[float, float], r: float) -> None:
        cx, cy = pt(rel)
        rr = r * unit
        draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=INK)

    c = pt(CENTER[0])
    for xy, _ in NODES:
        draw.line([c, pt(xy)], fill=INK, width=round(SPOKE * unit))
    disc(CENTER[0], CENTER_R)
    for xy, _ in NODES:
        disc(xy, NODE_R)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "resources"
    render(18).save(out / "trayTemplate.png")
    render(36).save(out / "trayTemplate@2x.png")
    print(f"wrote {out / 'trayTemplate.png'} and @2x")


if __name__ == "__main__":
    main()
