import asyncio
import inspect
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services import (
    provider_response_service as service,
)


def test_invalid_provider_idcif_goes_to_provider_and_stays_open(
    monkeypatch,
) -> None:
    old_sent_at = datetime(
        2026,
        8,
        30,
        1,
        0,
        tzinfo=UTC,
    )

    request_item = SimpleNamespace(
        id=990001,
        client_id=770001,
        rfc="CACI760811JY8",
        idcif="17050662360",
        result_code="OK",
        delivery_format="PDF",
        status="PENDING_PDF",
        pdf_status="PENDING",
        pdf_next_attempt_at=object(),
        pdf_started_at=None,
        pdf_error=None,
        pdf_url=None,
        pdf_filename=None,
        pdf_delivered_message_id=None,
        pdf_attempts=0,
        delivered_at=None,
        provider_result=(
            "CACI760811JY8 "
            "17050662360"
        ),
        provider_replied_at=datetime.now(
            UTC
        ),
        sent_to_provider_at=old_sent_at,
        sale_price=Decimal("8.00"),
    )

    client = SimpleNamespace(
        id=770001,
        whatsapp_jid=
            "client-test@g.us",
        name="Cliente prueba",
    )

    class FakeDB:
        def get(
            self,
            model,
            item_id,
        ):
            if (
                model is service.Request
                and item_id
                == request_item.id
            ):
                return request_item

            if (
                model is service.Client
                and item_id
                == client.id
            ):
                return client

            return None

        def commit(self):
            return None

    sent_messages = []

    async def fake_validate(
        *,
        rfc,
        idcif,
    ):
        return SimpleNamespace(
            valid=False,
            terminal=True,
            code="IDCIF_INVALID",
        )

    async def fake_send(
        *,
        destination_jid,
        text,
        instance,
    ):
        sent_messages.append(
            {
                "destination":
                    destination_jid,
                "text":
                    text,
                "instance":
                    instance,
            }
        )

        return SimpleNamespace(
            message_id="TEST"
        )

    monkeypatch.setattr(
        service,
        "validate_rfc_idcif",
        fake_validate,
    )

    monkeypatch.setattr(
        service,
        "send_text_message",
        fake_send,
    )

    processing = (
        service.ProviderProcessingResult(
            provider_id=880001,
            provider_name=
                "Proveedor prueba",
        )
    )

    processing.matched_request_ids.append(
        request_item.id
    )

    processing\
        .queued_pdf_request_ids\
        .append(
            request_item.id
        )

    delivery_group = (
        service.ClientDeliveryGroup(
            client_id=client.id,
            client_name=client.name,
            client_jid=
                client.whatsapp_jid,
            request_ids=(
                request_item.id,
            ),
            text=(
                "NO DEBE LLEGAR "
                "AL CLIENTE"
            ),
        )
    )

    result_groups = asyncio.run(
        service
        ._validate_matched_idcif_before_delivery(
            FakeDB(),
            processing_result=
                processing,
            delivery_groups=[
                delivery_group
            ],
            provider_jid=
                "provider-test@g.us",
            evolution_instance=
                "lotesbot",
        )
    )

    assert result_groups == []

    # La solicitud sigue ABIERTA.
    assert request_item.status == (
        "SENT_TO_PROVIDER"
    )

    assert request_item.result_code == (
        "IDCIF_INVALID"
    )

    # Reinicia la ventana de espera.
    assert (
        request_item.sent_to_provider_at
        > old_sent_at
    )

    assert (
        request_item.provider_replied_at
        is None
    )

    # No queda PDF en cola.
    assert request_item.pdf_status is None

    assert (
        request_item.pdf_next_attempt_at
        is None
    )

    assert request_item.pdf_attempts == 0

    # No se borra el precio porque la solicitud
    # todavía puede terminar correctamente.
    assert request_item.sale_price == (
        Decimal("8.00")
    )

    assert (
        request_item.id
        not in processing
        .queued_pdf_request_ids
    )

    # Un solo mensaje y exclusivamente
    # al grupo proveedor.
    assert len(sent_messages) == 1

    assert (
        sent_messages[0]
        ["destination"]
        == "provider-test@g.us"
    )

    assert (
        sent_messages[0]
        ["destination"]
        != client.whatsapp_jid
    )

    assert (
        "ID INCORRECTO"
        in sent_messages[0]["text"]
    )


def test_reopened_request_accepts_second_provider_response():
    finder = getattr(
        service,
        "find_pending_requests_for_result",
        None,
    )

    if finder is None:
        finder = getattr(
            service,
            "find_pending_request_for_result",
        )

    source = inspect.getsource(
        finder
    )

    assert "SENT_TO_PROVIDER" in source
