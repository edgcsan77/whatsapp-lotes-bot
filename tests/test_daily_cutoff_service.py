from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models.client import Client
from app.models.daily_cutoff import DailyCutoff
from app.models.request import Request
from app.services.daily_cutoff_service import (
    calculate_latest_cutoff_period,
    create_daily_cutoff,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
    )

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        yield session


def create_client(db: Session) -> Client:
    client = Client(
        name="Cliente corte",
        source_type="group",
        whatsapp_jid=(
            "120363111111111111@g.us"
        ),
        price_per_request=(
            Decimal("25.00")
        ),
        batch_enabled=True,
        batch_interval_minutes=10,
        batch_max_items=50,
        daily_cutoff_enabled=True,
        daily_cutoff_time="23:30",
        timezone="America/Monterrey",
        active=True,
    )

    db.add(client)
    db.commit()
    db.refresh(client)

    return client


def create_request(
    db: Session,
    *,
    client: Client,
    index: int,
    status: str,
    input_type: str,
    received_at: datetime,
) -> Request:
    is_curp = input_type == "CURP"

    request = Request(
        client_id=client.id,
        provider_id=None,
        whatsapp_message_id=(
            f"MSG-CUTOFF-{index}"
        ),
        identifier_key=(
            f"CURP{index:014d}"
            if is_curp
            else f"RFC{index:010d}"
        ),
        source_jid=client.whatsapp_jid,
        sender_jid=None,
        sender_name=None,
        original_text=f"Solicitud {index}",
        input_type=input_type,
        rfc=(
            None
            if is_curp
            else f"RFC{index:010d}"
        ),
        original_curp=(
            f"CURP{index:014d}"
            if is_curp
            else None
        ),
        detected_name=None,
        status=status,
        sale_price=Decimal("25.00"),
        received_at=received_at,
    )

    db.add(request)

    return request


def test_calculate_period_after_cutoff() -> None:
    period = calculate_latest_cutoff_period(
        now_utc=datetime(
            2026,
            8,
            7,
            5,
            35,
            tzinfo=UTC,
        ),
        cutoff_time_value="23:30",
        timezone_name=(
            "America/Monterrey"
        ),
    )

    assert period.start_utc == datetime(
        2026,
        8,
        6,
        5,
        30,
        tzinfo=UTC,
    )

    assert period.end_utc == datetime(
        2026,
        8,
        7,
        5,
        30,
        tzinfo=UTC,
    )


def test_calculate_period_before_cutoff() -> None:
    period = calculate_latest_cutoff_period(
        now_utc=datetime(
            2026,
            8,
            7,
            5,
            20,
            tzinfo=UTC,
        ),
        cutoff_time_value="23:30",
        timezone_name=(
            "America/Monterrey"
        ),
    )

    assert period.start_utc == datetime(
        2026,
        8,
        5,
        5,
        30,
        tzinfo=UTC,
    )

    assert period.end_utc == datetime(
        2026,
        8,
        6,
        5,
        30,
        tzinfo=UTC,
    )


def test_create_cutoff_totals(
    db: Session,
) -> None:
    client = create_client(db)

    received_at = datetime(
        2026,
        8,
        6,
        12,
        0,
        tzinfo=UTC,
    )

    create_request(
        db,
        client=client,
        index=1,
        status="DELIVERED",
        input_type="RFC",
        received_at=received_at,
    )

    create_request(
        db,
        client=client,
        index=2,
        status="PENDING_BATCH",
        input_type="RFC",
        received_at=received_at,
    )

    create_request(
        db,
        client=client,
        index=3,
        status="CURP_LOOKUP_FAILED",
        input_type="CURP",
        received_at=received_at,
    )

    create_request(
        db,
        client=client,
        index=4,
        status="DELIVERY_FAILED",
        input_type="CURP",
        received_at=received_at,
    )

    db.commit()

    cutoff, created = create_daily_cutoff(
        db,
        client=client,
        now_utc=datetime(
            2026,
            8,
            7,
            5,
            35,
            tzinfo=UTC,
        ),
    )

    assert created is True
    assert cutoff.total_requests == 4
    assert cutoff.delivered_count == 1
    assert cutoff.failed_count == 1
    assert cutoff.pending_count == 2
    assert cutoff.rfc_count == 2
    assert cutoff.curp_count == 2
    assert cutoff.total_amount == (
        Decimal("100.00")
    )
    assert cutoff.status == "CREATED"


