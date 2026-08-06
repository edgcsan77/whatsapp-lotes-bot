import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.database import SessionLocal
from app.models.client import Client
from app.services.daily_cutoff_service import (
    calculate_cutoff_totals,
    calculate_latest_cutoff_period,
    create_daily_cutoff,
    find_existing_cutoff,
    get_requests_for_period,
    render_daily_cutoff_message,
    send_daily_cutoff,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Procesa cortes diarios de clientes."
        )
    )

    parser.add_argument(
        "--send",
        action="store_true",
        help=(
            "Crea y envía cortes. "
            "Sin esta opción solo simula."
        ),
    )

    return parser.parse_args()


async def process_cutoffs(
    *,
    send_enabled: bool,
) -> dict:
    now_utc = datetime.now(UTC)

    result: dict = {
        "now_utc": now_utc.isoformat(),
        "mode": (
            "send"
            if send_enabled
            else "preview"
        ),
        "checked_clients": 0,
        "empty_client_ids": [],
        "already_sent_cutoff_ids": [],
        "previewed_client_ids": [],
        "created_cutoff_ids": [],
        "sent_cutoff_ids": [],
        "failed_cutoff_ids": [],
        "errors": [],
    }

    with SessionLocal() as db:
        clients = list(
            db.scalars(
                select(Client)
                .where(
                    Client.active.is_(True),
                    Client.daily_cutoff_enabled.is_(
                        True
                    ),
                )
                .order_by(Client.id)
            )
        )

        result["checked_clients"] = len(
            clients
        )

        for client in clients:
            try:
                period = (
                    calculate_latest_cutoff_period(
                        now_utc=now_utc,
                        cutoff_time_value=(
                            client.daily_cutoff_time
                        ),
                        timezone_name=(
                            client.timezone
                        ),
                    )
                )

                existing = find_existing_cutoff(
                    db,
                    client_id=client.id,
                    period=period,
                )

                if (
                    existing is not None
                    and existing.status == "SENT"
                ):
                    result[
                        "already_sent_cutoff_ids"
                    ].append(existing.id)
                    continue

                requests = get_requests_for_period(
                    db,
                    client_id=client.id,
                    period=period,
                )

                totals = calculate_cutoff_totals(
                    requests
                )

                if totals.total_requests == 0:
                    result[
                        "empty_client_ids"
                    ].append(client.id)
                    continue

                message = (
                    render_daily_cutoff_message(
                        client=client,
                        period=period,
                        totals=totals,
                    )
                )

                if not send_enabled:
                    result[
                        "previewed_client_ids"
                    ].append(client.id)

                    result.setdefault(
                        "previews",
                        [],
                    ).append(
                        {
                            "client_id": client.id,
                            "client_name": (
                                client.name
                            ),
                            "period_start": (
                                period.start_local
                                .isoformat()
                            ),
                            "period_end": (
                                period.end_local
                                .isoformat()
                            ),
                            "total_requests": (
                                totals.total_requests
                            ),
                            "total_amount": str(
                                totals.total_amount
                            ),
                            "message": message,
                        }
                    )
                    continue

                cutoff, created = (
                    create_daily_cutoff(
                        db,
                        client=client,
                        now_utc=now_utc,
                    )
                )

                if created:
                    result[
                        "created_cutoff_ids"
                    ].append(cutoff.id)

                cutoff = await send_daily_cutoff(
                    db,
                    cutoff=cutoff,
                    client=client,
                    message=message,
                )

                if cutoff.status == "SENT":
                    result[
                        "sent_cutoff_ids"
                    ].append(cutoff.id)
                else:
                    result[
                        "failed_cutoff_ids"
                    ].append(cutoff.id)

            except Exception as exc:
                db.rollback()

                result["errors"].append(
                    {
                        "client_id": client.id,
                        "error": str(exc),
                    }
                )

    return result


def main() -> None:
    args = parse_args()

    result = asyncio.run(
        process_cutoffs(
            send_enabled=args.send,
        )
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
