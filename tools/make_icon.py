"""Generate the app icon — a bold 字 (word/character) on an amber rounded square.

On-brand for a word-miner, and it renders at every size Windows asks for. Run once;
the .ico is committed. Regenerate if the brand colour changes.

    python tools/make_icon.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding="utf-8")

OUT = Path(__file__).resolve().parent.parent / "data" / "icon.ico"
FONT = r"C:\Windows\Fonts\msyhbd.ttc"          # Microsoft YaHei Bold
AMBER = (242, 176, 53)
AMBER_DEEP = (214, 148, 30)
INK = (26, 22, 16)


def rounded(size):
    """One square icon at `size`px: amber rounded tile + a dark 字."""
    scale = 4                                   # supersample for smooth edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(s * 0.22)
    # a subtle top-to-bottom amber gradient tile
    tile = Image.new("RGB", (s, s), AMBER)
    grad = Image.new("L", (1, s))
    for y in range(s):
        grad.putpixel((0, y), int(255 * (1 - y / s * 0.35)))
    tile = Image.composite(Image.new("RGB", (s, s), AMBER),
                           Image.new("RGB", (s, s), AMBER_DEEP), grad.resize((s, s)))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius, fill=255)
    img.paste(tile, (0, 0), mask)

    glyph = "字"
    font = ImageFont.truetype(FONT, int(s * 0.62))
    bb = d.textbbox((0, 0), glyph, font=font)
    gw, gh = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((s - gw) / 2 - bb[0], (s - gh) / 2 - bb[1]), glyph, font=font, fill=INK)

    return img.resize((size, size), Image.LANCZOS)


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = rounded(256)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base.save(OUT, format="ICO",
              sizes=[(x, x) for x in sizes])
    print(f"wrote {OUT}  ({', '.join(f'{x}px' for x in sizes)})")


if __name__ == "__main__":
    main()
