import argparse
import asyncio
import json
import time
from typing import Any

from app.database import SessionLocal
from app.services.curp_processing_service import (
    CurpProcessingRunResult,
    process_pending_curps,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Procesa CURP pendientes en "
            "carriles independientes."
        )
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--limit-per-worker",
        type=int,
        default=8,
    )

    return parser.parse_args()


def result_to_dict(
    *,
    slot: int,
    result: CurpProcessingRunResult,
) -> dict[str, Any]:
    return {
        "worker_slot": slot,
        "checked_requests":
            result.checked_requests,
        "processed_request_ids":
            result.processed_request_ids,
        "generated_rfcs":
            result.generated_rfcs,
        "corrected_curps":
            result.corrected_curps,
        "retried_request_ids":
            result.retried_request_ids,
        "failed_request_ids":
            result.failed_request_ids,
        "skipped_not_due":
            result.skipped_not_due,
        "skipped_locked":
            result.skipped_locked,
        "errors":
            result.errors,
    }


def run_worker(
    *,
    slot: int,
    workers: int,
    limit: int,
) -> dict[str, Any]:
    with SessionLocal() as db:
        result = asyncio.run(
            process_pending_curps(
                db,
                limit=limit,
                worker_slot=slot,
                worker_count=workers,
            )
        )

    return result_to_dict(
        slot=slot,
        result=result,
    )


async def main() -> None:
    args = parse_arguments()

    workers = max(
        1,
        min(int(args.workers), 4),
    )

    limit = max(
        1,
        min(
            int(args.limit_per_worker),
            12,
        ),
    )

    started = time.monotonic()

    raw_results = await asyncio.gather(
        *(
            asyncio.to_thread(
                run_worker,
                slot=slot,
                workers=workers,
                limit=limit,
            )
            for slot in range(workers)
        ),
        return_exceptions=True,
    )

    worker_results: list[
        dict[str, Any]
    ] = []

    errors: list[str] = []

    for slot, item in enumerate(
        raw_results
    ):
        if isinstance(
            item,
            Exception,
        ):
            errors.append(
                f"worker_slot={slot} "
                f"{type(item).__name__}:"
                f"{item}"
            )
            continue

        worker_results.append(item)

    output = {
        "workers": workers,
        "limit_per_worker": limit,
        "duration_seconds": round(
            time.monotonic() - started,
            3,
        ),
        "worker_results":
            worker_results,
        "errors":
            errors,
    }

    print(
        json.dumps(
            output,
            ensure_ascii=False,
        )
    )

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
