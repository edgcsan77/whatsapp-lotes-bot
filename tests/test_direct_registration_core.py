from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models.client import Client
from app.models.request import Request
from app.services.request_service import (
    IncomingWhatsAppMessage,
    register_client_message,
)
from app.services.acknowledgement_service import (
    build_request_acknowledgement,
)


RFC = "MATL941108QM3"
IDCIF = "16275230322"


def create_db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def create_client(
    db: Session,
    *,
    direct_enabled: bool,
) -> Client:
    client = Client(
        name="Cliente directa",
        source_type="group",
        whatsapp_jid="120363000000003001@g.us",
        default_provider_id=None,
        price_per_request=Decimal("2.00"),
        generic_price_per_request=Decimal("1.50"),
        direct_price_per_request=Decimal("3.25"),
        generic_pdf_enabled=False,
        direct_pdf_enabled=direct_enabled,
        idcif_pdf_enabled=False,
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


def test_direct_registration_never_uses_provider() -> None:
    db = create_db()
    try:
        client = create_client(db, direct_enabled=True)
        result = register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="DIRECT-1",
                source_jid=client.whatsapp_jid,
                sender_jid=None,
                sender_name=None,
                text=f"{RFC} {IDCIF}",
            ),
        )
        assert result.created_count == 1
        item = db.scalar(select(Request))
        assert item is not None
        assert item.provider_id is None
        assert item.service_type == "CONSTANCIA_DIRECTA"
        assert item.delivery_format == "PDF"
        assert item.lookup_route == "DIRECT_RFC_IDCIF"
        assert item.rfc == RFC
        assert item.idcif == IDCIF
        assert item.status == "PENDING_PDF"
        assert item.sale_price == Decimal("3.25")
    finally:
        db.close()


def test_direct_disabled_does_not_localize() -> None:
    db = create_db()
    try:
        client = create_client(db, direct_enabled=False)
        result = register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="DIRECT-OFF",
                source_jid=client.whatsapp_jid,
                sender_jid=None,
                sender_name=None,
                text=f"{RFC} {IDCIF}",
            ),
        )
        assert result.created_count == 0
        assert result.direct_not_enabled_identifiers == [
            f"{RFC} {IDCIF}"
        ]
        assert list(db.scalars(select(Request))) == []
    finally:
        db.close()


def test_normal_and_direct_same_rfc_can_coexist() -> None:
    db = create_db()
    try:
        client = create_client(db, direct_enabled=True)
        result = register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="MIX-DIRECT",
                source_jid=client.whatsapp_jid,
                sender_jid=None,
                sender_name=None,
                text=f"{RFC}\n{RFC} {IDCIF}",
            ),
        )
        assert result.created_count == 2
        service_types = {
            item.service_type
            for item in db.scalars(select(Request))
        }
        assert service_types == {
            "RFC_IDCIF",
            "CONSTANCIA_DIRECTA",
        }
    finally:
        db.close()


def test_direct_ack_has_own_label() -> None:
    text = build_request_acknowledgement(
        [f"DIRECT:{RFC}:{IDCIF}"]
    )
    assert "1 constancia directa" in text
    assert "pendiente de generación" in text
