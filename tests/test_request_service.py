from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models.client import Client
from app.models.request import Request
from app.services.request_service import (
    ClientInactiveError,
    ClientNotFoundError,
    IncomingWhatsAppMessage,
    register_client_message,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
    )

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        yield session


def create_client(
    db: Session,
    *,
    jid: str = "120363000000000000@g.us",
    active: bool = True,
) -> Client:
    client = Client(
        name="Cliente de prueba",
        source_type="group",
        whatsapp_jid=jid,
        price_per_request=Decimal("25.00"),
        batch_enabled=True,
        batch_interval_minutes=15,
        batch_max_items=50,
        daily_cutoff_enabled=True,
        daily_cutoff_time="23:30",
        timezone="America/Monterrey",
        active=active,
    )

    db.add(client)
    db.commit()
    db.refresh(client)

    return client


def test_register_single_rfc(db: Session) -> None:
    client = create_client(db)

    result = register_client_message(
        db,
        IncomingWhatsAppMessage(
            message_id="MSG-001",
            source_jid=client.whatsapp_jid,
            sender_jid="5218990000000@s.whatsapp.net",
            sender_name="Edgar",
            text="VALA830403RA8",
        ),
    )

    assert result.created_count == 1
    assert result.duplicate_count == 0

    request = db.scalar(
        select(Request)
    )

    assert request is not None
    assert request.rfc == "VALA830403RA8"
    assert request.original_curp is None
    assert request.identifier_key == "VALA830403RA8"
    assert request.input_type == "RFC"
    assert request.status == "PENDING_BATCH"
    assert request.sale_price == Decimal("25.00")


def test_register_curp_pending_lookup(
    db: Session,
) -> None:
    client = create_client(db)

    result = register_client_message(
        db,
        IncomingWhatsAppMessage(
            message_id="MSG-002",
            source_jid=client.whatsapp_jid,
            sender_jid=None,
            sender_name=None,
            text="BEEJ760109HSLRSL03",
        ),
    )

    assert result.created_count == 1

    request = db.scalar(
        select(Request)
    )

    assert request is not None
    assert request.rfc is None
    assert request.original_curp == "BEEJ760109HSLRSL03"
    assert request.identifier_key == "BEEJ760109HSLRSL03"
    assert request.input_type == "CURP"
    assert request.status == "PENDING_CURP_LOOKUP"


def test_rfc_has_priority_over_curp(
    db: Session,
) -> None:
    client = create_client(db)

    result = register_client_message(
        db,
        IncomingWhatsAppMessage(
            message_id="MSG-003",
            source_jid=client.whatsapp_jid,
            sender_jid=None,
            sender_name=None,
            text=(
                "TOFL980825MJCVLZ04\n"
                "TOFL980825ABC"
            ),
        ),
    )

    assert result.created_count == 1
    assert result.ignored_curps == [
        "TOFL980825MJCVLZ04"
    ]

    requests = list(
        db.scalars(
            select(Request)
        )
    )

    assert len(requests) == 1
    assert requests[0].rfc == "TOFL980825ABC"
    assert requests[0].original_curp is None


def test_multiple_rfcs_in_same_message(
    db: Session,
) -> None:
    client = create_client(db)

    result = register_client_message(
        db,
        IncomingWhatsAppMessage(
            message_id="MSG-004",
            source_jid=client.whatsapp_jid,
            sender_jid=None,
            sender_name=None,
            text=(
                "VALA830403RA8\n"
                "RAHC850707NW3\n"
                "MECA7305107Y3"
            ),
        ),
    )

    assert result.created_count == 3

    requests = list(
        db.scalars(
            select(Request).order_by(Request.id)
        )
    )

    assert [request.rfc for request in requests] == [
        "VALA830403RA8",
        "RAHC850707NW3",
        "MECA7305107Y3",
    ]


def test_duplicate_webhook_is_idempotent(
    db: Session,
) -> None:
    client = create_client(db)

    message = IncomingWhatsAppMessage(
        message_id="MSG-005",
        source_jid=client.whatsapp_jid,
        sender_jid=None,
        sender_name=None,
        text="VALA830403RA8",
    )

    first = register_client_message(
        db,
        message,
    )

    second = register_client_message(
        db,
        message,
    )

    assert first.created_count == 1
    assert second.created_count == 0
    assert second.duplicate_identifiers == [
        "VALA830403RA8"
    ]

    requests = list(
        db.scalars(
            select(Request)
        )
    )

    assert len(requests) == 1


def test_message_without_identifiers(
    db: Session,
) -> None:
    client = create_client(db)

    result = register_client_message(
        db,
        IncomingWhatsAppMessage(
            message_id="MSG-006",
            source_jid=client.whatsapp_jid,
            sender_jid=None,
            sender_name=None,
            text="Hola, muchas gracias",
        ),
    )

    assert result.created_count == 0
    assert result.no_identifiers_found is True


def test_unknown_client_is_rejected(
    db: Session,
) -> None:
    with pytest.raises(ClientNotFoundError):
        register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="MSG-007",
                source_jid="UNKNOWN@g.us",
                sender_jid=None,
                sender_name=None,
                text="VALA830403RA8",
            ),
        )


def test_inactive_client_is_rejected(
    db: Session,
) -> None:
    client = create_client(
        db,
        active=False,
    )

    with pytest.raises(ClientInactiveError):
        register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id="MSG-008",
                source_jid=client.whatsapp_jid,
                sender_jid=None,
                sender_name=None,
                text="VALA830403RA8",
            ),
        )


@pytest.mark.parametrize(
    "curp",
    [
        "CASE020722HTSRNDA8",
        "EIFJ970525HSPNLS09",
    ],
)
def test_valid_curp_formats_are_accepted(
    db: Session,
    curp: str,
) -> None:
    client = create_client(db)

    result = register_client_message(
        db,
        IncomingWhatsAppMessage(
            message_id=f"VALID-{curp}",
            source_jid=client.whatsapp_jid,
            sender_jid=None,
            sender_name=None,
            text=curp,
        ),
    )

    assert result.created_count == 1
    assert result.invalid_curps == []

    request = db.scalar(
        select(Request)
    )

    assert request is not None
    assert request.original_curp == curp
    assert (
        request.status
        == "PENDING_CURP_LOOKUP"
    )


@pytest.mark.parametrize(
    "curp",
    [
        "CASE020722HTSRNDAB",
        "C4SE020722HTSRNDA8",
        "CASE20722HTSRNDA8",
        "CASE020722PTSRNDA8",
        "EIFJ97O525HSPNLS09",
        "EIF970525HSPNLS09",
    ],
)
def test_invalid_curp_formats_are_rejected(
    db: Session,
    curp: str,
) -> None:
    client = create_client(db)

    result = register_client_message(
        db,
        IncomingWhatsAppMessage(
            message_id=f"INVALID-{curp}",
            source_jid=client.whatsapp_jid,
            sender_jid=None,
            sender_name=None,
            text=curp,
        ),
    )

    assert result.created_count == 0
    assert curp in result.invalid_curps

    requests = list(
        db.scalars(
            select(Request)
        )
    )

    assert requests == []
