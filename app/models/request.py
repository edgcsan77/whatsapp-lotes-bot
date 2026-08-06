from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id"),
        nullable=False,
        index=True,
    )

    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("providers.id"),
        nullable=True,
        index=True,
    )

    whatsapp_message_id: Mapped[str] = mapped_column(
        String(200),
        unique=True,
        nullable=False,
        index=True,
    )

    source_jid: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        index=True,
    )

    sender_jid: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )

    sender_name: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )

    original_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    input_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    rfc: Mapped[str] = mapped_column(
        String(13),
        nullable=False,
        index=True,
    )

    original_curp: Mapped[str | None] = mapped_column(
        String(18),
        nullable=True,
        index=True,
    )

    detected_name: Mapped[str | None] = mapped_column(
        String(240),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="RECEIVED",
        index=True,
    )

    provider_result: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    idcif: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    result_code: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    sale_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    sent_to_provider_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    provider_replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
