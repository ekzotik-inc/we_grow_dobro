from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserStatus(str, enum.Enum):
    new = "new"  # pressed /start, has not filled the form
    pending = "pending"  # form filled, waiting for P&C approval in the registration channel
    registered = "registered"  # approved participant
    rejected = "rejected"  # application declined by P&C
    disqualified = "disqualified"


class SubmissionStatus(str, enum.Enum):
    draft = "draft"  # being collected (photos / note)
    pending = "pending"  # sent for review
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(160))
    department: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.new)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    rules_accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    disqualified_reason: Mapped[str | None] = mapped_column(Text)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    reg_message_id: Mapped[int | None] = mapped_column(Integer)  # card in the registration channel
    moderated_by: Mapped[int | None] = mapped_column(BigInteger)  # tg_id of the P&C who decided
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), index=True)
    menu_message_id: Mapped[int | None] = mapped_column(Integer)  # last "anchor" message we edit in place
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    team: Mapped["Team | None"] = relationship(back_populates="members", foreign_keys=[team_id])
    submissions: Mapped[list["Submission"]] = relationship(back_populates="user")

    @property
    def display_name(self) -> str:
        return self.full_name or (f"@{self.username}" if self.username else f"id{self.tg_id}")

    @property
    def is_active_participant(self) -> bool:
        return self.status == UserStatus.registered


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    emoji: Mapped[str] = mapped_column(String(8), default="🌱")
    captain_id: Mapped[int | None] = mapped_column(Integer)  # users.id
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    members: Mapped[list[User]] = relationship(back_populates="team", foreign_keys=[User.team_id])


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[int] = mapped_column(Integer, unique=True)  # № from the spreadsheet
    week: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200))
    emoji: Mapped[str] = mapped_column(String(8), default="✅")
    description: Mapped[str] = mapped_column(Text)
    conditions: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer)  # max points (for option tasks — the best option)
    min_photos: Mapped[int] = mapped_column(Integer, default=1)
    note_required: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    options: Mapped[list["TaskOption"]] = relationship(back_populates="task", order_by="TaskOption.id")

    @property
    def has_options(self) -> bool:
        return bool(self.options)

    @property
    def points_label(self) -> str:
        if self.options:
            return "/".join(str(o.points) for o in self.options)
        return str(self.points)


class TaskOption(Base):
    __tablename__ = "task_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    points: Mapped[int] = mapped_column(Integer)
    min_photos: Mapped[int] = mapped_column(Integer, default=1)
    conditions: Mapped[str] = mapped_column(Text)

    task: Mapped[Task] = relationship(back_populates="options")


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("user_id", "task_id", name="uq_submission_user_task"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    option_id: Mapped[int | None] = mapped_column(ForeignKey("task_options.id", ondelete="SET NULL"))
    week: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[SubmissionStatus] = mapped_column(Enum(SubmissionStatus), default=SubmissionStatus.draft, index=True)
    note: Mapped[str | None] = mapped_column(Text)
    files: Mapped[list] = mapped_column(JSON, default=list)  # [{"type": "photo"|"document"|"video", "file_id": str, "name": str|None}]
    points_awarded: Mapped[int] = mapped_column(Integer, default=0)
    review_comment: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger)  # tg_id of the P&C reviewer
    channel_message_id: Mapped[int | None] = mapped_column(Integer)  # card in the results channel
    channel_media_ids: Mapped[list] = mapped_column(JSON, default=list)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="submissions")
    task: Mapped[Task] = relationship()
    option: Mapped[TaskOption | None] = relationship()

    @property
    def target_points(self) -> int:
        return self.option.points if self.option else self.task.points

    @property
    def required_photos(self) -> int:
        return self.option.min_photos if self.option else self.task.min_photos


class PointsLog(Base):
    """Audit trail of every points change (approval, manual adjustment, disqualification)."""

    __tablename__ = "points_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("submissions.id", ondelete="SET NULL"))
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))
    actor_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AppSetting(Base):
    """Key-value settings editable from /admin at runtime (channels, feature switches)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(60))  # week_announce:1, reminder:1, manual:<segment>
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    recipients: Mapped[int] = mapped_column(Integer, default=0)
