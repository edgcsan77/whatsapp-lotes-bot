import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.provider import Provider
from app.models.request import Request
from app.redis_client import redis_client
from app.services.batch_service import (
    BatchServiceError,
    create_pending_batch,
    send_existing_batch,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientBatchState:
    client_id: int
    client_name: str
    provider_id: int
    provider_name: str
    pending_count: int
    oldest_pending_at: datetime
    interval_minutes: int
    max_items: int
    due_by_interval: bool
    due_by_max_items: bool

    @property
    def is_due(self) -> bool:
        return (
            self.due_by_interval
            or self.due_by_max_items
        )


@dataclass
class SchedulerRunResult:
    checked_clients: int = 0
    due_clients: int = 0
    created_batch_ids: list[int] = field(
        default_factory=list
    )
    sent_batch_ids: list[int] = field(
        default_factory=list
    )
    skipped_locked_client_ids: list[int] = field(
        default_factory=list
    )
    errors: list[str] = field(
        default_factory=list
    )


def ensure_aware_utc(
    value: datetime,
) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def calculate_batch_due(
    *,
    now: datetime,
    oldest_pending_at: datetime,
    pending_count: int,
    interval_minutes: int,
    max_items: int,
) -> tuple[bool, bool]:
    now_utc = ensure_aware_utc(now)
    oldest_utc = ensure_aware_utc(
        oldest_pending_at
    )

    interval = max(
        int(interval_minutes),
        1,
    )

    maximum = max(
        int(max_items),
        1,
    )

    due_by_interval = (
        now_utc - oldest_utc
        >= timedelta(minutes=interval)
    )

    due_by_max_items = (
        int(pending_count) >= maximum
    )

    return (
        due_by_interval,
        due_by_max_items,
    )


def get_client_batch_states(
    db: Session,
    *,
    now: datetime | None = None,
) -> list[ClientBatchState]:
    current_time = now or datetime.now(UTC)

    clients = list(
        db.scalars(
            select(Client)
            .where(
                Client.active.is_(True),
                Client.batch_enabled.is_(True),
                Client.default_provider_id.is_not(
                    None
                ),
            )
            .order_by(Client.id.asc())
        )
    )

    states: list[ClientBatchState] = []

    for client in clients:
        provider = db.get(
            Provider,
            client.default_provider_id,
        )

        if provider is None or not provider.active:
            continue

        aggregate = db.execute(
            select(
                func.count(Request.id),
                func.min(Request.received_at),
            ).where(
                Request.client_id == client.id,
                Request.provider_id == provider.id,
                Request.status == "PENDING_BATCH",
                Request.rfc.is_not(None),
            )
        ).one()

        pending_count = int(
            aggregate[0] or 0
        )

        oldest_pending_at = aggregate[1]

        if (
            pending_count <= 0
            or oldest_pending_at is None
        ):
            continue

        (
            due_by_interval,
            due_by_max_items,
        ) = calculate_batch_due(
            now=current_time,
            oldest_pending_at=
                oldest_pending_at,
            pending_count=pending_count,
            interval_minutes=
                client.batch_interval_minutes,
            max_items=client.batch_max_items,
        )

        states.append(
            ClientBatchState(
                client_id=client.id,
                client_name=client.name,
                provider_id=provider.id,
                provider_name=provider.name,
                pending_count=pending_count,
                oldest_pending_at=
                    oldest_pending_at,
                interval_minutes=
                    client.batch_interval_minutes,
                max_items=
                    client.batch_max_items,
                due_by_interval=
                    due_by_interval,
                due_by_max_items=
                    due_by_max_items,
            )
        )

    return states


async def process_due_batches(
    db: Session,
) -> SchedulerRunResult:
    result = SchedulerRunResult()

    states = get_client_batch_states(db)

    result.checked_clients = len(states)

    for state in states:
        if not state.is_due:
            continue

        result.due_clients += 1

        lock_name = (
            "whatsapp-lotes:"
            f"batch-client:{state.client_id}"
        )

        lock = redis_client.lock(
            lock_name,
            timeout=180,
            blocking_timeout=0,
        )

        acquired = False

        try:
            acquired = bool(
                lock.acquire(blocking=False)
            )

            if not acquired:
                result\
                    .skipped_locked_client_ids\
                    .append(state.client_id)

                continue

            # Volvemos a revisar después de adquirir
            # el bloqueo, por si otro proceso ya actuó.
            refreshed_states = {
                item.client_id: item
                for item in get_client_batch_states(
                    db
                )
            }

            refreshed = refreshed_states.get(
                state.client_id
            )

            if (
                refreshed is None
                or not refreshed.is_due
            ):
                continue

            creation = create_pending_batch(
                db,
                provider_id=
                    refreshed.provider_id,
                client_id=
                    refreshed.client_id,
                max_items=
                    refreshed.max_items,
            )

            result.created_batch_ids.append(
                creation.batch_id
            )

            send_result = (
                await send_existing_batch(
                    db,
                    batch_id=creation.batch_id,
                )
            )

            result.sent_batch_ids.append(
                send_result.batch_id
            )

            logger.info(
                "Lote automático enviado: "
                "batch_id=%s client_id=%s "
                "provider_id=%s requests=%s",
                send_result.batch_id,
                refreshed.client_id,
                refreshed.provider_id,
                send_result.request_count,
            )

        except BatchServiceError as error:
            error_text = (
                f"client_id={state.client_id} "
                f"batch_error={error}"
            )

            result.errors.append(error_text)
            logger.exception(error_text)

        except Exception as error:
            error_text = (
                f"client_id={state.client_id} "
                f"unexpected_error="
                f"{type(error).__name__}:{error}"
            )

            result.errors.append(error_text)
            logger.exception(error_text)

        finally:
            if acquired:
                try:
                    lock.release()
                except Exception:
                    logger.exception(
                        "No se pudo liberar bloqueo "
                        "Redis para client_id=%s",
                        state.client_id,
                    )

    return result
