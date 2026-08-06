import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.evolution_client import (
    EvolutionAPIError,
    send_text_message,
)
from app.models.batch import Batch, BatchItem
from app.models.client import Client
from app.models.provider import Provider
from app.models.request import Request
from app.redis_client import redis_client
from app.services.batch_service import (
    BatchServiceError,
    send_existing_batch,
)


logger = logging.getLogger(__name__)

MAX_RETRY_ATTEMPTS = 5

# Minutos de espera después de cada fallo.
RETRY_DELAYS_MINUTES = {
    1: 1,
    2: 2,
    3: 5,
    4: 10,
    5: 30,
}

RETRY_KEY_TTL_SECONDS = 60 * 60 * 24 * 14


@dataclass
class RetryRunResult:
    checked_batch_failures: int = 0
    checked_delivery_failures: int = 0

    retried_batch_ids: list[int] = field(
        default_factory=list
    )
    recovered_batch_ids: list[int] = field(
        default_factory=list
    )
    exhausted_batch_ids: list[int] = field(
        default_factory=list
    )

    retried_request_ids: list[int] = field(
        default_factory=list
    )
    recovered_request_ids: list[int] = field(
        default_factory=list
    )
    exhausted_request_ids: list[int] = field(
        default_factory=list
    )

    skipped_not_due: list[str] = field(
        default_factory=list
    )
    skipped_locked: list[str] = field(
        default_factory=list
    )
    errors: list[str] = field(
        default_factory=list
    )


def retry_attempt_key(
    kind: str,
    item_id: int,
) -> str:
    return (
        "whatsapp-lotes:retry:"
        f"{kind}:{item_id}:attempts"
    )


def retry_next_key(
    kind: str,
    item_id: int,
) -> str:
    return (
        "whatsapp-lotes:retry:"
        f"{kind}:{item_id}:next_at"
    )


def retry_last_error_key(
    kind: str,
    item_id: int,
) -> str:
    return (
        "whatsapp-lotes:retry:"
        f"{kind}:{item_id}:last_error"
    )


def retry_lock_key(
    kind: str,
    item_id: int,
) -> str:
    return (
        "whatsapp-lotes:retry-lock:"
        f"{kind}:{item_id}"
    )


def get_attempts(
    kind: str,
    item_id: int,
) -> int:
    value = redis_client.get(
        retry_attempt_key(kind, item_id)
    )

    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def get_next_retry_timestamp(
    kind: str,
    item_id: int,
) -> float:
    value = redis_client.get(
        retry_next_key(kind, item_id)
    )

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def is_retry_due(
    kind: str,
    item_id: int,
    *,
    now_timestamp: float | None = None,
) -> bool:
    now = (
        now_timestamp
        if now_timestamp is not None
        else time.time()
    )

    return (
        now
        >= get_next_retry_timestamp(
            kind,
            item_id,
        )
    )


def register_retry_failure(
    kind: str,
    item_id: int,
    error: Exception | str,
) -> int:
    current_attempts = get_attempts(
        kind,
        item_id,
    )

    attempts = current_attempts + 1

    delay_minutes = RETRY_DELAYS_MINUTES.get(
        attempts,
        RETRY_DELAYS_MINUTES[
            MAX_RETRY_ATTEMPTS
        ],
    )

    next_timestamp = (
        time.time()
        + delay_minutes * 60
    )

    pipe = redis_client.pipeline()

    pipe.set(
        retry_attempt_key(kind, item_id),
        attempts,
        ex=RETRY_KEY_TTL_SECONDS,
    )

    pipe.set(
        retry_next_key(kind, item_id),
        next_timestamp,
        ex=RETRY_KEY_TTL_SECONDS,
    )

    pipe.set(
        retry_last_error_key(kind, item_id),
        str(error)[:1000],
        ex=RETRY_KEY_TTL_SECONDS,
    )

    pipe.execute()

    return attempts


def clear_retry_state(
    kind: str,
    item_id: int,
) -> None:
    redis_client.delete(
        retry_attempt_key(kind, item_id),
        retry_next_key(kind, item_id),
        retry_last_error_key(kind, item_id),
    )


def build_delivery_retry_text(
    requests: list[Request],
) -> str:
    lines: list[str] = []

    for request in requests:
        raw_result = str(
            request.provider_result or ""
        ).strip()

        if raw_result:
            lines.append(raw_result)
            continue

        rfc = str(
            request.rfc or ""
        ).strip().upper()

        if not rfc:
            continue

        if request.result_code == "OK" and request.idcif:
            lines.append(
                f"{rfc} {request.idcif}"
            )
        elif request.result_code == "SIN_ID":
            lines.append(
                f"{rfc} SIN ID"
            )
        elif request.result_code == "SIN_RESULTADO":
            lines.append(
                f"{rfc} SR"
            )
        else:
            lines.append(rfc)

    if not lines:
        raise ValueError(
            "DELIVERY_RETRY_WITHOUT_RESULT"
        )

    if len(lines) == 1:
        return (
            "✅ Resultado recibido\n\n"
            f"{lines[0]}"
        )

    return (
        "✅ Resultados recibidos\n\n"
        + "\n".join(lines)
    )


