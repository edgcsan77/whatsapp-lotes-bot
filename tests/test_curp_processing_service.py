from app.services.curp_processing_service import (
    MAX_CURP_ATTEMPTS,
    CURP_RETRY_DELAYS_MINUTES,
    build_detected_name,
    is_permanent_curp_error,
)
from app.services.curp_rfc_engine import (
    CurpRfcError,
)


def test_curp_retry_schedule() -> None:
    assert MAX_CURP_ATTEMPTS == 5

    assert CURP_RETRY_DELAYS_MINUTES == {
        1: 1,
        2: 2,
        3: 5,
        4: 10,
        5: 30,
    }


def test_build_detected_name() -> None:
    data = {
        "NOMBRE": "GUADALUPE",
        "PRIMER_APELLIDO": "MARTIN",
        "SEGUNDO_APELLIDO": "LUNA",
    }

    assert build_detected_name(data) == (
        "GUADALUPE MARTIN LUNA"
    )


def test_permanent_invalid_curp() -> None:
    error = CurpRfcError(
        "CURP_INVALIDA"
    )

    assert is_permanent_curp_error(
        error
    ) is True


def test_transient_moffin_timeout() -> None:
    error = CurpRfcError(
        "MOFFIN_ERROR:TimeoutException"
    )

    assert is_permanent_curp_error(
        error
    ) is False
