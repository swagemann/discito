from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Child(TimestampMixin, Base):
    __tablename__ = "child"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    avatar: Mapped[str] = mapped_column(String(16), nullable=False, default="🦉")
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="sky")
    # Sections unlocked per chain step.
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tts_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.9)
    # Keep the text on screen while recording (read along). It always shows before
    # recording; attempts made with it up are marked as helped.
    show_text: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