async def retry_failed_batch(
    db: Session,
    *,
    batch: Batch,
    result: RetryRunResult,
) -> None:
    kind = "batch"
    item_id = batch.id

    attempts = get_attempts(
        kind,
        item_id,
    )

    if attempts >= MAX_RETRY_ATTEMPTS:
        result.exhausted_batch_ids.append(
            batch.id
        )
        return

    if not is_retry_due(
        kind,
        item_id,
    ):
        result.skipped_not_due.append(
            f"batch:{batch.id}"
        )
        return

    lock = redis_client.lock(
        retry_lock_key(
            kind,
            item_id,
        ),
        timeout=120,
        blocking_timeout=0,
    )

    acquired = bool(
        lock.acquire(blocking=False)
    )

    if not acquired:
        result.skipped_locked.append(
            f"batch:{batch.id}"
        )
        return

    try:
        db.expire_all()

        refreshed = db.get(
            Batch,
            batch.id,
        )

        if refreshed is None:
            clear_retry_state(
                kind,
                item_id,
            )
            return

        if refreshed.status == "SENT":
            clear_retry_state(
                kind,
                item_id,
            )
            return

        if refreshed.status != "SEND_FAILED":
            clear_retry_state(
                kind,
                item_id,
            )
            return

        result.retried_batch_ids.append(
            refreshed.id
        )

        try:
            await send_existing_batch(
                db,
                batch_id=refreshed.id,
            )

        except EvolutionAPIError:
            # send_existing_batch() ya registró este fallo
            # y programó el siguiente intento.
            attempt = get_attempts(
                kind,
                item_id,
            )

            logger.exception(
                "Falló reintento de lote: "
                "batch_id=%s attempt=%s",
                refreshed.id,
                attempt,
            )

            if attempt >= MAX_RETRY_ATTEMPTS:
                result.exhausted_batch_ids.append(
                    refreshed.id
                )

            return

        except (
            BatchServiceError,
            ValueError,
        ) as error:
            attempt = register_retry_failure(
                kind,
                item_id,
                error,
            )

            logger.exception(
                "Falló reintento de lote: "
                "batch_id=%s attempt=%s",
                refreshed.id,
                attempt,
            )

            if attempt >= MAX_RETRY_ATTEMPTS:
                result.exhausted_batch_ids.append(
                    refreshed.id
                )

            return

        clear_retry_state(
            kind,
            item_id,
        )

        result.recovered_batch_ids.append(
            refreshed.id
        )

        logger.info(
            "Lote recuperado por reintento: "
            "batch_id=%s",
            refreshed.id,
        )

    finally:
        try:
            lock.release()
        except Exception:
            logger.exception(
                "No se pudo liberar bloqueo "
                "de retry para batch_id=%s",
                batch.id,
            )


