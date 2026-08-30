import pytest

from app.services.direct_request_parser import (
    extract_direct_requests,
    strip_direct_requests,
)


RFC = "EUPA031107864"
IDCIF = "24070438891"


@pytest.mark.parametrize(
    "text",
    [
        f"{RFC} {IDCIF}",
        f"{IDCIF} {RFC}",
        f"{RFC}\n{IDCIF}",
        f"{IDCIF}\n{RFC}",
        f"RFC: {RFC} IDCIF: {IDCIF}",
        f"IDCIF: {IDCIF} RFC: {RFC}",
        f"RFC: {RFC}\nIDCIF: {IDCIF}",
        f"IDCIF: {IDCIF}\nRFC: {RFC}",
        f"{RFC} | {IDCIF}",
        f"{IDCIF} | {RFC}",
        f"{RFC}, {IDCIF}",
        f"{IDCIF}; {RFC}",
    ],
)
def test_accepts_both_orders(
    text: str,
) -> None:
    items = extract_direct_requests(text)

    assert len(items) == 1
    assert items[0].rfc == RFC
    assert items[0].idcif == IDCIF
    assert (
        items[0].display_identifier
        == f"{RFC} {IDCIF}"
    )


def test_mixed_multiple_pairs_both_orders() -> None:
    text = (
        "EUPA031107864 24070438891\n"
        "16275230322 MATL941108QM3"
    )

    items = extract_direct_requests(text)

    assert [
        (item.rfc, item.idcif)
        for item in items
    ] == [
        (
            "EUPA031107864",
            "24070438891",
        ),
        (
            "MATL941108QM3",
            "16275230322",
        ),
    ]


def test_reverse_pair_is_removed_before_localization() -> None:
    text = (
        "24070438891 EUPA031107864\n"
        "MATL941108QM3"
    )

    stripped = strip_direct_requests(text)

    assert "24070438891" not in stripped
    assert "EUPA031107864" not in stripped
    assert "MATL941108QM3" in stripped


def test_rfc_alone_is_not_direct() -> None:
    assert (
        extract_direct_requests(
            RFC
        )
        == []
    )


def test_idcif_alone_is_not_direct() -> None:
    assert (
        extract_direct_requests(
            IDCIF
        )
        == []
    )
