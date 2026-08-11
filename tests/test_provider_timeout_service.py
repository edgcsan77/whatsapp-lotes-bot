from datetime import UTC, datetime, timedelta

from app.services.provider_timeout_service import (
    is_provider_timeout_due,
)


NOW = datetime(
    2026,
    8,
    11,
    8,
    0,
    tzinfo=UTC,
)


def test_provider_timeout_not_due() -> None:
    sent_at = (
        NOW
        - timedelta(
            minutes=59
        )
    )

    assert (
        is_provider_timeout_due(
            sent_at=sent_at,
            timeout_minutes=60,
            now=NOW,
        )
        is False
    )


def test_provider_timeout_exact_boundary() -> None:
    sent_at = (
        NOW
        - timedelta(
            minutes=60
        )
    )

    assert (
        is_provider_timeout_due(
            sent_at=sent_at,
            timeout_minutes=60,
            now=NOW,
        )
        is True
    )


def test_provider_timeout_after_boundary() -> None:
    sent_at = (
        NOW
        - timedelta(
            minutes=90
        )
    )

    assert (
        is_provider_timeout_due(
            sent_at=sent_at,
            timeout_minutes=60,
            now=NOW,
        )
        is True
    )


def test_provider_timeout_uses_provider_setting() -> None:
    sent_at = (
        NOW
        - timedelta(
            minutes=31
        )
    )

    assert (
        is_provider_timeout_due(
            sent_at=sent_at,
            timeout_minutes=30,
            now=NOW,
        )
        is True
    )

    assert (
        is_provider_timeout_due(
            sent_at=sent_at,
            timeout_minutes=60,
            now=NOW,
        )
        is False
    )


def test_provider_timeout_handles_naive_datetime() -> None:
    sent_at = datetime(
        2026,
        8,
        11,
        6,
        0,
    )

    now = datetime(
        2026,
        8,
        11,
        7,
        0,
    )

    assert (
        is_provider_timeout_due(
            sent_at=sent_at,
            timeout_minutes=60,
            now=now,
        )
        is True
    )
