from datetime import UTC, datetime, timedelta

from app.services.batch_scheduler_service import (
    calculate_batch_due,
    calculate_window,
)


NOW = datetime(
    2026,
    8,
    6,
    19,
    0,
    tzinfo=UTC,
)


def test_not_due_before_interval() -> None:
    due_interval, due_max = (
        calculate_batch_due(
            now=NOW,
            oldest_pending_at=(
                NOW - timedelta(minutes=14)
            ),
            pending_count=2,
            interval_minutes=15,
            max_items=50,
        )
    )

    assert due_interval is False
    assert due_max is False


def test_due_when_interval_is_reached() -> None:
    due_interval, due_max = (
        calculate_batch_due(
            now=NOW,
            oldest_pending_at=(
                NOW - timedelta(minutes=15)
            ),
            pending_count=2,
            interval_minutes=15,
            max_items=50,
        )
    )

    assert due_interval is True
    assert due_max is False


def test_due_immediately_by_max_items() -> None:
    due_interval, due_max = (
        calculate_batch_due(
            now=NOW,
            oldest_pending_at=(
                NOW - timedelta(minutes=1)
            ),
            pending_count=50,
            interval_minutes=15,
            max_items=50,
        )
    )

    assert due_interval is False
    assert due_max is True


def test_due_by_both_conditions() -> None:
    due_interval, due_max = (
        calculate_batch_due(
            now=NOW,
            oldest_pending_at=(
                NOW - timedelta(minutes=30)
            ),
            pending_count=60,
            interval_minutes=15,
            max_items=50,
        )
    )

    assert due_interval is True
    assert due_max is True


def test_naive_datetime_is_supported() -> None:
    naive_oldest = datetime(
        2026,
        8,
        6,
        18,
        30,
    )

    due_interval, _ = (
        calculate_batch_due(
            now=NOW,
            oldest_pending_at=naive_oldest,
            pending_count=1,
            interval_minutes=15,
            max_items=50,
        )
    )

    assert due_interval is True



def test_global_window_uses_fixed_ten_minutes() -> None:
    value = datetime(
        2026,
        8,
        11,
        7,
        14,
        59,
        tzinfo=UTC,
    )

    start, end = calculate_window(
        value
    )

    assert start == datetime(
        2026,
        8,
        11,
        7,
        10,
        tzinfo=UTC,
    )

    assert end == datetime(
        2026,
        8,
        11,
        7,
        20,
        tzinfo=UTC,
    )


def test_next_global_window_starts_at_boundary() -> None:
    value = datetime(
        2026,
        8,
        11,
        7,
        20,
        0,
        tzinfo=UTC,
    )

    start, end = calculate_window(
        value
    )

    assert start == datetime(
        2026,
        8,
        11,
        7,
        20,
        tzinfo=UTC,
    )

    assert end == datetime(
        2026,
        8,
        11,
        7,
        30,
        tzinfo=UTC,
    )
