import argparse
import asyncio
import json

from app.database import SessionLocal
from app.services.batch_scheduler_service import (
    get_provider_batch_states,
    process_due_batches,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Revisa y envía los lotes "
            "agrupados por proveedor "
            "e intervalo."
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
            states = (
                get_provider_batch_states(
                    db
                )
            )

            output = []

            for state in states:
                output.append(
                    {
                        "provider_id":
                            state.provider_id,
                        "provider_name":
                            state.provider_name,
                        "interval_minutes":
                            state.interval_minutes,
                        "max_items":
                            state.max_items,
                        "client_ids":
                            list(
                                state.client_ids
                            ),
                        "client_names":
                            list(
                                state.client_names
                            ),
                        "pending_count":
                            state.pending_count,
                        "oldest_pending_at":
                            state
                            .oldest_pending_at
                            .isoformat(),
                        "due_by_interval":
                            state
                            .due_by_interval,
                        "due_by_max_items":
                            state
                            .due_by_max_items,
                        "is_due":
                            state.is_due,
                    }
                )

            print(
                json.dumps(
                    output,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            return

        result = (
            await process_due_batches(
                db
            )
        )

        print(
            json.dumps(
                {
                    "checked_windows":
                        result
                        .checked_windows,
                    "due_windows":
                        result
                        .due_windows,
                    "created_batch_ids":
                        result
                        .created_batch_ids,
                    "sent_batch_ids":
                        result
                        .sent_batch_ids,
                    "skipped_locked_windows":
                        result
                        .skipped_locked_windows,
                    "errors":
                        result.errors,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
