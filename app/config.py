from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


def _parse_ids(raw: str) -> set[int]:
    out: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def _parse_date(raw: str, default: date) -> date:
    raw = (raw or "").strip()
    return date.fromisoformat(raw) if raw else default


@dataclass
class Week:
    number: int
    start: date
    end: date  # inclusive

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    @property
    def label(self) -> str:
        return f"{self.start.day:02d}.{self.start.month:02d} – {self.end.day:02d}.{self.end.month:02d}"


@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: set[int] = field(default_factory=lambda: _parse_ids(os.getenv("ADMIN_IDS", "")))
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/marathon.db")
    webapp_url: str = os.getenv("WEBAPP_URL", "").rstrip("/")
    # Channels for moderation. Optional here: both can be bound at runtime from /admin.
    reg_channel_id: str = os.getenv("REG_CHANNEL_ID", "").strip()
    results_channel_id: str = os.getenv("RESULTS_CHANNEL_ID", "").strip()
    web_host: str = os.getenv("WEB_HOST", "0.0.0.0")
    web_port: int = int(os.getenv("WEB_PORT", "8080"))
    tz_name: str = os.getenv("TZ", "Asia/Almaty")
    team_size: int = int(os.getenv("TEAM_SIZE", "5"))
    marathon_title: str = os.getenv("MARATHON_TITLE", "Марафон добрых дел «We Grow Dobro»")
    pc_contact: str = os.getenv("PC_CONTACT", "сотрудник P&C")
    announce_hour: int = int(os.getenv("ANNOUNCE_HOUR", "10"))
    reminder_hour: int = int(os.getenv("REMINDER_HOUR", "12"))
    # If set, the week is forced (useful for testing before the marathon starts). 0 = auto.
    force_week: int = int(os.getenv("FORCE_WEEK", "0"))

    weeks: list[Week] = field(default_factory=list)
    _tz: object = None

    def __post_init__(self) -> None:
        self.weeks = [
            Week(1, _parse_date(os.getenv("WEEK1_START"), date(2026, 9, 9)), _parse_date(os.getenv("WEEK1_END"), date(2026, 9, 15))),
            Week(2, _parse_date(os.getenv("WEEK2_START"), date(2026, 9, 16)), _parse_date(os.getenv("WEEK2_END"), date(2026, 9, 22))),
            Week(3, _parse_date(os.getenv("WEEK3_START"), date(2026, 9, 23)), _parse_date(os.getenv("WEEK3_END"), date(2026, 9, 30))),
        ]

    @property
    def tz(self):
        """Timezone, resolved once. Windows ships no system tz database, so a missing `tzdata`
        package would otherwise crash every menu render — fall back to UTC with a loud warning."""
        if self._tz is None:
            try:
                self._tz = ZoneInfo(self.tz_name)
            except (ZoneInfoNotFoundError, ValueError):
                log.warning(
                    "Не найдена таймзона %r — работаю по UTC. Установите пакет tzdata "
                    "(pip install -r requirements.txt) или укажите корректный TZ в .env.",
                    self.tz_name,
                )
                self._tz = timezone.utc
        return self._tz

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    def current_week(self) -> Week | None:
        """Week whose date range contains today, or None outside the marathon."""
        if self.force_week:
            return self.week(self.force_week)
        today = self.today()
        for w in self.weeks:
            if w.contains(today):
                return w
        return None

    def week(self, n: int) -> Week | None:
        for w in self.weeks:
            if w.number == n:
                return w
        return None

    def marathon_status(self) -> str:
        """'before' | 'active' | 'after'"""
        if self.force_week:
            return "active"
        today = self.today()
        if today < self.weeks[0].start:
            return "before"
        if today > self.weeks[-1].end:
            return "after"
        return "active"

    def week_deadline(self, n: int) -> datetime:
        w = self.week(n)
        assert w is not None
        return datetime.combine(w.end, time(23, 59, 59), tzinfo=self.tz)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


settings = Settings()
