from app.services.delayed_client_message_worker import (
    build_invalid_identifier_notice,
)


def test_notice_lists_curp_and_rfc() -> None:
    text = (
        build_invalid_identifier_notice(
            invalid_curps=[
                "CAH0070306HJCHRMA5",
            ],
            invalid_rfcs=[
                "CAHO070306HJ",
            ],
        )
    )

    assert "Solicitud no procesada" in text
    assert "CURP no válida" in text
    assert "CAH0070306HJCHRMA5" in text
    assert "RFC no válido" in text
    assert "CAHO070306HJ" in text
    assert "envíalo nuevamente" in text
