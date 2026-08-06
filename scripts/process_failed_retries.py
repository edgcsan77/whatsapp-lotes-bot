import asyncio
import json

from app.database import SessionLocal
from app.services.retry_service import (
    process_failed_retries,
)


async def main() -> None:
    with SessionLocal() as db:
        result = await process_failed_retries(
            db
        )

        print(
            json.dumps(
                {
                    "checked_batch_failures":
                        result
                        .checked_batch_failures,
                    "checked_delivery_failures":
                        result
                        .checked_delivery_failures,
                    "retried_batch_ids":
                        result.retried_batch_ids,
                    "recovered_batch_ids":
                        result.recovered_batch_ids,
                    "exhausted_batch_ids":
                        result.exhausted_batch_ids,
                    "retried_request_ids":
                        result.retried_request_ids,
                    "recovered_request_ids":
                        result
                        .recovered_request_ids,
                    "exhausted_request_ids":
                        result
                        .exhausted_request_ids,
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
    asyncio.run(main())
