from app.services.acknowledgement_service import (
    build_request_acknowledgement,
)


def test_single_rfc_acknowledgement() -> None:
    text = build_request_acknowledgement(
        ["VALA830403RA8"]
    )

    assert text == (
        "✅ Solicitud recibida\n\n"
        "RFC: VALA830403RA8\n"
        "Estado: pendiente de envío"
    )


def test_single_curp_acknowledgement() -> None:
    text = build_request_acknowledgement(
        ["BEEJ760109HSLRSL03"]
    )

    assert "CURP: BEEJ760109HSLRSL03" in text


def test_multiple_identifiers() -> None:
    text = build_request_acknowledgement(
        [
            "VALA830403RA8",
            "RAHC850707NW3",
        ]
    )

    assert "✅ Solicitudes recibidas" in text
    assert "• VALA830403RA8" in text
    assert "• RAHC850707NW3" in text
    assert "Total: 2" in text


def test_duplicate_identifiers_are_removed() -> None:
    text = build_request_acknowledgement(
        [
            "VALA830403RA8",
            "VALA830403RA8",
        ]
    )

    assert text.count("VALA830403RA8") == 1


def test_empty_acknowledgement() -> None:
    assert build_request_acknowledgement([]) == ""
