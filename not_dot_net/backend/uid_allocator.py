"""Centralized Unix UID allocator. PK enforces no-reuse."""
from __future__ import annotations
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, select, func
from sqlalchemy.orm import Mapped, mapped_column, MappedAsDataclass

from not_dot_net.backend.db import Base


class UidRangeExhausted(Exception):
    """No free UID left in the configured range."""


class UidAllocation(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "uid_allocation"

    uid: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # 'allocated' | 'seeded_from_ad'
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None,
    )
    sam_account: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default_factory=lambda: datetime.now(timezone.utc),
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)


@dataclass(frozen=True)
class UidAllocationView:
    uid: int
    source: str
    user_id: uuid.UUID | None
    sam_account: str | None
    acquired_at: datetime
    note: str | None
