from app.services.idcif_validation import (
    build_idcif_failure_message,
    is_terminal_code,
    terminal_code_from_error_text,
)


def test_terminal_codes():
    for code in (
        "IDCIF_INVALID",
        "RFC_DADO_DE_BAJA",
        "RFC_CANCELADO",
        "RFC_INACTIVO",
        "RFC_SIN_ESTATUS",
    ):
        assert is_terminal_code(code)


def test_terminal_from_pdf_error():
    assert terminal_code_from_error_text(
        "PDF_BACKEND_HTTP_422:IDCIF_INVALID"
    ) == "IDCIF_INVALID"


def test_message_wrong_id():
    assert build_idcif_failure_message(
        rfc="MADI690925TQ6",
        idcif="15060092886",
        code="IDCIF_INVALID",
    ) == (
        "❌❌\n"
        "MADI690925TQ6 15060092886\n\n"
        "NO SE PUEDE GENERAR CONSTANCIA\n"
        "MOTIVO: ID INCORRECTO"
    )


def test_message_baja():
    assert "MOTIVO: RFC DADO DE BAJA" in build_idcif_failure_message(
        rfc="MADI690925TQ6",
        idcif="15060092886",
        code="RFC_DADO_DE_BAJA",
    )
