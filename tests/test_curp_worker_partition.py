import pytest

from app.services.curp_processing_service import (
    global_curp_lock_key,
    normalize_worker_partition,
)


def test_single_worker_keeps_old_lock_key() -> None:
    assert global_curp_lock_key() == (
        "whatsapp-lotes:"
        "curp-lock:global"
    )


def test_two_workers_have_different_locks() -> None:
    key_zero = global_curp_lock_key(
        worker_slot=0,
        worker_count=2,
    )

    key_one = global_curp_lock_key(
        worker_slot=1,
        worker_count=2,
    )

    assert key_zero != key_one
    assert key_zero.endswith(":2:0")
    assert key_one.endswith(":2:1")


def test_invalid_worker_slot_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_worker_partition(
            worker_slot=2,
            worker_count=2,
        )
