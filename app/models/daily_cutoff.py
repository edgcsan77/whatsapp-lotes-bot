from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DailyCutoff(Base):
    __tablename__ = "daily_cutoffs"

    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "period_start",
            "period_end",
            name=(
                "uq_daily_cutoffs_"
                "client_period"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id"),
        nullable=False,
        index=True,
    )

    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    total_requests: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    idcif_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    generic_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    direct_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    delivered_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    pending_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    rfc_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    curp_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="CREATED",
        index=True,
    )

    whatsapp_message_id: Mapped[
        str | None
    ] = mapped_column(
        String(200),
        nullable=True,
    )

    error_message: Mapped[
        str | None
    ] = mapped_column(
        String(1000),
        nullable=True,
    )

    sent_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
