"""Centralized Unix UID allocator. PK enforces no-reuse."""
from __future__ import annotations
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, select, func
from sqlalchemy.orm import Mapped, mapped_column, MappedAsDataclass

from not_dot_net.backend.db import Base, session_scope


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
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=None,
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


async def allocate_uid(user_id: uuid.UUID, sam_account: str) -> int:
    """Allocate the smallest free UID in the configured [uid_min, uid_max] range.

    Inserts a row marking the UID consumed; raises UidRangeExhausted if no free slot.
    Audit-logs the allocation with category='ad' action='allocate_uid'.
    """
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.audit import log_audit

    cfg = await ad_account_config.get()
    lo, hi = cfg.uid_min, cfg.uid_max

    async with session_scope() as session:
        rows = (await session.execute(
            select(UidAllocation.uid).where(
                UidAllocation.uid >= lo, UidAllocation.uid <= hi,
            ).order_by(UidAllocation.uid.asc())
        )).scalars().all()

        used = set(rows)
        chosen: int | None = None
        for n in range(lo, hi + 1):
            if n not in used:
                chosen = n
                break
        if chosen is None:
            raise UidRangeExhausted(f"No free UID in [{lo}, {hi}]")

        session.add(UidAllocation(
            uid=chosen, source="allocated",
            user_id=user_id, sam_account=sam_account,
        ))
        await session.commit()

    await log_audit(
        category="ad", action="allocate_uid",
        actor_id=None, target_id=str(user_id),
        detail=f"uid={chosen} sam={sam_account}",
    )
    return chosen