async def retry_failed_delivery_group(
    db: Session,
    *,
    client: Client,
    requests: list[Request],
    result: RetryRunResult,
) -> None:
    if not requests:
        return

    request_ids = sorted(
        request.id
        for request in requests
    )

    # Cada solicitud conserva su propio contador.
    due_requests: list[Request] = []

    for request in requests:
        attempts = get_attempts(
            "delivery",
            request.id,
        )

        if attempts >= MAX_RETRY_ATTEMPTS:
            result.exhausted_request_ids.append(
                request.id
            )
            continue

        if not is_retry_due(
            "delivery",
            request.id,
        ):
            result.skipped_not_due.append(
                f"delivery:{request.id}"
            )
            continue

        due_requests.append(request)

    if not due_requests:
        return

    lock_name = (
        "whatsapp-lotes:retry-lock:"
        f"delivery-client:{client.id}"
    )

    lock = redis_client.lock(
        lock_name,
        timeout=120,
        blocking_timeout=0,
    )

    acquired = bool(
        lock.acquire(blocking=False)
    )

    if not acquired:
        result.skipped_locked.append(
            f"delivery-client:{client.id}"
        )
        return

    try:
        db.expire_all()

        fresh_requests = list(
            db.scalars(
                select(Request)
                .where(
                    Request.id.in_(
                        [
                            request.id
                            for request in due_requests
                        ]
                    ),
                    Request.status
                    == "DELIVERY_FAILED",
                )
                .order_by(Request.id.asc())
            )
        )

        if not fresh_requests:
            for request_id in request_ids:
                clear_retry_state(
                    "delivery",
                    request_id,
                )
            return

        provider_ids = {
            request.provider_id
            for request in fresh_requests
            if request.provider_id is not None
        }

        if len(provider_ids) != 1:
            error = ValueError(
                "DELIVERY_RETRY_PROVIDER_AMBIGUOUS"
            )

            for request in fresh_requests:
                attempt = register_retry_failure(
                    "delivery",
                    request.id,
                    error,
                )

                if attempt >= MAX_RETRY_ATTEMPTS:
                    result\
                        .exhausted_request_ids\
                        .append(request.id)

            result.errors.append(
                f"client_id={client.id} {error}"
            )
            return

        provider = db.get(
            Provider,
            next(iter(provider_ids)),
        )

        if provider is None:
            error = ValueError(
                "DELIVERY_RETRY_PROVIDER_NOT_FOUND"
            )

            for request in fresh_requests:
                attempt = register_retry_failure(
                    "delivery",
                    request.id,
                    error,
                )

                if attempt >= MAX_RETRY_ATTEMPTS:
                    result\
                        .exhausted_request_ids\
                        .append(request.id)

            result.errors.append(
                f"client_id={client.id} {error}"
            )
            return

        text = build_delivery_retry_text(
            fresh_requests
        )

        result.retried_request_ids.extend(
            request.id
            for request in fresh_requests
        )

        try:
            await send_text_message(
                destination_jid=
                    client.whatsapp_jid,
                text=text,
                instance=
                    provider.evolution_instance,
            )

        except (
            EvolutionAPIError,
            ValueError,
        ) as error:
            for request in fresh_requests:
                attempt = register_retry_failure(
                    "delivery",
                    request.id,
                    error,
                )

                if attempt >= MAX_RETRY_ATTEMPTS:
                    result\
                        .exhausted_request_ids\
                        .append(request.id)

            logger.exception(
                "Falló reintento de entrega: "
                "client_id=%s request_ids=%s",
                client.id,
                [
                    request.id
                    for request in fresh_requests
                ],
            )

            return

        now = datetime.now(UTC)

        for request in fresh_requests:
            request.status = "DELIVERED"
            request.delivered_at = now

            clear_retry_state(
                "delivery",
                request.id,
            )

            result.recovered_request_ids.append(
                request.id
            )

        db.commit()

        logger.info(
            "Entrega recuperada por reintento: "
            "client_id=%s request_ids=%s",
            client.id,
            [
                request.id
                for request in fresh_requests
            ],
        )

    finally:
        try:
            lock.release()
        except Exception:
            logger.exception(
                "No se pudo liberar bloqueo "
                "de retry para client_id=%s",
                client.id,
            )


async def process_failed_retries(
    db: Session,
) -> RetryRunResult:
    result = RetryRunResult()

    failed_batches = list(
        db.scalars(
            select(Batch)
            .where(
                Batch.status == "SEND_FAILED"
            )
            .order_by(Batch.id.asc())
        )
    )

    result.checked_batch_failures = len(
        failed_batches
    )

    for batch in failed_batches:
        try:
            await retry_failed_batch(
                db,
                batch=batch,
                result=result,
            )
        except Exception as error:
            message = (
                f"batch_id={batch.id} "
                f"unexpected_error="
                f"{type(error).__name__}:{error}"
            )

            result.errors.append(message)
            logger.exception(message)

    failed_requests = list(
        db.scalars(
            select(Request)
            .where(
                Request.status
                == "DELIVERY_FAILED"
            )
            .order_by(
                Request.client_id.asc(),
                Request.id.asc(),
            )
        )
    )

    result.checked_delivery_failures = len(
        failed_requests
    )

    requests_by_client: dict[
        int,
        list[Request],
    ] = {}

    for request in failed_requests:
        requests_by_client.setdefault(
            request.client_id,
            [],
        ).append(request)

    for client_id, requests in (
        requests_by_client.items()
    ):
        client = db.get(
            Client,
            client_id,
        )

        if client is None:
            result.errors.append(
                f"client_id={client_id} "
                "CLIENT_NOT_FOUND"
            )
            continue

        try:
            await retry_failed_delivery_group(
                db,
                client=client,
                requests=requests,
                result=result,
            )
        except Exception as error:
            message = (
                f"client_id={client_id} "
                f"unexpected_error="
                f"{type(error).__name__}:{error}"
            )

            result.errors.append(message)
            logger.exception(message)

    return result
