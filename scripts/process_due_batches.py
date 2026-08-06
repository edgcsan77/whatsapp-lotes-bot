import argparse
import asyncio
import json

from app.database import SessionLocal
from app.services.batch_scheduler_service import (
    get_client_batch_states,
    process_due_batches,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Revisa y envía los lotes que "
            "ya cumplieron su intervalo o máximo."
        )
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Solo muestra el estado; "
            "no crea ni envía lotes."
        ),
    )

    return parser.parse_args()


async def main() -> None:
    args = parse_arguments()

    with SessionLocal() as db:
        if args.dry_run:
            states = get_client_batch_states(
                db
            )

            output = []

            for state in states:
                output.append({
                    "client_id":
                        state.client_id,
                    "client_name":
                        state.client_name,
                    "provider_id":
                        state.provider_id,
                    "provider_name":
                        state.provider_name,
                    "pending_count":
                        state.pending_count,
                    "oldest_pending_at":
                        state.oldest_pending_at.isoformat(),
                    "interval_minutes":
                        state.interval_minutes,
                    "max_items":
                        state.max_items,
                    "due_by_interval":
                        state.due_by_interval,
                    "due_by_max_items":
                        state.due_by_max_items,
                    "is_due":
                        state.is_due,
                })

            print(
                json.dumps(
                    output,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            return

        result = await process_due_batches(
            db
        )

        print(
            json.dumps(
                {
                    "checked_clients":
                        result.checked_clients,
                    "due_clients":
                        result.due_clients,
                    "created_batch_ids":
                        result.created_batch_ids,
                    "sent_batch_ids":
                        result.sent_batch_ids,
                    "skipped_locked_client_ids":
                        result
                        .skipped_locked_client_ids,
                    "errors":
                        result.errors,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
