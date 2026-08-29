import argparse
import asyncio
import json

from app.database import SessionLocal
from app.services.pdf_processing_service import (
    process_pending_pdfs,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Genera y entrega constancias "
            "PDF pendientes."
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

    with SessionLocal() as db:
        result = await process_pending_pdfs(
            db,
            limit=args.limit,
        )

    print(
        json.dumps(
            {
                "checked_requests":
                    result.checked_requests,
                "generated_request_ids":
                    result.generated_request_ids,
                "delivered_request_ids":
                    result.delivered_request_ids,
                "retried_request_ids":
                    result.retried_request_ids,
                "failed_request_ids":
                    result.failed_request_ids,
                "skipped_request_ids":
                    result.skipped_request_ids,
                "errors":
                    result.errors,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
