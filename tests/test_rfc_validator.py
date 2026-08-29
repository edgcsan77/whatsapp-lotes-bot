from app.services.rfc_validator import (
    extract_rfc_like_candidates,
    validate_rfc_format,
)


def test_accepts_valid_physical_rfc() -> None:
    valid, reason = validate_rfc_format(
        "VALA830403RA8"
    )

    assert valid is True
    assert reason == ""


def test_accepts_valid_moral_rfc() -> None:
    valid, reason = validate_rfc_format(
        "ABC830403AB1"
    )

    assert valid is True
    assert reason == ""


def test_detects_missing_rfc_character() -> None:
    value = "CAHO070306HJ"

    candidates = (
        extract_rfc_like_candidates(
            value
        )
    )

    assert candidates == [value]

    valid, _ = validate_rfc_format(
        value
    )

    assert valid is False


def test_rejects_impossible_rfc_date() -> None:
    valid, reason = validate_rfc_format(
        "CAHO990231ABC"
    )

    assert valid is False
    assert "fecha" in reason.lower()


def test_does_not_classify_curp_as_rfc() -> None:
    candidates = (
        extract_rfc_like_candidates(
            "CAH0070306HJCHRMA5"
        )
    )

    assert candidates == []
