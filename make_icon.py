"""Generate the Nama Onso application icon (assets/NamaOnso.ico).

The 1024-unit geometry matches assets/NamaOnso.svg. Sizes up to 32 px use
a simplified three-bar drawing so the waveform and the cut stay readable.

Run: python tools\\jinriki_prep\\make_icon.py [--preview DIR]
"""
from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
ICON_PATH = os.path.join(ASSETS, "NamaOnso.ico")

BLUE = (0x00, 0x4B, 0x82)
ORANGE = (0xF5, 0x9A, 0x45)
TILE = (0xFA, 0xFB, 0xFC)
EDGE = (0xE3, 0xE8, 0xEE)

SIZES = (16, 24, 32, 48, 64, 128, 256)
SMALL_MAX = 32


def _canvas(size: int, supersample: int = 8):
    total = size * supersample
    img = Image.new("RGBA", (total, total), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    k = total / 1024.0
    draw.rounded_rectangle([40 * k, 40 * k, 984 * k, 984 * k], radius=200 * k,
                           fill=TILE + (255,), outline=EDGE + (255,),
                           width=max(1, round(6 * k)))
    return img, draw, k


def _bar(draw, cx, cy, width, height, radius):
    draw.rounded_rectangle([cx - width / 2, cy - height / 2,
                            cx + width / 2, cy + height / 2],
                           radius=radius, fill=BLUE + (255,))


def render(size: int) -> Image.Image:
    """Draw the icon at ``size`` px; small sizes use the simplified waveform."""
    img, draw, k = _canvas(size)
    if size <= SMALL_MAX:
        for cx, height in ((336, 520), (512, 800), (688, 430)):
            _bar(draw, cx * k, 512 * k, 132 * k, height * k, 66 * k)
        white, line = 118 * k, max(1.0, 66 * k)
        ends = (292 * k, 706 * k, 732 * k, 318 * k)
    else:
        for cx, height in ((276, 300), (394, 460), (512, 600), (630, 380), (748, 220)):
            _bar(draw, cx * k, 512 * k, 74 * k, height * k, 37 * k)
        white, line = 32 * k, max(1.0, 15 * k)
        ends = (274 * k, 690 * k, 750 * k, 330 * k)
    draw.line(list(ends), fill=TILE + (255,), width=max(1, round(white)))
    draw.line(list(ends), fill=ORANGE + (255,), width=max(1, round(line)))
    return img.resize((size, size), Image.LANCZOS)


def build_ico(path: str = ICON_PATH) -> str:
    frames = [render(size) for size in SIZES]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    largest = frames[-1]
    largest.save(path, format="ICO", sizes=[(s, s) for s in SIZES],
                 append_images=frames[:-1], bitmap_format="bmp")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", default="", help="folder for per-size PNG previews")
    args = parser.parse_args(argv)
    path = build_ico()
    print("wrote", path)
    if args.preview:
        os.makedirs(args.preview, exist_ok=True)
        for size in SIZES:
            render(size).save(os.path.join(args.preview, f"icon-{size}.png"))
        print("previews in", args.preview)
    return 0


if __name__ == "__main__":
    sys.exit(main())
