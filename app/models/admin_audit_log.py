from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    admin_user: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        index=True,
    )

    action: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        index=True,
    )

    entity_type: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        index=True,
    )

    entity_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    summary: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    details: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