def test_cutoff_is_idempotent(
    db: Session,
) -> None:
    client = create_client(db)

    now_utc = datetime(
        2026,
        8,
        7,
        5,
        35,
        tzinfo=UTC,
    )

    first, first_created = (
        create_daily_cutoff(
            db,
            client=client,
            now_utc=now_utc,
        )
    )

    second, second_created = (
        create_daily_cutoff(
            db,
            client=client,
            now_utc=now_utc,
        )
    )

    assert first_created is True
    assert second_created is False
    assert first.id == second.id

    rows = list(
        db.scalars(
            select(DailyCutoff)
        )
    )

    assert len(rows) == 1


def test_render_daily_cutoff_message(
    db: Session,
) -> None:
    from app.services.daily_cutoff_service import (
        CutoffTotals,
        render_daily_cutoff_message,
    )

    client = create_client(db)

    period = calculate_latest_cutoff_period(
        now_utc=datetime(
            2026,
            8,
            7,
            5,
            35,
            tzinfo=UTC,
        ),
        cutoff_time_value="23:30",
        timezone_name="America/Monterrey",
    )

    totals = CutoffTotals(
        total_requests=5,
        idcif_count=1,
        delivered_count=4,
        pending_count=1,
        failed_count=0,
        rfc_count=3,
        curp_count=2,
        total_amount=Decimal("125.00"),
    )

    message = render_daily_cutoff_message(
        client=client,
        period=period,
        totals=totals,
    )

    assert (
        "CORTE - 6 DE AGOSTO DEL 2026"
        in message
    )
    assert "🧾 Resumen del día" in message
    assert "📄 *Total de IDCIF: 1*" in message
    assert (
        "Si algún rastreo se envió doble"
        in message
    )


def test_next_period_starts_after_last_sent_cutoff(
    db: Session,
) -> None:
    from app.models.daily_cutoff import (
        DailyCutoff,
    )
    from app.services.daily_cutoff_service import (
        calculate_next_cutoff_period,
    )

    client = create_client(db)
    client.daily_cutoff_time = "23:30"

    previous = DailyCutoff(
        client_id=client.id,
        period_start=datetime(
            2026, 8, 5, 21, 33,
            tzinfo=UTC,
        ),
        period_end=datetime(
            2026, 8, 6, 21, 33,
            tzinfo=UTC,
        ),
        total_requests=3,
        delivered_count=3,
        pending_count=0,
        failed_count=0,
        rfc_count=1,
        curp_count=2,
        total_amount=Decimal("3.00"),
        status="SENT",
    )

    db.add(previous)
    db.commit()

    period = calculate_next_cutoff_period(
        db,
        client=client,
        now_utc=datetime(
            2026, 8, 7, 5, 35,
            tzinfo=UTC,
        ),
    )

    assert period is not None
    assert period.start_utc == datetime(
        2026, 8, 6, 21, 33,
        tzinfo=UTC,
    )
    assert period.end_utc == datetime(
        2026, 8, 7, 5, 30,
        tzinfo=UTC,
    )


def test_period_already_covered_returns_none(
    db: Session,
) -> None:
    from app.models.daily_cutoff import (
        DailyCutoff,
    )
    from app.services.daily_cutoff_service import (
        calculate_next_cutoff_period,
    )

    client = create_client(db)

    previous = DailyCutoff(
        client_id=client.id,
        period_start=datetime(
            2026, 8, 5, 5, 30,
            tzinfo=UTC,
        ),
        period_end=datetime(
            2026, 8, 7, 5, 30,
            tzinfo=UTC,
        ),
        total_requests=3,
        delivered_count=3,
        pending_count=0,
        failed_count=0,
        rfc_count=1,
        curp_count=2,
        total_amount=Decimal("3.00"),
        status="SENT",
    )

    db.add(previous)
    db.commit()

    period = calculate_next_cutoff_period(
        db,
        client=client,
        now_utc=datetime(
            2026, 8, 7, 5, 35,
            tzinfo=UTC,
        ),
    )

    assert period is None
