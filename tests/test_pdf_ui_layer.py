import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.integrations.evolution_client import (
    EvolutionSendResult,
)
from app.models.client import Client
from app.models.daily_cutoff import DailyCutoff
from app.models.request import Request
from app.routes import panel
from app.services.daily_cutoff_service import (
    calculate_cutoff_totals,
)
from app.services.delayed_client_message_worker import (
    build_generic_not_enabled_notice,
)


class DummyTemplates:
    def TemplateResponse(self, **kwargs):
        return kwargs["context"]


def create_db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:"
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def create_client(db: Session) -> Client:
    client = Client(
        name="Cliente PDF UI",
        source_type="group",
        whatsapp_jid=(
            "120363000000009001@g.us"
        ),
        default_provider_id=None,
        price_per_request=Decimal("2.00"),
        generic_price_per_request=
            Decimal("1.50"),
        generic_pdf_enabled=False,
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


def create_request(
    db: Session,
    *,
    client: Client,
    index: int,
    service_type: str,
    delivery_format: str,
    status: str,
    result_code: str | None = None,
    idcif: str | None = None,
) -> Request:
    request = Request(
        client_id=client.id,
        provider_id=None,
        whatsapp_message_id=f"PDF-UI-{index}",
        identifier_key=f"VALA83040{index:02d}RA8",
        source_jid=client.whatsapp_jid,
        sender_jid="5218990000000@s.whatsapp.net",
        sender_name="Prueba",
        original_text="SOLICITUD",
        input_type="RFC",
        service_type=service_type,
        delivery_format=delivery_format,
        lookup_route=(
            "RFC_CHECKID"
            if service_type == "RFC_GENERIC"
            else (
                "DIRECT_RFC_IDCIF"
                if delivery_format == "PDF"
                else None
            )
        ),
        rfc=f"VALA83040{index:02d}RA8",
        original_curp=None,
        detected_name=None,
        status=status,
        result_code=result_code,
        idcif=idcif,
        sale_price=(
            Decimal("1.50")
            if service_type == "RFC_GENERIC"
            else Decimal("2.00")
        ),
        received_at=datetime.now(UTC),
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def patch_panel_security(monkeypatch):
    monkeypatch.setattr(
        panel,
        "require_authenticated",
        lambda request: None,
    )
    monkeypatch.setattr(
        panel,
        "validate_csrf",
        lambda request, token: True,
    )
    monkeypatch.setattr(
        panel,
        "ensure_csrf_token",
        lambda request: "csrf",
    )
    monkeypatch.setattr(
        panel,
        "register_admin_audit",
        lambda *args, **kwargs: None,
    )


def test_generic_not_enabled_notice() -> None:
    text = build_generic_not_enabled_notice(
        [
            "VALA830403RA8-G",
            "VALA830403RA8-G",
        ]
    )
    assert "Servicio no habilitado" in text
    assert "VALA830403RA8-G" in text
    assert "no fueron procesadas" in text


def test_cutoff_counts_generic_separately() -> None:
    requests = [
        SimpleNamespace(
            status="DELIVERED",
            service_type="RFC_IDCIF",
            result_code="OK",
            idcif="25030288082",
            input_type="RFC",
            sale_price=Decimal("2.00"),
        ),
        SimpleNamespace(
            status="DELIVERED",
            service_type="RFC_GENERIC",
            result_code="OK",
            idcif=None,
            input_type="CURP",
            sale_price=Decimal("1.50"),
        ),
        SimpleNamespace(
            status="PDF_FAILED",
            service_type="RFC_GENERIC",
            result_code=None,
            idcif=None,
            input_type="RFC",
            sale_price=Decimal("1.50"),
        ),
    ]

    totals = calculate_cutoff_totals(
        requests
    )

    assert totals.idcif_count == 1
    assert totals.generic_count == 1
    assert totals.delivered_count == 2
    assert totals.failed_count == 1
    assert totals.total_amount == Decimal("5.00")


def test_update_client_saves_pdf_settings(
    monkeypatch,
) -> None:
    patch_panel_security(monkeypatch)
    db = create_db()
    try:
        client = create_client(db)
        response = panel.update_client(
            client_id=client.id,
            request=object(),
            name=client.name,
            source_type="group",
            whatsapp_jid=client.whatsapp_jid,
            price_per_request="2.50",
            generic_price_per_request="1.75",
            batch_interval_minutes=10,
            batch_max_items=1000,
            daily_cutoff_time="23:30",
            timezone="America/Monterrey",
            csrf_token="csrf",
            daily_cutoff_enabled="on",
            generic_pdf_enabled="on",
            idcif_pdf_enabled="on",
            active="on",
            db=db,
        )
        assert response.status_code == 303
        db.refresh(client)
        assert client.price_per_request == Decimal("2.50")
        assert client.generic_price_per_request == Decimal("1.75")
        assert client.generic_pdf_enabled is True
        assert client.idcif_pdf_enabled is True
    finally:
        db.close()


def test_clients_page_exposes_separate_counts(
    monkeypatch,
) -> None:
    patch_panel_security(monkeypatch)
    monkeypatch.setattr(
        panel,
        "templates",
        DummyTemplates(),
    )
    db = create_db()
    try:
        client = create_client(db)
        create_request(
            db,
            client=client,
            index=1,
            service_type="RFC_IDCIF",
            delivery_format="TEXT",
            status="DELIVERED",
            result_code="OK",
            idcif="25030288082",
        )
        create_request(
            db,
            client=client,
            index=2,
            service_type="RFC_GENERIC",
            delivery_format="PDF",
            status="DELIVERED",
            result_code="OK",
        )
        context = panel.clients_page(
            request=object(),
            db=db,
        )
        assert context["client_idcif_counts"][client.id] == 1
        assert context["client_generic_counts"][client.id] == 1
    finally:
        db.close()


def test_cutoffs_page_exposes_generic_summary(
    monkeypatch,
) -> None:
    patch_panel_security(monkeypatch)
    monkeypatch.setattr(
        panel,
        "templates",
        DummyTemplates(),
    )
    db = create_db()
    try:
        client = create_client(db)
        now = datetime.now(UTC)
        db.add(
            DailyCutoff(
                client_id=client.id,
                period_start=now,
                period_end=now,
                total_requests=3,
                idcif_count=1,
                generic_count=2,
                delivered_count=3,
                pending_count=0,
                failed_count=0,
                rfc_count=2,
                curp_count=1,
                total_amount=Decimal("5.00"),
                status="SENT",
            )
        )
        db.commit()
        context = panel.cutoffs_page(
            request=object(),
            db=db,
        )
        assert context["cutoff_summary"]["idcif"] == 1
        assert context["cutoff_summary"]["generic"] == 2
    finally:
        db.close()


def test_system_page_includes_pdf_timer(
    monkeypatch,
) -> None:
    patch_panel_security(monkeypatch)
    monkeypatch.setattr(
        panel,
        "templates",
        DummyTemplates(),
    )
    monkeypatch.setattr(
        panel,
        "_service_health",
        lambda unit: {
            "healthy": True,
            "unit": unit,
            "active_state": "active",
            "sub_state": "running",
            "result": "success",
        },
    )
    monkeypatch.setattr(
        panel,
        "_timer_health",
        lambda timer, service: {
            "healthy": True,
            "timer": timer,
            "active_state": "active",
            "last_result": "success",
            "last_trigger": "—",
            "next_trigger": "—",
        },
    )
    context = panel.system_status_page(
        request=object()
    )
    names = [
        item["name"]
        for item in context["automations"]
    ]
    assert "Constancias PDF" in names


def test_retry_pdf_resets_failure(
    monkeypatch,
) -> None:
    patch_panel_security(monkeypatch)
    db = create_db()
    try:
        client = create_client(db)
        request_item = create_request(
            db,
            client=client,
            index=3,
            service_type="RFC_GENERIC",
            delivery_format="PDF",
            status="PDF_FAILED",
        )
        request_item.pdf_status = "FAILED"
        request_item.pdf_attempts = 3
        request_item.pdf_error = "Backend falló"
        request_item.pdf_url = "https://example.com/old.pdf"
        request_item.pdf_filename = "old.pdf"
        db.commit()

        response = panel.request_retry_pdf(
            request=object(),
            request_id=request_item.id,
            csrf_token="csrf",
            db=db,
        )
        assert response.status_code == 303
        db.refresh(request_item)
        assert request_item.status == "PENDING_PDF"
        assert request_item.pdf_status == "PENDING"
        assert request_item.pdf_attempts == 0
        assert request_item.pdf_error is None
        assert request_item.pdf_url is None
        assert request_item.pdf_filename is None
    finally:
        db.close()


def test_resend_generic_pdf_without_provider(
    monkeypatch,
) -> None:
    patch_panel_security(monkeypatch)
    db = create_db()
    sent: list[dict] = []

    async def fake_send_document_message(**kwargs):
        sent.append(kwargs)
        return EvolutionSendResult(
            ok=True,
            message_id="RESENT-PDF",
            raw_response={},
        )

    monkeypatch.setattr(
        panel,
        "send_document_message",
        fake_send_document_message,
    )

    try:
        client = create_client(db)
        request_item = create_request(
            db,
            client=client,
            index=4,
            service_type="RFC_GENERIC",
            delivery_format="PDF",
            status="DELIVERED",
            result_code="OK",
        )
        request_item.pdf_url = "https://example.com/constancia.pdf"
        request_item.pdf_filename = "CONSTANCIA.pdf"
        db.commit()

        response = asyncio.run(
            panel.request_resend_result(
                request=object(),
                request_id=request_item.id,
                csrf_token="csrf",
                db=db,
            )
        )
        assert response.status_code == 303
        assert len(sent) == 1
        assert sent[0]["destination_jid"] == client.whatsapp_jid
        assert sent[0]["file_name"] == "CONSTANCIA.pdf"
    finally:
        db.close()


def test_templates_compile() -> None:
    environment = Environment(
        loader=FileSystemLoader(
            "app/templates"
        )
    )
    environment.filters["panel_datetime"] = (
        lambda value, *args, **kwargs: value
    )
    environment.filters["panel_status"] = (
        lambda value, *args, **kwargs: value
    )
    for template_name in (
        "panel/clients.html",
        "panel/cutoffs.html",
        "panel/system.html",
        "panel/request_detail.html",
    ):
        environment.get_template(
            template_name
        )


def test_pdf_status_labels() -> None:
    assert panel.panel_status("PENDING_PDF") == "Pendiente de PDF"
    assert panel.panel_status("PDF_FAILED") == "Error de PDF"
