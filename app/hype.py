"""Ролик «добрые дела недели»: анимация из уже принятых работ участников.

Телеграм показывает GIF автоматически и без звука — это и нужно: человек открывает
чат и сразу видит ленту из фотографий коллег. Кадры берём только из принятых
отчётов (их уже посмотрел человек из P&C), под каждым кадром — кто и что сделал.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
BOLD, REG = FONT_DIR / "DejaVuSans-Bold.ttf", FONT_DIR / "DejaVuSans.ttf"

W, H = 720, 720          # квадрат: одинаково хорошо смотрится и в телефоне, и в десктопе
CAPTION_H = 120          # полоса с подписью под кадром
FRAME_MS = 1200          # столько держится один кадр
MAX_SECONDS = 30         # жёсткое ограничение длительности из задачи
MAX_FRAMES = MAX_SECONDS * 1000 // FRAME_MS

BG = (13, 17, 23)
TEXT = (240, 244, 248)
MUTED = (150, 160, 175)


def max_frames() -> int:
    """Сколько кадров влезает в лимит длительности."""
    return MAX_FRAMES


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:  # на хостинге системных шрифтов может не быть — рассылку это не роняет
        try:
            return ImageFont.load_default(size)
        except TypeError:  # Pillow старее 10
            return ImageFont.load_default()


def _fit(text: str, font, draw: ImageDraw.ImageDraw, width: int) -> str:
    """Обрезать строку по ширине кадра, чтобы подпись не уезжала за край."""
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def make_frame(raw: bytes, title: str, subtitle: str) -> Image.Image:
    """Один кадр: фотография участника по центру и подпись под ней."""
    canvas = Image.new("RGB", (W, H), BG)
    photo = Image.open(io.BytesIO(raw)).convert("RGB")
    box_h = H - CAPTION_H
    scale = min(W / photo.width, box_h / photo.height)
    photo = photo.resize((max(int(photo.width * scale), 1), max(int(photo.height * scale), 1)),
                         Image.LANCZOS)
    canvas.paste(photo, ((W - photo.width) // 2, (box_h - photo.height) // 2))

    draw = ImageDraw.Draw(canvas)
    f_title, f_sub = _font(BOLD, 34), _font(REG, 26)
    draw.text((32, box_h + 18), _fit(title, f_title, draw, W - 64), font=f_title, fill=TEXT)
    draw.text((32, box_h + 64), _fit(subtitle, f_sub, draw, W - 64), font=f_sub, fill=MUTED)
    return canvas


def save_gif(frames: list[Image.Image], path: str | Path) -> Path:
    """Собрать кадры в зацикленную анимацию не длиннее 30 секунд."""
    if not frames:
        raise ValueError("нет кадров для анимации")
    frames = frames[:MAX_FRAMES]
    path = Path(path)
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:],
                   duration=FRAME_MS, loop=0, optimize=True)
    return path


def duration_seconds(count: int) -> float:
    return min(count, MAX_FRAMES) * FRAME_MS / 1000


async def build_from_submissions(bot, subs, path: str | Path) -> tuple[Path, int] | None:
    """Скачать фотографии принятых отчётов и склеить ролик. None — если показывать нечего."""
    frames: list[Image.Image] = []
    for sub in subs:
        if len(frames) >= MAX_FRAMES:
            break
        photo = next((f for f in (sub.files or []) if f.get("type") == "photo"), None)
        if not photo:
            continue
        try:
            buf = await bot.download(photo["file_id"])
            title = sub.user.full_name or "Участник"
            team = sub.user.team.name if sub.user.team else "без команды"
            frames.append(make_frame(buf.read(), f"{title} · {team}", sub.task.title))
        except Exception as ex:  # noqa: BLE001
            log.warning("кадр из отчёта %s пропущен: %s", sub.id, ex)
    if not frames:
        return None
    return save_gif(frames, path), len(frames)
