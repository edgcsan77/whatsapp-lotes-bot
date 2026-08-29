from app.services.acknowledgement_service import (
    _build_request_acknowledgement_legacy,
    build_request_acknowledgement,
)


RFC = "VALA830403RA8"


def test_normal_ack_is_exactly_legacy() -> None:
    identifiers = [RFC]

    assert (
        build_request_acknowledgement(
            identifiers
        )
        == (
            _build_request_acknowledgement_legacy(
                identifiers
            )
        )
    )


def test_generic_acknowledgement() -> None:
    text = build_request_acknowledgement(
        [
            f"{RFC}-G",
        ]
    )

    assert "1 RFC genérico" in text

    assert (
        "pendiente de generación"
        in text
    )


def test_mixed_acknowledgement() -> None:
    text = build_request_acknowledgement(
        [
            RFC,
            f"{RFC}-G",
        ]
    )

    assert RFC not in text or (
        "Solicitud recibida" in text
    )

    assert "1 RFC genérico" in text
