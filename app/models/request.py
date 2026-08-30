from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Request(Base):
    __tablename__ = "requests"

    __table_args__ = (
        UniqueConstraint(
            "whatsapp_message_id",
            "identifier_key",
            "service_type",
            name=(
                "uq_requests_message_"
                "identifier_service"
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

    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("providers.id"),
        nullable=True,
        index=True,
    )

    whatsapp_message_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )

    # RFC o CURP original que identifica de manera única
    # una solicitud dentro del mismo mensaje de WhatsApp.
    identifier_key: Mapped[str] = mapped_column(
        String(18),
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

    # RFC_IDCIF:
    # flujo de localización actual.
    #
    # RFC_GENERIC:
    # constancia solicitada mediante -G.
    #
    # CONSTANCIA_DIRECTA:
    # cliente proporciona RFC + IDCIF; no localiza.
    service_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="RFC_IDCIF",
        index=True,
    )

    # TEXT para el flujo actual.
    # PDF para genéricos o clientes con
    # constancia automática por IDCIF.
    delivery_format: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="TEXT",
    )

    # CURP_NL_SEPOMEX_NO_CHECKID
    # RFC_CHECKID
    # DIRECT_RFC_IDCIF
    lookup_route: Mapped[
        str | None
    ] = mapped_column(
        String(64),
        nullable=True,
    )

    # Será NULL cuando la entrada original sea una CURP
    # pendiente de consulta y conversión con Moffin.
    rfc: Mapped[str | None] = mapped_column(
        String(13),
        nullable=True,
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

    pdf_status: Mapped[
        str | None
    ] = mapped_column(
        String(30),
        nullable=True,
        index=True,
    )

    pdf_url: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    pdf_filename: Mapped[
        str | None
    ] = mapped_column(
        String(255),
        nullable=True,
    )

    pdf_error: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    pdf_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    pdf_started_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    pdf_next_attempt_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    pdf_delivered_message_id: Mapped[
        str | None
    ] = mapped_column(
        String(200),
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
        default=lambda: datetime.now(UTC),
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
