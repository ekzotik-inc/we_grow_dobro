"""Premium (custom) emoji from the @d_code set, with a safe fallback to plain emoji.

Telegram lets a bot send custom emoji only in private/group/supergroup chats and only while the
bot's owner keeps a Telegram Premium subscription. Channels never accept them, and the subscription
can lapse — so every emoji is stored together with the plain character it degrades to, and the
whole feature can be switched off at runtime without touching the texts.
"""
from __future__ import annotations

import logging

from .config import settings

log = logging.getLogger(__name__)

# name -> (custom emoji id, plain fallback shown when custom emoji are unavailable)
CATALOGUE: dict[str, tuple[str, str]] = {
    "heart":     ("5350663853560581569", "❤️"),
    "hug":       ("5350444500990839137", "🤗"),
    "bolt":      ("5348386649015339472", "⚡️"),
    "rocket":    ("5348324105701574477", "🚀"),
    "medal":     ("5386721336666119075", "🥇"),
    "like":      ("5389101006246137986", "👍"),
    "salute":    ("5348339911181222919", "🫡"),
    "blocked":   ("5348435100541404792", "⛔️"),
    "thinking":  ("5348236917865465879", "🤔"),
    "shield":    ("5348230282140993341", "🛡"),
    "brain":     ("5348302871383263062", "🧠"),
    "coffee":    ("5348414471813483004", "☕️"),
    "money":     ("5348568115678562055", "💸"),
    "coin":      ("5388776581596467392", "💲"),
    "cat":       ("5348129784201233117", "😻"),
    "kitten":    ("5348102210511193735", "🐈"),
    "cool":      ("5348548466203183571", "😎"),
    "star_eyes": ("5350331204048537405", "🤩"),
    "smile":     ("5348191962442779588", "😄"),
    "grin":      ("5348542577803019512", "😀"),
    "laugh":     ("5348447328313297818", "😆"),
    "kiss":      ("5348375542229911778", "😘"),
    "shh":       ("5348182702493288203", "🤫"),
    "shrug":     ("5350411476987299674", "🤷‍♂️"),
    "phone":     ("5348175551372740831", "📱"),
    "laptop":    ("5348048252837055373", "💻"),
    "apple":     ("5348335294091382096", "🍏"),
    "car":       ("5348212496681421622", "🚘"),
    "ufo":       ("5350513460985743273", "🛸"),
    "headphones": ("5348471165381788971", "🎧"),
}

# Turned off automatically if Telegram ever rejects a custom emoji (expired Premium, for example),
# so a subscription lapse degrades the look instead of breaking every message.
_enabled: bool | None = None


def enabled() -> bool:
    global _enabled
    if _enabled is None:
        _enabled = settings.premium_emoji
    return _enabled


def disable(reason: str = "") -> None:
    """Fall back to plain emoji for the rest of the process."""
    global _enabled
    if _enabled is not False:
        _enabled = False
        log.warning("Премиум-эмодзи отключены, перехожу на обычные. %s", reason)


def plain(name: str) -> str:
    """The plain character for this name — used in channels and inline buttons, where Telegram
    ignores custom emoji entities."""
    return CATALOGUE[name][1] if name in CATALOGUE else ""


def e(name: str) -> str:
    """Render an emoji for message text: premium when available, plain otherwise."""
    if name not in CATALOGUE:
        return ""
    emoji_id, fallback = CATALOGUE[name]
    if not enabled():
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def strip(text: str) -> str:
    """Replace every <tg-emoji> tag with its plain character — the retry path when Telegram
    refuses custom emoji, and the way channel cards are rendered."""
    import re

    return re.sub(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', r"\1", text)
