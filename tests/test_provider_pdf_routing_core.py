from datetime import UTC, datetime
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
from app.services.provider_parser import (
    ParsedProviderResult,
)
from app.services import (
    provider_response_service
    as service,
)


RFC_PDF = "VALA830403RA8"
RFC_TEXT = "MECA7305107Y3"
IDCIF = "25030288082"


def create_context():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )

    Base.metadata.create_all(
        engine
    )

    db = Session(engine)

    provider = Provider(
        name="Proveedor PDF core",
        whatsapp_jid=(
            "120363000000002001"
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
        name="Cliente PDF core",
        source_type="group",
        whatsapp_jid=(
            "120363000000002002"
            "@g.us"
        ),
        default_provider_id=
            provider.id,
        price_per_request=
            Decimal("2.00"),
        generic_price_per_request=
            Decimal("1.50"),
        generic_pdf_enabled=False,
        idcif_pdf_enabled=True,
        batch_enabled=True,
        batch_interval_minutes=10,
        batch_max_items=1000,
        daily_cutoff_enabled=True,
        daily_cutoff_time="23:30",
        timezone="America/Monterrey",
        active=True,
    )

    db.add(client)
    db.flush()

    now = datetime.now(UTC)

    request_pdf = Request(
        client_id=client.id,
        provider_id=provider.id,
        whatsapp_message_id="CORE-PDF",
        identifier_key=RFC_PDF,
        source_jid=client.whatsapp_jid,
        sender_jid=None,
        sender_name=None,
        original_text=RFC_PDF,
        input_type="RFC",
        service_type="RFC_IDCIF",
        delivery_format="PDF",
        lookup_route="DIRECT_RFC_IDCIF",
        rfc=RFC_PDF,
        original_curp=None,
        detected_name=None,
        status="RESULT_RECEIVED",
        provider_result=(
            f"{RFC_PDF} {IDCIF}"
        ),
        idcif=IDCIF,
        result_code="OK",
        sale_price=Decimal("2.00"),
        received_at=now,
        provider_replied_at=now,
    )

    request_text = Request(
        client_id=client.id,
        provider_id=provider.id,
        whatsapp_message_id="CORE-TEXT",
        identifier_key=RFC_TEXT,
        source_jid=client.whatsapp_jid,
        sender_jid=None,
        sender_name=None,
        original_text=RFC_TEXT,
        input_type="RFC",
        service_type="RFC_IDCIF",
        delivery_format="TEXT",
        lookup_route=None,
        rfc=RFC_TEXT,
        original_curp=None,
        detected_name=None,
        status="RESULT_RECEIVED",
        provider_result=(
            f"{RFC_TEXT} NO ID"
        ),
        idcif=None,
        result_code="SIN_ID",
        sale_price=Decimal("2.00"),
        received_at=now,
        provider_replied_at=now,
    )

    db.add_all(
        [
            request_pdf,
            request_text,
        ]
    )

    db.commit()
    db.refresh(request_pdf)
    db.refresh(request_text)

    return (
        db,
        provider,
        client,
        request_pdf,
        request_text,
    )


def test_pdf_result_is_removed_from_text_group(
    monkeypatch,
) -> None:
    (
        db,
        provider,
        client,
        request_pdf,
        request_text,
    ) = create_context()

    try:
        processing_result = (
            service.ProviderProcessingResult(
                provider_id=provider.id,
                provider_name=provider.name,
                parsed_count=2,
                matched_request_ids=[
                    request_pdf.id,
                    request_text.id,
                ],
            )
        )

        original_group = (
            service.ClientDeliveryGroup(
                client_id=client.id,
                client_name=client.name,
                client_jid=
                    client.whatsapp_jid,
                request_ids=(
                    request_pdf.id,
                    request_text.id,
                ),
                text="ORIGINAL MIXED TEXT",
            )
        )

        def fake_legacy(
            db,
            *,
            provider,
            provider_message_id,
            text,
        ):
            return (
                processing_result,
                [original_group],
            )

        monkeypatch.setattr(
            service,
            "_register_provider_results_legacy",
            fake_legacy,
        )

        result, groups = (
            service.register_provider_results(
                db,
                provider=provider,
                provider_message_id=
                    "PROVIDER-CORE",
                text="RESPUESTA",
            )
        )

        refreshed_pdf = db.scalar(
            select(Request).where(
                Request.id
                == request_pdf.id
            )
        )

        assert refreshed_pdf is not None

        assert refreshed_pdf.status == (
            "PENDING_PDF"
        )

        assert refreshed_pdf.pdf_status == (
            "PENDING"
        )

        assert (
            result.queued_pdf_request_ids
            == [request_pdf.id]
        )

        assert len(groups) == 1

        assert groups[0].request_ids == (
            request_text.id,
        )

        assert "NO ID" in groups[0].text
        assert RFC_TEXT in groups[0].text
        assert RFC_PDF not in groups[0].text

    finally:
        db.close()


def test_no_id_pdf_request_is_not_queued(
    monkeypatch,
) -> None:
    (
        db,
        provider,
        client,
        request_pdf,
        _,
    ) = create_context()

    try:
        request_pdf.idcif = None
        request_pdf.result_code = "SIN_ID"
        request_pdf.provider_result = (
            f"{RFC_PDF} NO ID"
        )

        db.commit()

        processing_result = (
            service.ProviderProcessingResult(
                provider_id=provider.id,
                provider_name=provider.name,
                parsed_count=1,
                matched_request_ids=[
                    request_pdf.id
                ],
            )
        )

        original_group = (
            service.ClientDeliveryGroup(
                client_id=client.id,
                client_name=client.name,
                client_jid=
                    client.whatsapp_jid,
                request_ids=(
                    request_pdf.id,
                ),
                text=(
                    f"✅ Resultado recibido\n\n"
                    f"NO ID {RFC_PDF}"
                ),
            )
        )

        def fake_legacy(
            db,
            *,
            provider,
            provider_message_id,
            text,
        ):
            return (
                processing_result,
                [original_group],
            )

        monkeypatch.setattr(
            service,
            "_register_provider_results_legacy",
            fake_legacy,
        )

        result, groups = (
            service.register_provider_results(
                db,
                provider=provider,
                provider_message_id=
                    "PROVIDER-NO-ID",
                text="RESPUESTA",
            )
        )

        assert (
            result.queued_pdf_request_ids
            == []
        )

        assert len(groups) == 1

        assert groups[0].request_ids == (
            request_pdf.id,
        )

    finally:
        db.close()
