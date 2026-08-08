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
class ProviderBatchState:
    provider_id: int
    provider_name: str

    interval_minutes: int
    max_items: int

    client_ids: tuple[int, ...]
    client_names: tuple[str, ...]

    pending_count: int
    oldest_pending_at: datetime

    due_by_interval: bool
    due_by_max_items: bool

    @property
    def is_due(self) -> bool:
        return (
            self.due_by_interval
            or self.due_by_max_items
        )

    @property
    def key(self) -> tuple[int, int]:
        return (
            self.provider_id,
            self.interval_minutes,
        )


@dataclass
class SchedulerRunResult:
    checked_windows: int = 0
    due_windows: int = 0

    created_batch_ids: list[int] = field(
        default_factory=list
    )

    sent_batch_ids: list[int] = field(
        default_factory=list
    )

    skipped_locked_windows: list[str] = field(
        default_factory=list
    )

    errors: list[str] = field(
        default_factory=list
    )


def ensure_aware_utc(
    value: datetime,
) -> datetime:
    if value.tzinfo is None:
        return value.replace(
            tzinfo=UTC
        )

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
        >= timedelta(
            minutes=interval
        )
    )

    due_by_max_items = (
        int(pending_count)
        >= maximum
    )

    return (
        due_by_interval,
        due_by_max_items,
    )


def get_provider_batch_states(
    db: Session,
    *,
    now: datetime | None = None,
) -> list[ProviderBatchState]:
    current_time = (
        now
        or datetime.now(UTC)
    )

    clients = list(
        db.scalars(
            select(Client)
            .where(
                Client.active.is_(True),
                Client.batch_enabled.is_(True),
                Client.deleted_at.is_(None),
                Client.default_provider_id
                .is_not(None),
            )
            .order_by(
                Client.id.asc()
            )
        )
    )

    # Agrupamos por proveedor + intervalo.
    #
    # Si dos clientes tienen el mismo
    # proveedor y el mismo intervalo,
    # comparten el mismo lote.
    #
    # El máximo será el menor
    # batch_max_items configurado.

    groups: dict[
        tuple[int, int],
        list[Client],
    ] = {}

    for client in clients:
        provider_id = (
            client.default_provider_id
        )

        if provider_id is None:
            continue

        interval = max(
            int(
                client
                .batch_interval_minutes
            ),
            1,
        )

        key = (
            provider_id,
            interval,
        )

        groups.setdefault(
            key,
            [],
        ).append(client)

    states: list[
        ProviderBatchState
    ] = []

    for (
        provider_id,
        interval_minutes,
    ), grouped_clients in groups.items():

        provider = db.get(
            Provider,
            provider_id,
        )

        if (
            provider is None
            or not provider.active
            or provider.deleted_at
            is not None
        ):
            continue

        client_ids = tuple(
            client.id
            for client
            in grouped_clients
        )

        client_names = tuple(
            client.name
            for client
            in grouped_clients
        )

        max_items = min(
            max(
                int(
                    client
                    .batch_max_items
                ),
                1,
            )
            for client
            in grouped_clients
        )

        aggregate = db.execute(
            select(
                func.count(
                    Request.id
                ),
                func.min(
                    Request.received_at
                ),
            ).where(
                Request.client_id.in_(
                    client_ids
                ),
                Request.provider_id
                == provider.id,
                Request.status
                == "PENDING_BATCH",
                Request.rfc.is_not(
                    None
                ),
            )
        ).one()

        pending_count = int(
            aggregate[0]
            or 0
        )

        oldest_pending_at = (
            aggregate[1]
        )

        if (
            pending_count <= 0
            or oldest_pending_at
            is None
        ):
            continue

        (
            due_by_interval,
            due_by_max_items,
        ) = calculate_batch_due(
            now=current_time,
            oldest_pending_at=
                oldest_pending_at,
            pending_count=
                pending_count,
            interval_minutes=
                interval_minutes,
            max_items=
                max_items,
        )

        states.append(
            ProviderBatchState(
                provider_id=
                    provider.id,
                provider_name=
                    provider.name,
                interval_minutes=
                    interval_minutes,
                max_items=
                    max_items,
                client_ids=
                    client_ids,
                client_names=
                    client_names,
                pending_count=
                    pending_count,
                oldest_pending_at=
                    oldest_pending_at,
                due_by_interval=
                    due_by_interval,
                due_by_max_items=
                    due_by_max_items,
            )
        )

    return states


