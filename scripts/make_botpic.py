"""Картинки для профиля бота в @BotFather.

Оформление то же, что у обложек недель: градиентная цифра на почти белом поле.

  data/images/description.png — Description Picture, 640×360 (стандарт Telegram)
  data/images/botpic.png      — аватар бота, 512×512 (квадрат)

Запуск: python -m scripts.make_botpic
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .make_images import BG, BOLD, INK, MUTED, REG, gradient

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "images"
ACCENT = (58, 92, 214)


def _paint(img: Image.Image, mask: Image.Image) -> None:
    """Залить фигуру градиентом так, чтобы вся палитра уложилась в её высоту, а не в высоту кадра."""
    box = mask.getbbox()
    top, bottom = box[1], box[3]
    img.paste(gradient((img.width, bottom - top)), (0, top), mask.crop((0, top, img.width, bottom)))


def _fit(draw, text: str, font_path, size: int, max_w: int) -> ImageFont.FreeTypeFont:
    """Подобрать кегль так, чтобы строка гарантированно поместилась в отведённую ширину."""
    while size > 10:
        font = ImageFont.truetype(str(font_path), size)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
    return ImageFont.truetype(str(font_path), size)


def description_picture(path: Path) -> None:
    """Картинка над описанием бота: её видят до нажатия «Запустить». Ровно 640×360."""
    w, h = 640, 360
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)

    # Градиентная «3» — три недели марафона, тот же приём, что на обложках недель.
    num_font = ImageFont.truetype(str(BOLD), 250)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((44, h // 2 + 4), "3", font=num_font, fill=255, anchor="lm")
    _paint(img, mask)

    x, right = 214, w - 28
    title_font = _fit(draw, "Добрых дел", BOLD, 46, right - x)
    draw.text((x, 108), "МАРАФОН", font=ImageFont.truetype(str(BOLD), 20), fill=MUTED)
    draw.text((x, 138), "Добрых дел", font=title_font, fill=INK)
    draw.rounded_rectangle((x, 208, x + 56, 213), radius=3, fill=ACCENT)
    f = ImageFont.truetype(str(REG), 19)
    draw.text((x, 234), "3 недели · 12 добрых дел", font=f, fill=MUTED)
    draw.text((x, 262), "командный зачёт", font=f, fill=MUTED)

    img.save(path)


def botpic(path: Path) -> None:
    """Аватар: квадрат, крупная цифра и подпись — читается даже в списке чатов."""
    size = 512
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)

    num_font = ImageFont.truetype(str(BOLD), 340)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).text((size // 2, 200), "3", font=num_font, fill=255, anchor="mm")
    _paint(img, mask)

    draw.text((size // 2, 378), "МАРАФОН", font=ImageFont.truetype(str(BOLD), 30), fill=MUTED, anchor="mm")
    draw.text((size // 2, 422), "ДОБРЫХ ДЕЛ", font=ImageFont.truetype(str(BOLD), 44), fill=INK, anchor="mm")

    img.save(path)


def build() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = [OUT / "description.png", OUT / "botpic.png"]
    description_picture(paths[0])
    botpic(paths[1])
    return paths


if __name__ == "__main__":
    for p in build():
        print("Готово:", p)
