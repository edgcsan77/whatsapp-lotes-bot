import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.database import SessionLocal
from app.models.client import Client
from app.services.daily_cutoff_service import (
    calculate_cutoff_totals,
    calculate_next_cutoff_period,
    find_existing_cutoff,
    get_requests_for_period,
    render_daily_cutoff_message,
)


def main() -> None:
    now_utc = datetime.now(UTC)

    output: dict = {
        "now_utc": now_utc.isoformat(),
        "checked_clients": 0,
        "previews": [],
        "errors": [],
    }

    with SessionLocal() as db:
        clients = list(
            db.scalars(
                select(Client)
                .where(
                    Client.active.is_(True),
                    Client.daily_cutoff_enabled.is_(True),
                )
                .order_by(Client.id)
            )
        )

        output["checked_clients"] = len(
            clients
        )

        for client in clients:
            try:
                period = (
                    calculate_next_cutoff_period(
                        db,
                        client=client,
                        now_utc=now_utc,
                    )
                )

                if period is None:
                    output["previews"].append(
                        {
                            "client_id": client.id,
                            "client_name": client.name,
                            "already_covered": True,
                        }
                    )
                    continue

                existing = find_existing_cutoff(
                    db,
                    client_id=client.id,
                    period=period,
                )

                requests = get_requests_for_period(
                    db,
                    client_id=client.id,
                    period=period,
                )

                totals = calculate_cutoff_totals(
                    requests
                )

                message = (
                    render_daily_cutoff_message(
                        client=client,
                        period=period,
                        totals=totals,
                    )
                )

                output["previews"].append(
                    {
                        "client_id": client.id,
                        "client_name": client.name,
                        "already_exists": (
                            existing is not None
                        ),
                        "period_start": (
                            period.start_local.isoformat()
                        ),
                        "period_end": (
                            period.end_local.isoformat()
                        ),
                        "total_requests": (
                            totals.total_requests
                        ),
                        "message": message,
                    }
                )

            except Exception as exc:
                output["errors"].append(
                    {
                        "client_id": client.id,
                        "error": str(exc),
                    }
                )

    print(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
