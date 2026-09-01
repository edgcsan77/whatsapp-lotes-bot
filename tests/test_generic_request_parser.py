from app.services.generic_request_parser import (
    extract_generic_requests,
    strip_generic_requests,
)


CURP = "CASE020722HTSRNDA8"
RFC = "VALA830403RA8"


def test_curp_hash_is_generic() -> None:
    parsed = extract_generic_requests(
        f"{CURP}#"
    )

    assert len(parsed) == 1
    assert parsed[0].identifier == CURP
    assert parsed[0].identifier_type == "CURP"
    assert (
        parsed[0].lookup_route
        == "CURP_NL_SEPOMEX_NO_CHECKID"
    )
    assert (
        parsed[0].display_identifier
        == f"{CURP}#"
    )


def test_rfc_hash_uses_checkid() -> None:
    parsed = extract_generic_requests(
        f"{RFC}#"
    )

    assert len(parsed) == 1
    assert parsed[0].identifier == RFC
    assert parsed[0].identifier_type == "RFC"
    assert (
        parsed[0].lookup_route
        == "RFC_CHECKID"
    )
    assert (
        parsed[0].display_identifier
        == f"{RFC}#"
    )


def test_old_g_suffix_is_not_generic() -> None:
    old_values = [
        f"{CURP}-G",
        f"{CURP} -G",
        f"{CURP}- G",
        f"{CURP} - G",
        f"{RFC}-G",
        f"{RFC} - G",
    ]

    for value in old_values:
        assert extract_generic_requests(
            value
        ) == []


def test_plain_identifiers_are_not_generic() -> None:
    assert extract_generic_requests(
        CURP
    ) == []

    assert extract_generic_requests(
        RFC
    ) == []


def test_mixed_message_keeps_localization() -> None:
    text = (
        f"{CURP}#\n"
        f"{RFC}"
    )

    stripped = strip_generic_requests(
        text
    )

    assert CURP not in stripped
    assert RFC in stripped


def test_duplicates_are_removed() -> None:
    parsed = extract_generic_requests(
        f"{RFC}#\n"
        f"{RFC} #"
    )

    assert len(parsed) == 1


def test_curp_and_rfc_hash_can_coexist() -> None:
    parsed = extract_generic_requests(
        f"{CURP}#\n"
        f"{RFC}#"
    )

    assert len(parsed) == 2

    assert {
        item.identifier_type
        for item in parsed
    } == {
        "CURP",
        "RFC",
    }
