from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.models.child import Child


class SpellingList(TimestampMixin, Base):
    __tablename__ = "spelling_list"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pass_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    carry_forward: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    words: Mapped[list["SpellingWord"]] = relationship(
        back_populates="spelling_list",
        foreign_keys="SpellingWord.list_id",
        order_by="SpellingWord.ordinal",
        cascade="all, delete-orphan",
    )
    assignments: Mapped[list["ListAssignment"]] = relationship(
        back_populates="spelling_list", cascade="all, delete-orphan"
    )


class SpellingWord(Base):
    __tablename__ = "spelling_word"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_id: Mapped[int] = mapped_column(
        ForeignKey("spelling_list.id", ondelete="CASCADE"), nullable=False, index=True
    )
    word: Mapped[str] = mapped_column(String(80), nullable=False)
    sentence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    homophone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Carry-forward review words belong to one child's copy of the list.
    for_child_id: Mapped[int | None] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=True, index=True
    )
    carried_from_list_id: Mapped[int | None] = mapped_column(
        ForeignKey("spelling_list.id", ondelete="SET NULL"), nullable=True
    )

    spelling_list: Mapped[SpellingList] = relationship(
        back_populates="words", foreign_keys=[list_id]
    )


class ListAssignment(TimestampMixin, Base):
    __tablename__ = "list_assignment"
    __table_args__ = (UniqueConstraint("list_id", "child_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_id: Mapped[int] = mapped_column(
        ForeignKey("spelling_list.id", ondelete="CASCADE"), nullable=False, index=True
    )
    child_id: Mapped[int] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # not_started | practicing | passed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_started")
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Serialized app.core.spelling state (mode, queue = remaining words, misses, ...).
    state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    passed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    spelling_list: Mapped[SpellingList] = relationship(back_populates="assignments")
    child: Mapped[Child] = relationship()


class SpellingAttempt(Base):
    __tablename__ = "spelling_attempt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("list_assignment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    word_id: Mapped[int] = mapped_column(
        ForeignKey("spelling_word.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(10), nullable=False)  # learn | practice | test
    typed: Mapped[str] = mapped_column(Text, nullable=False, default="")
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    word: Mapped[SpellingWord] = relationship()


class WordStat(Base):
    __tablename__ = "word_stat"
    __table_args__ = (UniqueConstraint("assignment_id", "word_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("list_assignment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    word_id: Mapped[int] = mapped_column(
        ForeignKey("spelling_word.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="new")
    struggle_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    consecutive_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    miss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prev_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Set once this word has been copied into the child's next list.
    carried: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    word: Mapped[SpellingWord] = relationship()
