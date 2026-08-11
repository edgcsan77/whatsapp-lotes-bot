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
            "Procesa ventanas globales "
            "de 10 minutos por proveedor."
        )
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Solo muestra ventanas; "
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
                        "window_start":
                            state
                            .window_start
                            .isoformat(),
                        "window_end":
                            state
                            .window_end
                            .isoformat(),
                        "client_ids":
                            list(
                                state.client_ids
                            ),
                        "client_names":
                            list(
                                state.client_names
                            ),
                        "ready_count":
                            state.ready_count,
                        "waiting_curp_count":
                            state
                            .waiting_curp_count,
                        "failed_curp_count":
                            state
                            .failed_curp_count,
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

        result = await process_due_batches(
            db
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
                    "waiting_curp_windows":
                        result
                        .waiting_curp_windows,
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
