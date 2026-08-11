import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
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


# Ventana GLOBAL.
#
# 19:00–19:10
# 19:10–19:20
# 19:20–19:30
#
# Ya NO depende del intervalo configurado
# individualmente en cada cliente.
GLOBAL_BATCH_INTERVAL_MINUTES = 10


WAITING_CURP_STATUSES = (
    "PENDING_CURP_LOOKUP",
    "CURP_LOOKUP_RETRY",
)

OPEN_WINDOW_STATUSES = (
    "PENDING_BATCH",
    *WAITING_CURP_STATUSES,
)


@dataclass(frozen=True)
class ProviderBatchState:
    provider_id: int
    provider_name: str

    interval_minutes: int

    window_start: datetime
    window_end: datetime

    client_ids: tuple[int, ...]
    client_names: tuple[str, ...]

    ready_count: int
    waiting_curp_count: int
    failed_curp_count: int

    @property
    def pending_count(self) -> int:
        return (
            self.ready_count
            + self.waiting_curp_count
        )

    @property
    def max_items(self) -> int:
        # 0 = sin máximo por cantidad.
        return 0

    @property
    def oldest_pending_at(self) -> datetime:
        return self.window_start

    @property
    def due_by_interval(self) -> bool:
        return (
            datetime.now(UTC)
            >= ensure_aware_utc(
                self.window_end
            )
        )

    @property
    def due_by_max_items(self) -> bool:
        return False

    @property
    def is_due(self) -> bool:
        return (
            self.due_by_interval
            and self.waiting_curp_count == 0
            and self.ready_count > 0
        )

    @property
    def key(
        self,
    ) -> tuple[int, datetime]:
        return (
            self.provider_id,
            ensure_aware_utc(
                self.window_start
            ),
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

    waiting_curp_windows: list[str] = field(
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


def calculate_window(
    value: datetime,
) -> tuple[datetime, datetime]:
    value = ensure_aware_utc(value)

    interval = (
        GLOBAL_BATCH_INTERVAL_MINUTES
    )

    start_minute = (
        value.minute
        // interval
        * interval
    )

    window_start = value.replace(
        minute=start_minute,
        second=0,
        microsecond=0,
    )

    window_end = (
        window_start
        + timedelta(
            minutes=interval
        )
    )

    return (
        window_start,
        window_end,
    )


# Se conserva por compatibilidad con tests/código
# previo, pero el scheduler nuevo ya NO usa
# máximo por lote.
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

    due_by_interval = (
        now_utc - oldest_utc
        >= timedelta(
            minutes=max(
                int(interval_minutes),
                1,
            )
        )
    )

    due_by_max_items = (
        int(pending_count)
        >= max(
            int(max_items),
            1,
        )
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
    current_time = ensure_aware_utc(
        now or datetime.now(UTC)
    )

    providers = list(
        db.scalars(
            select(Provider)
            .where(
                Provider.active.is_(True),
                Provider.deleted_at.is_(None),
            )
            .order_by(
                Provider.priority.asc(),
                Provider.id.asc(),
            )
        )
    )

    states: list[
        ProviderBatchState
    ] = []

    for provider in providers:
        # Encontramos la solicitud ABIERTA más
        # antigua de este proveedor.
        oldest_request = db.scalar(
            select(Request)
            .where(
                Request.provider_id
                == provider.id,
                Request.status.in_(
                    OPEN_WINDOW_STATUSES
                ),
            )
            .order_by(
                Request.received_at.asc(),
                Request.id.asc(),
            )
            .limit(1)
        )

        if oldest_request is None:
            continue

        (
            window_start,
            window_end,
        ) = calculate_window(
            oldest_request.received_at
        )

        # Todo lo recibido dentro de ESTA
        # ventana pertenece al mismo lote.
        window_requests = list(
            db.scalars(
                select(Request)
                .where(
                    Request.provider_id
                    == provider.id,
                    Request.received_at
                    >= window_start,
                    Request.received_at
                    < window_end,
                    Request.status.in_(
                        (
                            "PENDING_BATCH",
                            "PENDING_CURP_LOOKUP",
                            "CURP_LOOKUP_RETRY",
                            "CURP_LOOKUP_FAILED",
                        )
                    ),
                )
                .order_by(
                    Request.received_at.asc(),
                    Request.id.asc(),
                )
            )
        )

        ready_requests = [
            item
            for item in window_requests
            if (
                item.status
                == "PENDING_BATCH"
                and bool(item.rfc)
            )
        ]

        waiting_requests = [
            item
            for item in window_requests
            if item.status
            in WAITING_CURP_STATUSES
        ]

        failed_requests = [
            item
            for item in window_requests
            if item.status
            == "CURP_LOOKUP_FAILED"
        ]

        client_ids = tuple(
            sorted(
                {
                    int(item.client_id)
                    for item
                    in window_requests
                }
            )
        )

        client_names: list[str] = []

        for client_id in client_ids:
            client = db.get(
                Client,
                client_id,
            )

            if client is not None:
                client_names.append(
                    client.name
                )

        state = ProviderBatchState(
            provider_id=provider.id,
            provider_name=provider.name,
            interval_minutes=
                GLOBAL_BATCH_INTERVAL_MINUTES,
            window_start=window_start,
            window_end=window_end,
            client_ids=client_ids,
            client_names=tuple(
                client_names
            ),
            ready_count=len(
                ready_requests
            ),
            waiting_curp_count=len(
                waiting_requests
            ),
            failed_curp_count=len(
                failed_requests
            ),
        )

        # Solo presentamos ventanas que ya
        # tienen actividad.
        if state.pending_count <= 0:
            continue

        states.append(state)

    return states


# Compatibilidad con código anterior.
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

    states = get_provider_batch_states(
        db
    )

    result.checked_windows = len(
        states
    )

    for state in states:
        # La ventana todavía está abierta.
        if not state.due_by_interval:
            continue

        result.due_windows += 1

        # Ya llegó el minuto 10, pero todavía
        # existen CURP obteniendo RFC.
        #
        # NO se manda un lote parcial.
        if state.waiting_curp_count > 0:
            window_key = (
                f"{state.provider_id}:"
                f"{state.window_start.isoformat()}"
            )

            result\
                .waiting_curp_windows\
                .append(window_key)

            logger.info(
                (
                    "Ventana esperando CURP "
                    "provider_id=%s "
                    "window_start=%s "
                    "window_end=%s "
                    "ready=%s "
                    "waiting_curp=%s"
                ),
                state.provider_id,
                state.window_start,
                state.window_end,
                state.ready_count,
                state.waiting_curp_count,
            )

            continue

        # Puede ocurrir que todas las solicitudes
        # de la ventana hayan fallado en CURP.
        if state.ready_count <= 0:
            continue

        lock_name = (
            "whatsapp-lotes:"
            "batch-provider-fixed-window:"
            f"{state.provider_id}:"
            f"{int(state.window_start.timestamp())}"
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
                            f"{state.window_start.isoformat()}"
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

            if refreshed is None:
                continue

            if not refreshed.due_by_interval:
                continue

            if (
                refreshed.waiting_curp_count
                > 0
            ):
                continue

            if refreshed.ready_count <= 0:
                continue

            creation = create_pending_batch(
                db,
                provider_id=
                    refreshed.provider_id,
                max_items=None,
                received_from=
                    refreshed.window_start,
                received_before=
                    refreshed.window_end,
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
                    "Lote global enviado "
                    "batch_id=%s "
                    "provider_id=%s "
                    "window_start=%s "
                    "window_end=%s "
                    "clients=%s "
                    "requests=%s"
                ),
                creation.batch_id,
                refreshed.provider_id,
                refreshed.window_start,
                refreshed.window_end,
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
                f"provider="
                f"{state.provider_id} "
                f"window="
                f"{state.window_start.isoformat()} "
                f"{error}"
            )

            result.errors.append(
                message
            )

            logger.exception(
                "Error procesando ventana "
                "global: %s",
                message,
            )

        except Exception as error:
            db.rollback()

            message = (
                f"provider="
                f"{state.provider_id} "
                f"window="
                f"{state.window_start.isoformat()} "
                f"{type(error).__name__}:"
                f"{error}"
            )

            result.errors.append(
                message
            )

            logger.exception(
                "Error inesperado en ventana "
                "global: %s",
                message,
            )

        finally:
            if acquired:
                try:
                    lock.release()
                except Exception:
                    logger.exception(
                        "No se pudo liberar lock "
                        "provider=%s window=%s",
                        state.provider_id,
                        state.window_start,
                    )

    return result
