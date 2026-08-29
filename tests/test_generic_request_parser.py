import pytest

from app.services.generic_request_parser import (
    extract_generic_requests,
    strip_generic_requests,
)


CURP = "CASE020722HTSRNDA8"
RFC = "VALA830403RA8"


@pytest.mark.parametrize(
    "suffix",
    [
        "-G",
        " -G",
        "- G",
        " - G",
        "–G",
        " – G",
        "—G",
        " — G",
    ],
)
def test_accepts_all_generic_suffixes(
    suffix: str,
) -> None:
    parsed = extract_generic_requests(
        f"{CURP}{suffix}"
    )

    assert len(parsed) == 1

    assert (
        parsed[0].identifier
        == CURP
    )

    assert (
        parsed[0].identifier_type
        == "CURP"
    )

    assert (
        parsed[0].lookup_route
        == "CURP_NL_SEPOMEX_NO_CHECKID"
    )


def test_rfc_generic_uses_checkid() -> None:
    parsed = extract_generic_requests(
        f"{RFC} - G"
    )

    assert len(parsed) == 1

    assert (
        parsed[0].identifier
        == RFC
    )

    assert (
        parsed[0].identifier_type
        == "RFC"
    )

    assert (
        parsed[0].lookup_route
        == "RFC_CHECKID"
    )


def test_mixed_message_keeps_localization() -> None:
    text = (
        f"{CURP}-G\n"
        f"{RFC}"
    )

    stripped = strip_generic_requests(
        text
    )

    assert CURP not in stripped
    assert RFC in stripped


def test_duplicates_are_removed() -> None:
    parsed = extract_generic_requests(
        f"{RFC}-G\n"
        f"{RFC} - G"
    )

    assert len(parsed) == 1
