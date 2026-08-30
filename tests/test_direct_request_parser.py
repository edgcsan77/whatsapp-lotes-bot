from app.services.direct_request_parser import (
    extract_direct_requests,
    strip_direct_requests,
)


RFC = "MATL941108QM3"
IDCIF = "16275230322"


def test_direct_parser_accepts_supported_forms() -> None:
    samples = [
        f"{RFC} {IDCIF}",
        f"{RFC} | {IDCIF}",
        f"{RFC}, {IDCIF}",
        f"{RFC}; {IDCIF}",
        f"RFC: {RFC} IDCIF: {IDCIF}",
        f"{RFC}\n{IDCIF}",
    ]

    for sample in samples:
        items = extract_direct_requests(sample)
        assert len(items) == 1
        assert items[0].rfc == RFC
        assert items[0].idcif == IDCIF


def test_direct_strip_prevents_localization_reparse() -> None:
    text = f"{RFC} {IDCIF}"
    stripped = strip_direct_requests(text)
    assert RFC not in stripped
    assert IDCIF not in stripped
