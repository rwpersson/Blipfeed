"""Spike helper: synthesize subtitle-style frames to exercise the pipeline.

Throwaway (ROADMAP Phase 1). Real hardsub accuracy still needs a genuine Douyin/
YouTube frame — this only proves OCR -> segment -> dict runs and is roughly right on
clean-ish white-outlined text over a busy background, which is the common hardsub
look. Writes a few PNGs into spikes/frames/.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding="utf-8")

FONT = r"C:\Windows\Fonts\msyh.ttc"          # Microsoft YaHei
OUT = Path(__file__).resolve().parent / "frames"

# Sentences chosen to include multi-char words (喜欢/中文/学习), particles that
# should be filtered (的/了), and repeats across frames (中文, 喜欢) so the spike
# can eyeball frequency too.
LINES = [
    "我喜欢学习中文",
    "今天天气很好我们去公园吧",
    "他正在看一部很有意思的电影",
    "学习中文需要很多的练习和耐心",
    "这个视频真的太好笑了哈哈哈",
]


def busy_bg(w, h, seed):
    """A gradient-ish noisy background so OCR isn't handed pure white."""
    import random
    rng = random.Random(seed)
    img = Image.new("RGB", (w, h))
    px = img.load()
    base = (rng.randint(20, 90), rng.randint(20, 90), rng.randint(30, 110))
    for y in range(h):
        for x in range(0, w, 4):            # coarse fill, keep it fast
            c = (min(255, base[0] + (x * 120 // w) + rng.randint(-12, 12)),
                 min(255, base[1] + (y * 90 // h) + rng.randint(-12, 12)),
                 min(255, base[2] + rng.randint(-12, 12)))
            for dx in range(4):
                if x + dx < w:
                    px[x + dx, y] = c
    return img


def outlined_text(draw, xy, text, font, fill="white", outline="black", w=3):
    x, y = xy
    for dx in range(-w, w + 1):
        for dy in range(-w, w + 1):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    W, H = 1280, 200                          # a subtitle band, not a whole screen
    font = ImageFont.truetype(FONT, 64)
    for i, line in enumerate(LINES):
        img = busy_bg(W, H, seed=i)
        draw = ImageDraw.Draw(img)
        tw = draw.textlength(line, font=font)
        outlined_text(draw, ((W - tw) / 2, 60), line, font)
        p = OUT / f"frame_{i}.png"
        img.save(p)
        print(f"wrote {p}  ({line})")


if __name__ == "__main__":
    main()
