from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )

    source_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="group",
    )

    whatsapp_jid: Mapped[str] = mapped_column(
        String(160),
        unique=True,
        nullable=False,
        index=True,
    )

    contact_name: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    default_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("providers.id"),
        nullable=True,
        index=True,
    )

    price_per_request: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
    )

    batch_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    batch_interval_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=15,
    )

    batch_max_items: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=50,
    )

    daily_cutoff_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    daily_cutoff_time: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
        default="23:30",
    )

    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="America/Monterrey",
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
