"""Рисует экраны бота как скриншоты Telegram — для презентации участникам.

Тексты берутся из самого бота, поэтому картинки не расходятся с тем, что увидят люди.

    python -m scripts.make_shots      # результат: data/shots/*.png
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from html import unescape
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "1:shots")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PC_CONTACT", "Дарья (P&C)")
os.environ.setdefault("PC_USERNAME", "DaryaPMI")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app import keyboards as kb  # noqa: E402
from app import services, texts  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402

OUT = Path("data/shots")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
EMOJI_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

# Тёмная тема Telegram — в ней участники и увидят бота.
BG = (23, 33, 43)
BUBBLE = (24, 37, 51)
TEXT = (255, 255, 255)
MUTED = (149, 165, 180)
ACCENT = (100, 181, 246)
QUOTE_BAR = (100, 181, 246)
BTN = (33, 45, 61)
BTN_TEXT = (100, 181, 246)

W = 720
PAD = 22
LINE = 34
SIZE = 22

EMOJI_RE = re.compile(
    "([\U0001F000-\U0001FAFF←-⇿⌀-➿⬀-⯿☀-⛿]"
    "[️‍\U0001F000-\U0001FAFF]*)"
)
_emoji_cache: dict[tuple[str, int], Image.Image] = {}


def emoji_image(char: str, size: int) -> Image.Image | None:
    key = (char, size)
    if key in _emoji_cache:
        return _emoji_cache[key]
    try:
        font = ImageFont.truetype(EMOJI_FONT, 109)
        tile = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((0, 0), char, font=font, embedded_color=True)
        box = tile.getbbox()
        if box is None:
            return None
        img = tile.crop(box).resize((size, size), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        return None
    _emoji_cache[key] = img
    return img


def parse(html: str) -> list[list[dict]]:
    """HTML сообщения -> строки из кусочков {text, bold, italic, quote}."""
    html = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r"\1", html)
    html = re.sub(r'<a href="[^"]*">(.*?)</a>', r"\1", html)
    html = html.replace("<blockquote expandable>", "<blockquote>")
    lines: list[list[dict]] = [[]]
    bold = italic = quote = False
    token = re.compile(r"</?(b|i|code|blockquote)>|\n|[^<\n]+")
    for m in token.finditer(html):
        piece = m.group(0)
        if piece == "\n":
            lines.append([])
        elif piece.startswith("<"):
            tag = m.group(1)
            closing = piece.startswith("</")
            if tag in ("b",):
                bold = not closing
            elif tag in ("i", "code"):
                italic = not closing
            elif tag == "blockquote":
                quote = not closing
                if not closing:
                    lines.append([])
        else:
            lines[-1].append({"text": unescape(piece), "bold": bold, "italic": italic, "quote": quote})
    return [ln for ln in lines]


def wrap(runs: list[dict], font_reg, font_bold, max_w: int) -> list[list[dict]]:
    """Перенос по словам с учётом жирного текста и эмодзи."""
    out: list[list[dict]] = [[]]
    x = 0
    for run in runs:
        font = font_bold if run["bold"] else font_reg
        for part in EMOJI_RE.split(run["text"]):
            if not part:
                continue
            if EMOJI_RE.fullmatch(part):
                w = SIZE + 4
                if x + w > max_w and out[-1]:
                    out.append([]); x = 0
                out[-1].append({**run, "emoji": part, "w": w}); x += w
                continue
            for word in re.findall(r"\S+\s*|\s+", part):
                w = font.getlength(word)
                if x + w > max_w and out[-1]:
                    out.append([]); x = 0
                    word = word.lstrip()
                    w = font.getlength(word)
                if not word:
                    continue
                out[-1].append({**run, "text": word, "w": w}); x += w
    return out


def render(name: str, html: str, markup=None, header: str = "Марафон добрых дел") -> Path:
    font = ImageFont.truetype(FONT, SIZE)
    bold = ImageFont.truetype(FONT_BOLD, SIZE)
    small = ImageFont.truetype(FONT, 19)

    max_text = W - PAD * 4
    rows: list[list[dict]] = []
    for logical in parse(html):
        wrapped = wrap(logical, font, bold, max_text) if logical else [[]]
        rows.extend(wrapped)

    buttons = []
    if markup:
        for row in markup.inline_keyboard:
            buttons.append([b.text for b in row])

    height = 96 + len(rows) * LINE + PAD * 2 + len(buttons) * 56 + 40
    img = Image.new("RGB", (W, height), BG)
    d = ImageDraw.Draw(img)

    # шапка чата
    d.rectangle([0, 0, W, 72], fill=(32, 43, 54))
    d.text((PAD, 20), header, font=bold, fill=TEXT)
    d.text((PAD, 46), "бот", font=small, fill=MUTED)

    top = 88
    bubble_h = len(rows) * LINE + PAD
    d.rounded_rectangle([PAD, top, W - PAD, top + bubble_h], radius=16, fill=BUBBLE)

    y = top + PAD // 2
    for row in rows:
        x = PAD * 2
        if row and row[0].get("quote"):
            d.rounded_rectangle([PAD + 10, y - 2, PAD + 13, y + LINE - 6], radius=2, fill=QUOTE_BAR)
            x = PAD * 2 + 8
        for run in row:
            if "emoji" in run:
                em = emoji_image(run["emoji"], SIZE)
                if em is not None:
                    img.paste(em, (int(x), int(y + 2)), em)
                x += run["w"]
                continue
            f = bold if run["bold"] else font
            color = MUTED if run["italic"] or run["quote"] else TEXT
            d.text((x, y), run["text"], font=f, fill=color)
            x += run["w"]
        y += LINE

    y = top + bubble_h + 12
    for row in buttons:
        cell = (W - PAD * 2 - 8 * (len(row) - 1)) // len(row)
        x = PAD
        for label in row:
            d.rounded_rectangle([x, y, x + cell, y + 46], radius=10, fill=BTN)
            parts = [p for p in EMOJI_RE.split(label) if p]
            tw = sum(SIZE + 4 if EMOJI_RE.fullmatch(p) else font.getlength(p) for p in parts)
            tx = x + (cell - tw) / 2
            for p in parts:
                if EMOJI_RE.fullmatch(p):
                    em = emoji_image(p, SIZE)
                    if em is not None:
                        img.paste(em, (int(tx), int(y + 12)), em)
                    tx += SIZE + 4
                else:
                    d.text((tx, y + 12), p, font=font, fill=BTN_TEXT)
                    tx += font.getlength(p)
            x += cell + 8
        y += 56

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    img.save(path)
    return path


async def main() -> None:
    await init_db()
    async with SessionLocal() as s:
        for w in (1, 2, 3):
            await services.set_week_open(s, w, w == 1)
        team = await services.create_team(s, None, "Добряки", "🔥")
        user = await services.get_or_create_user(s, 1, "ivan")
        await services.register_user(s, user, "Иван Петров", "Отдел продаж", "Ташкент", phone="+998901234567")
        await services.approve_user(s, user, 1357560299)
        await services.join_team(s, user, team.id)
        await s.commit()
        user = await services.get_user(s, 1)
        tasks = await services.list_tasks(s, 1)
        rows = await services.leaderboard(s)

        shots = [
            ("01_welcome", texts.welcome(user), kb.start_kb()),
            ("02_reg_name", texts.registration_step("full_name", {}), kb.reg_kb("full_name")),
            ("03_reg_phone", texts.registration_step("phone", {"full_name": "Иван Петров"}), kb.reg_kb("phone")),
            ("04_reg_team", texts.registration_step("team", {"full_name": "Иван Петров", "phone": "+998901234567"}),
             kb.reg_team_kb(rows)),
            ("05_pending", texts.pending_status(user), kb.pending_kb(user)),
        ]
        for name, body, markup in shots:
            render(name, body, markup)

        ws = dict(await services.week_stats_for_user(s, user.id, 1))
        ws.update(total_teams=len(rows), approved_total=0, my_rank=1, total_users=12)
        render("06_menu", texts.main_menu(user, 0, 0, 1, ws), kb.main_menu_kb(user))
        render("07_week", texts.tasks_list(1, tasks, {}), kb.week_tabs_kb(1, tasks, {}))
        task = tasks[0]
        render("08_task", texts.task_card(task, None, 1), kb.task_card_kb(task, None, True, user))

        sub = await services.start_submission(s, user, task, None)
        render("09_step1", texts.submission_step(sub, 0),
               kb.submission_step_kb(sub, 0, len(services.submission_steps(sub)), False))
        await services.add_file(s, sub, {"type": "photo", "file_id": "f", "name": None}, step=0)
        await s.commit()
        sub = await services.get_submission(s, sub.id)
        render("10_step2", texts.submission_step(sub, 1),
               kb.submission_step_kb(sub, 1, len(services.submission_steps(sub)), False))
        await services.save_step_answer(s, sub, 1, "Поблагодарил Марину за помощь с отчётом")
        await s.commit()
        sub = await services.get_submission(s, sub.id)
        steps = services.submission_steps(sub)
        done = [services.step_done(sub, i) for i in range(len(steps))]
        render("11_ready", texts.submission_review(sub), kb.submission_review_kb(sub, steps, done, True))
        render("12_rules", texts.RULES, kb.rules_kb(True))
        render("13_rating", texts.leaderboard_text(rows), kb.back_kb())
        render("14_help", texts.help_screen(), kb.help_kb())

    print(f"Готово: {len(list(OUT.glob('*.png')))} экранов в {OUT}")


asyncio.run(main())
