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


class Passage(TimestampMixin, Base):
    __tablename__ = "passage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    reference: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    type: Mapped[str] = mapped_column(String(10), nullable=False, default="poem")  # verse | poem
    text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_unit: Mapped[str] = mapped_column(String(10), nullable=False, default="line")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Defaults copied onto new assignments; each assignment can be tuned after.
    pass_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    k_required: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    sections: Mapped[list["Section"]] = relationship(
        back_populates="passage", order_by="Section.ordinal", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["PassageAssignment"]] = relationship(
        back_populates="passage", cascade="all, delete-orphan"
    )


class Section(Base):
    __tablename__ = "section"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    passage_id: Mapped[int] = mapped_column(
        ForeignKey("passage.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-based
    text: Mapped[str] = mapped_column(Text, nullable=False)

    passage: Mapped[Passage] = relationship(back_populates="sections")


class PassageAssignment(TimestampMixin, Base):
    __tablename__ = "passage_assignment"
    __table_args__ = (UniqueConstraint("passage_id", "child_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    passage_id: Mapped[int] = mapped_column(
        ForeignKey("passage.id", ondelete="CASCADE"), nullable=False, index=True
    )
    child_id: Mapped[int] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # not_started | partial | full | passed — mirrored from `state` for listing.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_started")
    current_section: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clean_full_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    k_required: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # Serialized app.core.chain.ChainState; null until the child first opens it.
    state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    passed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    passage: Mapped[Passage] = relationship(back_populates="assignments")
    child: Mapped[Child] = relationship()


class ReciteAttempt(Base):
    __tablename__ = "recite_attempt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("passage_assignment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # partial:<n> | full | drill:<line|transition>:<section>
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    section_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    transcript: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # [{section_id, word, expected, heard}] — word is the display-word index in the section.
    missed_words: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verdict: Mapped[bool] = mapped_column(Boolean, nullable=False)
    override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    peeked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    audio_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    stt_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Assignment state + touched line stats before this attempt, so a parent
    # override of the latest attempt can replay it with the corrected verdict.
    state_before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    assignment: Mapped[PassageAssignment] = relationship()

    @property
    def effective_verdict(self) -> bool:
        return self.verdict if self.override is None else self.override


class LineStat(Base):
    __tablename__ = "line_stat"
    __table_args__ = (UniqueConstraint("assignment_id", "section_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("passage_assignment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("section.id", ondelete="CASCADE"), nullable=False
    )
    struggle_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    consecutive_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    miss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # {"<display word index>": misses} — drives the underline on often-missed words.
    word_misses: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    # Score before the latest attempt, for the trouble-spots trend arrow.
    prev_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    section: Mapped[Section] = relationship()
