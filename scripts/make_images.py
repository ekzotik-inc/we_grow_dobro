"""Generate a preview image for every task, in the style of the supplied week covers:
a gradient-filled numeral on a near-white field, with the task title beside it.

Run once after editing data/tasks.json:  python -m scripts.make_images
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "images"
W, H = 1400, 1000
BG = (246, 247, 250)
INK = (24, 24, 27)
MUTED = (113, 113, 122)
# Same ramp as the supplied covers: teal -> blue -> violet -> amber.
STOPS = [(0.0, (26, 168, 168)), (0.35, (58, 92, 214)), (0.62, (139, 92, 246)), (0.82, (245, 165, 36)), (1.0, (196, 196, 205))]

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
BOLD, REG = FONT_DIR / "DejaVuSans-Bold.ttf", FONT_DIR / "DejaVuSans.ttf"


def gradient(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        for i in range(len(STOPS) - 1):
            t0, c0 = STOPS[i]
            t1, c1 = STOPS[i + 1]
            if t0 <= t <= t1:
                k = (t - t0) / (t1 - t0)
                px[0, y] = tuple(round(c0[j] + (c1[j] - c0[j]) * k) for j in range(3))
                break
    return img.resize((w, h), Image.LANCZOS)


def wrap(draw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        probe = f"{cur} {word}".strip()
        if draw.textlength(probe, font=font) <= max_w:
            cur = probe
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def card(number: str, title: str, points: str, week: int, path: Path) -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Gradient-filled numeral on the left, like the week covers.
    num_font = ImageFont.truetype(str(BOLD), 460)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).text((110, H // 2), number, font=num_font, fill=255, anchor="lm")
    img.paste(gradient((W, H)), (0, 0), mask)

    x = 620
    f_title = ImageFont.truetype(str(BOLD), 62)
    f_meta = ImageFont.truetype(str(REG), 40)

    draw.text((x, 250), f"НЕДЕЛЯ {week}", font=ImageFont.truetype(str(BOLD), 34), fill=MUTED)
    lines = wrap(draw, title, f_title, W - x - 90)[:4]
    y = 330
    for line in lines:
        draw.text((x, y), line, font=f_title, fill=INK)
        y += 82

    y += 24
    draw.rounded_rectangle((x, y, x + 90, y + 8), radius=4, fill=(58, 92, 214))
    draw.text((x, y + 44), f"{points} баллов", font=f_meta, fill=MUTED)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)


def main() -> None:
    tasks = json.loads((ROOT / "data" / "tasks.json").read_text(encoding="utf-8"))
    by_week: dict[int, int] = {}
    for t in tasks:
        by_week[t["week"]] = by_week.get(t["week"], 0) + 1
        idx = by_week[t["week"]]
        points = "/".join(str(o["points"]) for o in t.get("options", [])) or str(t["points"])
        name = f"task{t['code']}.png"
        card(str(idx), t["title"], points, t["week"], OUT / name)
        print(f"  {name}  неделя {t['week']} · {t['title'][:44]}")


if __name__ == "__main__":
    main()
