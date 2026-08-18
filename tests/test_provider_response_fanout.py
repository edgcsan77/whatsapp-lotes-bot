from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models.batch import Batch, BatchItem
from app.models.client import Client
from app.models.provider import Provider
from app.models.request import Request
from app.services.provider_response_service import (
    register_provider_results,
)


RFC = "MAAF901118T20"
IDCIF = "15040018668"


def create_provider(
    db: Session,
) -> Provider:
    provider = Provider(
        name="Proveedor test",
        whatsapp_jid=
            "120363000000000001@g.us",
        evolution_instance="test",
        priority=100,
        timeout_minutes=60,
        active=True,
    )

    db.add(provider)
    db.commit()
    db.refresh(provider)

    return provider


def create_client(
    db: Session,
    *,
    provider_id: int,
    number: int,
) -> Client:
    client = Client(
        name=f"Cliente {number}",
        source_type="group",
        whatsapp_jid=(
            f"1203630000000001{number:02d}"
            "@g.us"
        ),
        default_provider_id=provider_id,
        price_per_request=
            Decimal("10.00"),
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


def create_request(
    db: Session,
    *,
    client: Client,
    provider: Provider,
    message_id: str,
    status: str,
) -> Request:
    now = datetime.now(UTC)

    request = Request(
        client_id=client.id,
        provider_id=provider.id,
        whatsapp_message_id=message_id,
        identifier_key=RFC,
        source_jid=client.whatsapp_jid,
        sender_jid=None,
        sender_name=None,
        original_text=RFC,
        input_type="RFC",
        rfc=RFC,
        original_curp=None,
        detected_name=None,
        status=status,
        sale_price=
            Decimal("10.00"),
        received_at=now,
        sent_to_provider_at=now,
    )

    db.add(request)
    db.commit()
    db.refresh(request)

    return request


def create_batch(
    db: Session,
    *,
    provider: Provider,
    requests: list[Request],
) -> Batch:
    now = datetime.now(UTC)

    batch = Batch(
        client_id=None,
        provider_id=provider.id,
        status="SENT",
        request_count=len(requests),
        outbound_text="\n".join(
            request.rfc
            for request in requests
            if request.rfc
        ),
        sent_at=now,
    )

    db.add(batch)
    db.flush()

    for position, request in enumerate(
        requests,
        start=1,
    ):
        db.add(
            BatchItem(
                batch_id=batch.id,
                request_id=request.id,
                position=position,
            )
        )

    db.commit()
    db.refresh(batch)

    return batch


def test_same_rfc_same_batch_fans_out_to_all_clients() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )

    Base.metadata.create_all(
        engine
    )

    with Session(engine) as db:
        provider = create_provider(db)

        client_a = create_client(
            db,
            provider_id=provider.id,
            number=1,
        )

        client_b = create_client(
            db,
            provider_id=provider.id,
            number=2,
        )

        request_a = create_request(
            db,
            client=client_a,
            provider=provider,
            message_id="MSG-A",
            status="SENT_TO_PROVIDER",
        )

        request_b = create_request(
            db,
            client=client_b,
            provider=provider,
            message_id="MSG-B",
            status="SENT_TO_PROVIDER",
        )

        create_batch(
            db,
            provider=provider,
            requests=[
                request_a,
                request_b,
            ],
        )

        result, groups = (
            register_provider_results(
                db,
                provider=provider,
                provider_message_id=
                    "PROVIDER-MSG-1",
                text=f"{RFC} {IDCIF}",
            )
        )

        assert set(
            result.matched_request_ids
        ) == {
            request_a.id,
            request_b.id,
        }

        assert len(groups) == 2

        refreshed = list(
            db.scalars(
                select(Request)
                .order_by(Request.id)
            )
        )

        assert len(refreshed) == 2

        for request in refreshed:
            assert request.status == (
                "RESULT_RECEIVED"
            )
            assert request.idcif == IDCIF
            assert request.result_code == "OK"


def test_late_provider_response_matches_timeout() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )

    Base.metadata.create_all(
        engine
    )

    with Session(engine) as db:
        provider = create_provider(db)

        client = create_client(
            db,
            provider_id=provider.id,
            number=3,
        )

        request = create_request(
            db,
            client=client,
            provider=provider,
            message_id="MSG-TIMEOUT",
            status="PROVIDER_TIMEOUT",
        )

        create_batch(
            db,
            provider=provider,
            requests=[request],
        )

        result, groups = (
            register_provider_results(
                db,
                provider=provider,
                provider_message_id=
                    "PROVIDER-LATE",
                text=f"{RFC} {IDCIF}",
            )
        )

        assert result.matched_request_ids == [
            request.id
        ]

        assert len(groups) == 1

        db.expire_all()

        refreshed = db.get(
            Request,
            request.id,
        )

        assert refreshed is not None
        assert refreshed.status == (
            "RESULT_RECEIVED"
        )
        assert refreshed.idcif == IDCIF
        assert refreshed.result_code == "OK"


def test_same_rfc_across_batches_matches_latest_batch_only() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )

    Base.metadata.create_all(
        engine
    )

    with Session(engine) as db:
        provider = create_provider(db)

        old_client = create_client(
            db,
            provider_id=provider.id,
            number=4,
        )

        new_client = create_client(
            db,
            provider_id=provider.id,
            number=5,
        )

        old_request = create_request(
            db,
            client=old_client,
            provider=provider,
            message_id="MSG-OLD",
            status="PROVIDER_TIMEOUT",
        )

        new_request = create_request(
            db,
            client=new_client,
            provider=provider,
            message_id="MSG-NEW",
            status="SENT_TO_PROVIDER",
        )

        # Diferentes lotes intencionalmente.
        create_batch(
            db,
            provider=provider,
            requests=[old_request],
        )

        create_batch(
            db,
            provider=provider,
            requests=[new_request],
        )

        result, groups = (
            register_provider_results(
                db,
                provider=provider,
                provider_message_id=
                    "PROVIDER-MSG-BOTH",
                text=f"{RFC} {IDCIF}",
            )
        )

        assert result.matched_request_ids == [
            new_request.id
        ]

        assert len(groups) == 1

        db.expire_all()

        refreshed_old = db.get(
            Request,
            old_request.id,
        )

        refreshed_new = db.get(
            Request,
            new_request.id,
        )

        assert refreshed_old is not None
        assert refreshed_new is not None

        assert refreshed_old.status == (
            "PROVIDER_TIMEOUT"
        )

        assert refreshed_old.idcif is None

        assert refreshed_new.status == (
            "RESULT_RECEIVED"
        )

        assert refreshed_new.idcif == IDCIF


def test_batch_send_failed_is_not_matched() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )

    Base.metadata.create_all(
        engine
    )

    with Session(engine) as db:
        provider = create_provider(db)

        client = create_client(
            db,
            provider_id=provider.id,
            number=6,
        )

        request = create_request(
            db,
            client=client,
            provider=provider,
            message_id="MSG-FAILED",
            status="BATCH_SEND_FAILED",
        )

        result, groups = (
            register_provider_results(
                db,
                provider=provider,
                provider_message_id=
                    "PROVIDER-NOT-SENT",
                text=f"{RFC} {IDCIF}",
            )
        )

        assert result.matched_request_ids == []
        assert result.unmatched_rfcs == [
            RFC
        ]

        assert groups == []

        db.expire_all()

        refreshed = db.get(
            Request,
            request.id,
        )

        assert refreshed is not None
        assert refreshed.status == (
            "BATCH_SEND_FAILED"
        )
        assert refreshed.idcif is None