# Compatibilidad temporal con código
# que todavía importe este nombre.
def get_client_batch_states(
    db: Session,
    *,
    now: datetime | None = None,
) -> list[ProviderBatchState]:
    return get_provider_batch_states(
        db,
        now=now,
    )


async def process_due_batches(
    db: Session,
) -> SchedulerRunResult:
    result = SchedulerRunResult()

    states = (
        get_provider_batch_states(
            db
        )
    )

    result.checked_windows = len(
        states
    )

    for state in states:
        if not state.is_due:
            continue

        result.due_windows += 1

        lock_name = (
            "whatsapp-lotes:"
            "batch-provider-window:"
            f"{state.provider_id}:"
            f"{state.interval_minutes}"
        )

        lock = redis_client.lock(
            lock_name,
            timeout=180,
            blocking_timeout=0,
        )

        acquired = False

        try:
            acquired = bool(
                lock.acquire(
                    blocking=False
                )
            )

            if not acquired:
                result\
                    .skipped_locked_windows\
                    .append(
                        (
                            f"{state.provider_id}:"
                            f"{state.interval_minutes}"
                        )
                    )

                continue

            # Refrescamos dentro del lock.
            refreshed_states = {
                item.key: item
                for item
                in get_provider_batch_states(
                    db
                )
            }

            refreshed = (
                refreshed_states.get(
                    state.key
                )
            )

            if (
                refreshed is None
                or not refreshed.is_due
            ):
                continue

            creation = (
                create_pending_batch(
                    db,
                    provider_id=
                        refreshed
                        .provider_id,
                    client_ids=list(
                        refreshed
                        .client_ids
                    ),
                    max_items=
                        refreshed
                        .max_items,
                )
            )

            result\
                .created_batch_ids\
                .append(
                    creation.batch_id
                )

            send_result = (
                await send_existing_batch(
                    db,
                    batch_id=
                        creation.batch_id,
                )
            )

            result\
                .sent_batch_ids\
                .append(
                    send_result.batch_id
                )

            logger.info(
                (
                    "Lote conjunto enviado "
                    "batch_id=%s "
                    "provider_id=%s "
                    "interval=%s "
                    "clients=%s "
                    "requests=%s"
                ),
                creation.batch_id,
                refreshed.provider_id,
                refreshed.interval_minutes,
                list(
                    refreshed.client_ids
                ),
                len(
                    creation.request_ids
                ),
            )

        except BatchServiceError as error:
            db.rollback()

            message = (
                "provider="
                f"{state.provider_id} "
                "interval="
                f"{state.interval_minutes}: "
                f"{error}"
            )

            result.errors.append(
                message
            )

            logger.exception(
                "Error procesando lote "
                "conjunto: %s",
                message,
            )

        except Exception as error:
            db.rollback()

            message = (
                "provider="
                f"{state.provider_id} "
                "interval="
                f"{state.interval_minutes}: "
                f"{error}"
            )

            result.errors.append(
                message
            )

            logger.exception(
                "Error inesperado procesando "
                "lote conjunto: %s",
                message,
            )

        finally:
            if acquired:
                try:
                    lock.release()
                except Exception:
                    logger.exception(
                        "No se pudo liberar "
                        "lock provider=%s "
                        "interval=%s",
                        state.provider_id,
                        state.interval_minutes,
                    )

    return result
