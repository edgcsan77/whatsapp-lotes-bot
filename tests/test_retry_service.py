from app.services.retry_service import (
    MAX_RETRY_ATTEMPTS,
    RETRY_DELAYS_MINUTES,
    build_delivery_retry_text,
)
from app.models.request import Request


def test_retry_schedule() -> None:
    assert MAX_RETRY_ATTEMPTS == 5
    assert RETRY_DELAYS_MINUTES == {
        1: 1,
        2: 2,
        3: 5,
        4: 10,
        5: 30,
    }


def test_build_ok_delivery_retry() -> None:
    request = Request(
        client_id=1,
        provider_id=1,
        whatsapp_message_id="TEST-1",
        identifier_key="VALA830403RA8",
        source_jid="CLIENT@g.us",
        original_text="VALA830403RA8",
        input_type="RFC",
        rfc="VALA830403RA8",
        status="DELIVERY_FAILED",
        provider_result=(
            "VALA830403RA8 19060153257"
        ),
        idcif="19060153257",
        result_code="OK",
        sale_price=1,
    )

    assert build_delivery_retry_text(
        [request]
    ) == (
        "✅ Resultado recibido\n\n"
        "VALA830403RA8 19060153257"
    )


def test_build_multiple_delivery_retry() -> None:
    first = Request(
        client_id=1,
        provider_id=1,
        whatsapp_message_id="TEST-2",
        identifier_key="VALA830403RA8",
        source_jid="CLIENT@g.us",
        original_text="VALA830403RA8",
        input_type="RFC",
        rfc="VALA830403RA8",
        status="DELIVERY_FAILED",
        provider_result=(
            "VALA830403RA8 SIN ID"
        ),
        result_code="SIN_ID",
        sale_price=1,
    )

    second = Request(
        client_id=1,
        provider_id=1,
        whatsapp_message_id="TEST-3",
        identifier_key="RAHC850707NW3",
        source_jid="CLIENT@g.us",
        original_text="RAHC850707NW3",
        input_type="RFC",
        rfc="RAHC850707NW3",
        status="DELIVERY_FAILED",
        provider_result=(
            "RAHC850707NW3 SR"
        ),
        result_code="SIN_RESULTADO",
        sale_price=1,
    )

    text = build_delivery_retry_text(
        [first, second]
    )

    assert text == (
        "✅ Resultados recibidos\n\n"
        "VALA830403RA8 SIN ID\n"
        "RAHC850707NW3 SR"
    )


def test_retry_attempt_limit_is_five() -> None:
    attempts = 0

    for _ in range(MAX_RETRY_ATTEMPTS):
        attempts += 1

    assert attempts == 5
    assert attempts >= MAX_RETRY_ATTEMPTS
