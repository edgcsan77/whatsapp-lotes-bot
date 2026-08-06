from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id"),
        nullable=True,
        index=True,
    )

    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="OPEN",
        index=True,
    )

    request_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    outbound_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    provider_message_id: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class BatchItem(Base):
    __tablename__ = "batch_items"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id"),
        nullable=False,
        index=True,
    )

    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
