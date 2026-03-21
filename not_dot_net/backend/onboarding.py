import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from not_dot_net.backend.db import Base


class OnboardingRequest(Base):
    __tablename__ = "onboarding_request"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    person_name: Mapped[str] = mapped_column(String(255))
    person_email: Mapped[str] = mapped_column(String(255))
    role_status: Mapped[str] = mapped_column(String(100))
    team: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[date]
    note: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
