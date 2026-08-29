from decimal import Decimal

from sqlalchemy import (
    create_engine,
    select,
)
from sqlalchemy.orm import Session

from app.database import Base
from app.models.client import Client
from app.models.provider import Provider
from app.models.request import Request
from app.services.request_service import (
    IncomingWhatsAppMessage,
    register_client_message,
)


CURP = "CASE020722HTSRNDA8"
RFC = "VALA830403RA8"


def create_db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )

    Base.metadata.create_all(
        engine
    )

    return Session(engine)


def create_client(
    db: Session,
    *,
    generic_enabled: bool = False,
    idcif_pdf_enabled: bool = False,
) -> Client:
    provider = Provider(
        name="Proveedor core",
        whatsapp_jid=(
            "120363000000001001"
            "@g.us"
        ),
        evolution_instance="test",
        priority=1,
        timeout_minutes=60,
        active=True,
    )

    db.add(provider)
    db.flush()

    client = Client(
        name="Cliente core",
        source_type="group",
        whatsapp_jid=(
            "120363000000001002"
            "@g.us"
        ),
        default_provider_id=
            provider.id,
        price_per_request=
            Decimal("2.00"),
        generic_price_per_request=
            Decimal("1.50"),
        generic_pdf_enabled=
            generic_enabled,
        idcif_pdf_enabled=
            idcif_pdf_enabled,
        batch_enabled=True,
        batch_interval_minutes=10,
        batch_max_items=1000,
        daily_cutoff_enabled=True,
        daily_cutoff_time="23:30",
        timezone="America/Monterrey",
        active=True,
    )

    db.add(client)
    db.commit()
    db.refresh(client)

    return client


def test_normal_request_preserves_text_mode() -> None:
    db = create_db()

    try:
        client = create_client(db)

        result = register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="NORMAL-TEXT",
                source_jid=
                    client.whatsapp_jid,
                sender_jid=None,
                sender_name=None,
                text=RFC,
            ),
        )

        assert result.created_count == 1

        request_item = db.scalar(
            select(Request)
        )

        assert request_item is not None
        assert request_item.service_type == (
            "RFC_IDCIF"
        )
        assert request_item.delivery_format == (
            "TEXT"
        )
        assert request_item.status == (
            "PENDING_BATCH"
        )

    finally:
        db.close()


def test_normal_request_snapshots_pdf_permission() -> None:
    db = create_db()

    try:
        client = create_client(
            db,
            idcif_pdf_enabled=True,
        )

        result = register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="NORMAL-PDF",
                source_jid=
                    client.whatsapp_jid,
                sender_jid=None,
                sender_name=None,
                text=RFC,
            ),
        )

        assert result.created_count == 1

        request_item = db.scalar(
            select(Request)
        )

        assert request_item is not None
        assert request_item.service_type == (
            "RFC_IDCIF"
        )
        assert request_item.delivery_format == (
            "PDF"
        )
        assert request_item.lookup_route == (
            "DIRECT_RFC_IDCIF"
        )
        assert request_item.status == (
            "PENDING_BATCH"
        )

    finally:
        db.close()


def test_disabled_generic_is_not_created() -> None:
    db = create_db()

    try:
        client = create_client(
            db,
            generic_enabled=False,
        )

        result = register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="GEN-DISABLED",
                source_jid=
                    client.whatsapp_jid,
                sender_jid=None,
                sender_name=None,
                text=f"{RFC}-G",
            ),
        )

        assert result.created_count == 0

        assert (
            result
            .generic_not_enabled_identifiers
            == [f"{RFC}-G"]
        )

        assert list(
            db.scalars(
                select(Request)
            )
        ) == []

    finally:
        db.close()


def test_curp_generic_never_uses_provider() -> None:
    db = create_db()

    try:
        client = create_client(
            db,
            generic_enabled=True,
        )

        result = register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="GEN-CURP",
                source_jid=
                    client.whatsapp_jid,
                sender_jid=None,
                sender_name=None,
                text=f"{CURP} - G",
            ),
        )

        assert result.created_count == 1

        request_item = db.scalar(
            select(Request)
        )

        assert request_item is not None
        assert request_item.provider_id is None
        assert request_item.service_type == (
            "RFC_GENERIC"
        )
        assert request_item.delivery_format == (
            "PDF"
        )
        assert request_item.status == (
            "PENDING_PDF"
        )
        assert request_item.lookup_route == (
            "CURP_NL_SEPOMEX_NO_CHECKID"
        )
        assert request_item.original_curp == CURP
        assert request_item.rfc is None
        assert request_item.sale_price == (
            Decimal("1.50")
        )

    finally:
        db.close()


def test_normal_and_generic_same_rfc_coexist() -> None:
    db = create_db()

    try:
        client = create_client(
            db,
            generic_enabled=True,
        )

        result = register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="MIXED-SAME-RFC",
                source_jid=
                    client.whatsapp_jid,
                sender_jid=None,
                sender_name=None,
                text=(
                    f"{RFC}\n"
                    f"{RFC} - G"
                ),
            ),
        )

        assert result.created_count == 2

        requests = list(
            db.scalars(
                select(Request)
                .order_by(Request.id)
            )
        )

        assert {
            request.service_type
            for request in requests
        } == {
            "RFC_IDCIF",
            "RFC_GENERIC",
        }

    finally:
        db.close()
