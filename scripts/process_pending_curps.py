import asyncio
import argparse
import json

from app.database import SessionLocal
from app.services.curp_processing_service import (
    process_pending_curps,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Consulta CURP pendientes y "
            "las convierte a RFC."
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=3,
    )

    return parser.parse_args()


async def main() -> None:
    args = parse_arguments()

    limit = max(
        1,
        min(args.limit, 20),
    )

    with SessionLocal() as db:
        result = await process_pending_curps(
            db,
            limit=limit,
        )

        print(
            json.dumps(
                {
                    "checked_requests":
                        result.checked_requests,
                    "processed_request_ids":
                        result
                        .processed_request_ids,
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
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
