from app.services.acknowledgement_service import (
    build_request_acknowledgement,
)


def test_single_rfc_acknowledgement() -> None:
    text = build_request_acknowledgement(
        ["VALA830403RA8"]
    )

    assert text == (
        "✅ Solicitud recibida\n\n"
        "1 RFC\n"
        "Estado: pendiente de envío"
    )


def test_single_curp_acknowledgement() -> None:
    text = build_request_acknowledgement(
        ["BEEJ760109HSLRSL03"]
    )

    assert text == (
        "✅ Solicitud recibida\n\n"
        "1 CURP\n"
        "Estado: pendiente de envío"
    )


def test_rfc_and_curp_acknowledgement() -> None:
    text = build_request_acknowledgement(
        [
            "BEEJ760109HSLRSL03",
            "VALA830403RA8",
        ]
    )

    assert text == (
        "✅ Solicitud recibida\n\n"
        "1 CURP y 1 RFC\n"
        "Estado: pendiente de envío"
    )


def test_multiple_curps_and_rfcs() -> None:
    text = build_request_acknowledgement(
        [
            "BEEJ760109HSLRSL03",
            "TOFL980825MJCVLZ04",
            "VALA830403RA8",
            "RAHC850707NW3",
            "MECA7305107Y3",
        ]
    )

    assert text == (
        "✅ Solicitud recibida\n\n"
        "2 CURP y 3 RFC\n"
        "Estado: pendiente de envío"
    )


def test_duplicate_identifiers_are_removed() -> None:
    text = build_request_acknowledgement(
        [
            "VALA830403RA8",
            "VALA830403RA8",
        ]
    )

    assert text == (
        "✅ Solicitud recibida\n\n"
        "1 RFC\n"
        "Estado: pendiente de envío"
    )


def test_empty_acknowledgement() -> None:
    assert (
        build_request_acknowledgement([])
        == ""
    )
