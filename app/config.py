from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


# Query parameters the async driver actually understands. Everything else in a provider's URL is
# libpq-style (sslmode, channel_binding, sslrootcert, options, …): SQLAlchemy passes the whole query
# straight to asyncpg.connect(), which then dies with
# "TypeError: connect() got an unexpected keyword argument '<name>'".
_ASYNCPG_URL_PARAMS = frozenset(
    {
        "prepared_statement_cache_size",  # read by SQLAlchemy's asyncpg dialect
        "statement_cache_size",
        "command_timeout",
        "timeout",
        "target_session_attrs",
        "max_cached_statement_lifetime",
        "max_cacheable_statement_size",
    }
)


def _normalize_db_url(raw: str) -> str:
    """Make a hosting provider's DATABASE_URL usable by SQLAlchemy's async engine.

    Render and Neon hand out `postgres://…` or `postgresql://…` with libpq parameters attached.
    Two things have to happen: switch to the `+asyncpg` driver, and drop every parameter asyncpg
    cannot take. Dropping `sslmode`/`channel_binding` does not disable encryption — asyncpg
    negotiates TLS on its own by default.
    """
    url = (raw or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    if "+asyncpg" in url:
        base, _, query = url.partition("?")
        kept = [p for p in query.split("&") if p.split("=", 1)[0] in _ASYNCPG_URL_PARAMS]
        # A "-pooler" host is PgBouncer in transaction mode: connections are shared between
        # transactions, so asyncpg's prepared statements break with DuplicatePreparedStatementError.
        # SQLAlchemy's asyncpg dialect reads this setting from the URL query, not from create_engine().
        if "-pooler." in base and not any(p.startswith("prepared_statement_cache_size=") for p in kept):
            kept.append("prepared_statement_cache_size=0")
        url = base + ("?" + "&".join(kept) if kept else "")
    return url


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
    # Владелец бота и сотрудники P&C. Значения по умолчанию — боевые id проекта: даже если
    # переменные окружения потеряются, панель не откроется всем подряд.
    admin_ids: set[int] = field(default_factory=lambda: _parse_ids(os.getenv("ADMIN_IDS", "1357560299")))
    # P&C staff. Participants' questions and team requests go only here, never to the owner.
    pc_ids: set[int] = field(default_factory=lambda: _parse_ids(os.getenv("PC_IDS", "101727102")))
    database_url: str = _normalize_db_url(os.getenv("DATABASE_URL", "")) or "sqlite+aiosqlite:///./data/marathon.db"
    # Channels for moderation. Optional here: both can be bound at runtime from /admin.
    reg_channel_id: str = os.getenv("REG_CHANNEL_ID", "").strip()
    results_channel_id: str = os.getenv("RESULTS_CHANNEL_ID", "").strip()
    web_host: str = os.getenv("WEB_HOST", "0.0.0.0")
    # PORT is what Render (and most PaaS) assigns; binding anything else makes the host declare the service dead.
    web_port: int = int(os.getenv("PORT") or os.getenv("WEB_PORT") or "8080")
    tz_name: str = os.getenv("TZ", "Asia/Tashkent")
    team_size: int = int(os.getenv("TEAM_SIZE", "5"))
    marathon_title: str = os.getenv("MARATHON_TITLE", "Марафон добрых дел «We Grow Dobro»")
    pc_contact: str = os.getenv("PC_CONTACT", "сотрудник P&C")
    # Telegram-логин сотрудника P&C: участник получает прямую ссылку, чтобы не искать контакт.
    pc_username: str = os.getenv("PC_USERNAME", "DaryaPMI").strip().lstrip("@")
    # Код страны для номеров, набранных без него: «90 123 45 67» -> «+998 90 123 45 67».
    phone_country_code: str = os.getenv("PHONE_COUNTRY_CODE", "998").strip().lstrip("+")
    announce_hour: int = int(os.getenv("ANNOUNCE_HOUR", "10"))
    reminder_hour: int = int(os.getenv("REMINDER_HOUR", "12"))
    # Как часто трогать базу, чтобы не засыпала. 0 — не трогать: на бесплатном тарифе Neon
    # круглосуточная активность съедает месячную квоту примерно за две недели.
    db_keepalive_minutes: int = int(os.getenv("DB_KEEPALIVE_MINUTES", "4"))
    motivation_hour: int = int(os.getenv("MOTIVATION_HOUR", "11"))   # nudge every other day
    top_hour: int = int(os.getenv("TOP_HOUR", "19"))                 # standings, Wed and Sun
    # Еженедельная мотивационная рассылка всем участникам: день недели (0 — понедельник) и час.
    weekly_weekday: int = int(os.getenv("WEEKLY_WEEKDAY", "0"))
    weekly_hour: int = int(os.getenv("WEEKLY_HOUR", "10"))
    # Личные подсказки каждому участнику по его состоянию и обучающая серия «что и куда».
    nudge_hour: int = int(os.getenv("NUDGE_HOUR", "16"))
    howto_hour: int = int(os.getenv("HOWTO_HOUR", "12"))
    # If set, the week is forced (useful for testing before the marathon starts). 0 = auto.
    # Public URL of this service; when set, the bot pings its own /api/health so a free host does not sleep it.
    external_url: str = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    # Date the hosted database is deleted (free Render Postgres lives 30 days). Empty = no deadline.
    db_expires_at: str = os.getenv("DB_EXPIRES_AT", "").strip()
    # Custom (premium) emoji work only while the bot owner has Telegram Premium; 0 turns them off.
    premium_emoji: bool = os.getenv("PREMIUM_EMOJI", "1").strip() not in ("0", "false", "no", "")
    # Name the bot speaks under in its quoted lines.
    voice_name: str = os.getenv("VOICE_NAME", "Добрик").strip()

    weeks: list[Week] = field(default_factory=list)
    _tz: object = None

    def __post_init__(self) -> None:
        self.weeks = [
            Week(1, _parse_date(os.getenv("WEEK1_START"), date(2026, 9, 10)), _parse_date(os.getenv("WEEK1_END"), date(2026, 9, 16))),
            Week(2, _parse_date(os.getenv("WEEK2_START"), date(2026, 9, 17)), _parse_date(os.getenv("WEEK2_END"), date(2026, 9, 23))),
            Week(3, _parse_date(os.getenv("WEEK3_START"), date(2026, 9, 24)), _parse_date(os.getenv("WEEK3_END"), date(2026, 9, 30))),
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

    def week(self, n: int) -> Week | None:
        for w in self.weeks:
            if w.number == n:
                return w
        return None

    def week_deadline(self, n: int) -> datetime:
        w = self.week(n)
        assert w is not None
        return datetime.combine(w.end, time(23, 59, 59), tzinfo=self.tz)

    def is_admin(self, user_id: int) -> bool:
        """Full access to the panel: the owner and every P&C member."""
        return user_id in self.admin_ids or user_id in self.pc_ids

    def is_pc(self, user_id: int) -> bool:
        return user_id in self.pc_ids

    @property
    def db_expiry_date(self) -> date | None:
        try:
            return date.fromisoformat(self.db_expires_at) if self.db_expires_at else None
        except ValueError:
            log.warning("DB_EXPIRES_AT=%r — не дата в формате ГГГГ-ММ-ДД, игнорирую.", self.db_expires_at)
            return None

    def db_days_left(self) -> int | None:
        d = self.db_expiry_date
        return (d - self.today()).days if d else None


settings = Settings()
