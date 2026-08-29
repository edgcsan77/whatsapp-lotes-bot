import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.evolution_client import (
    EvolutionAPIError,
    send_text_message,
)

from app.models.client import Client
from app.models.provider import Provider
from app.models.request import Request
from app.redis_client import redis_client
from app.services.curp_rfc_engine import (
    CurpRfcError,
    convert_curp_to_rfc,
)


logger = logging.getLogger(__name__)

MAX_CURP_ATTEMPTS = 3

CURP_RETRY_DELAYS_MINUTES = {
    1: 1,
    2: 2,
    3: 5,
}

CURP_RETRY_TTL_SECONDS = 60 * 60 * 24 * 14


@dataclass
class CurpProcessingRunResult:
    checked_requests: int = 0
    processed_request_ids: list[int] = field(
        default_factory=list
    )
    generated_rfcs: dict[int, str] = field(
        default_factory=dict
    )
    corrected_curps: dict[int, str] = field(
        default_factory=dict
    )
    retried_request_ids: list[int] = field(
        default_factory=list
    )
    failed_request_ids: list[int] = field(
        default_factory=list
    )
    skipped_not_due: list[int] = field(
        default_factory=list
    )
    skipped_locked: list[int] = field(
        default_factory=list
    )
    errors: list[str] = field(
        default_factory=list
    )


def curp_attempt_key(
    request_id: int,
) -> str:
    return (
        "whatsapp-lotes:curp:"
        f"{request_id}:attempts"
    )


def curp_next_key(
    request_id: int,
) -> str:
    return (
        "whatsapp-lotes:curp:"
        f"{request_id}:next_at"
    )


def curp_error_key(
    request_id: int,
) -> str:
    return (
        "whatsapp-lotes:curp:"
        f"{request_id}:last_error"
    )


def curp_lock_key(
    request_id: int,
) -> str:
    return (
        "whatsapp-lotes:curp-lock:"
        f"{request_id}"
    )


def normalize_worker_partition(
    *,
    worker_slot: int = 0,
    worker_count: int = 1,
) -> tuple[int, int]:
    count = max(
        1,
        min(int(worker_count), 4),
    )

    slot = int(worker_slot)

    if not 0 <= slot < count:
        raise ValueError(
            "CURP_WORKER_SLOT_INVALID:"
            f"{slot}/{count}"
        )

    return slot, count


def global_curp_lock_key(
    *,
    worker_slot: int = 0,
    worker_count: int = 1,
) -> str:
    slot, count = (
        normalize_worker_partition(
            worker_slot=worker_slot,
            worker_count=worker_count,
        )
    )

    base = (
        "whatsapp-lotes:"
        "curp-lock:global"
    )

    if count == 1:
        return base

    return (
        f"{base}:"
        f"{count}:"
        f"{slot}"
    )


def get_curp_attempts(
    request_id: int,
) -> int:
    value = redis_client.get(
        curp_attempt_key(request_id)
    )

    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def get_curp_next_timestamp(
    request_id: int,
) -> float:
    value = redis_client.get(
        curp_next_key(request_id)
    )

    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def is_curp_due(
    request_id: int,
    *,
    now_timestamp: float | None = None,
) -> bool:
    now = (
        time.time()
        if now_timestamp is None
        else now_timestamp
    )

    return (
        now
        >= get_curp_next_timestamp(
            request_id
        )
    )


def register_curp_failure(
    request_id: int,
    error: Exception | str,
) -> int:
    attempts = (
        get_curp_attempts(request_id)
        + 1
    )

    delay_minutes = (
        CURP_RETRY_DELAYS_MINUTES.get(
            attempts,
            CURP_RETRY_DELAYS_MINUTES[
                MAX_CURP_ATTEMPTS
            ],
        )
    )

    next_timestamp = (
        time.time()
        + delay_minutes * 60
    )

    pipe = redis_client.pipeline()

    pipe.set(
        curp_attempt_key(request_id),
        attempts,
        ex=CURP_RETRY_TTL_SECONDS,
    )

    pipe.set(
        curp_next_key(request_id),
        next_timestamp,
        ex=CURP_RETRY_TTL_SECONDS,
    )

    pipe.set(
        curp_error_key(request_id),
        str(error)[:1000],
        ex=CURP_RETRY_TTL_SECONDS,
    )

    pipe.execute()

    return attempts


def clear_curp_retry_state(
    request_id: int,
) -> None:
    redis_client.delete(
        curp_attempt_key(request_id),
        curp_next_key(request_id),
        curp_error_key(request_id),
    )


def is_permanent_curp_error(
    error: CurpRfcError,
) -> bool:
    text = str(error)
    normalized_text = text.lower()

    permanent_prefixes = (
        "CURP_INVALIDA",
        "NL_CURP_RESPONSE_MISMATCH",
        "NL_CURP_DATA_INCOMPLETE",
        "CURP_BIRTHDATE_INVALID",
        "MOFFIN_DATA_INCOMPLETE",
        "RFC_CURP_DATE_MISMATCH",
    )

    if text.startswith(
        permanent_prefixes
    ):
        return True

    permanent_messages = (
        "no se encuentra en la base de datos",
    )

    return any(
        message in normalized_text
        for message in permanent_messages
    )


def build_detected_name(
    data: dict[str, str],
) -> str | None:
    parts = [
        data.get("NOMBRE", ""),
        data.get("PRIMER_APELLIDO", ""),
        data.get("SEGUNDO_APELLIDO", ""),
    ]

    name = " ".join(
        part.strip()
        for part in parts
        if part and part.strip()
    )

    return name or None


def get_pending_curp_requests(
    db: Session,
    *,
    limit: int = 3,
    worker_slot: int = 0,
    worker_count: int = 1,
) -> list[Request]:
    slot, count = (
        normalize_worker_partition(
            worker_slot=worker_slot,
            worker_count=worker_count,
        )
    )

    query = select(Request).where(
        Request.status.in_(
            [
                "PENDING_CURP_LOOKUP",
                "CURP_LOOKUP_RETRY",
            ]
        ),
        Request.original_curp.is_not(
            None
        ),
        Request.rfc.is_(None),
    )

    # Carriles disjuntos:
    # con 2 workers, uno toma IDs pares
    # y el otro IDs impares.
    if count > 1:
        query = query.where(
            (Request.id % count)
            == slot
        )

    query = (
        query
        .order_by(
            Request.received_at.asc(),
            Request.id.asc(),
        )
        .limit(limit)
    )

    return list(
        db.scalars(query)
    )


def resolve_provider(
    db: Session,
    request: Request,
) -> Provider:
    if request.provider_id is not None:
        provider = db.get(
            Provider,
            request.provider_id,
        )

        if (
            provider is not None
            and provider.active
        ):
            return provider

    client = db.get(
        Client,
        request.client_id,
    )

    if client is None:
        raise CurpRfcError(
            "CLIENT_NOT_FOUND"
        )

    if client.default_provider_id is None:
        raise CurpRfcError(
            "CLIENT_WITHOUT_PROVIDER"
        )

    provider = db.get(
        Provider,
        client.default_provider_id,
    )

    if provider is None:
        raise CurpRfcError(
            "PROVIDER_NOT_FOUND"
        )

    if not provider.active:
        raise CurpRfcError(
            "PROVIDER_INACTIVE"
        )

    request.provider_id = provider.id

    return provider


async def notify_curp_lookup_failed(
    request: Request,
) -> None:
    destination_jid = str(
        request.source_jid or ""
    ).strip()

    if not destination_jid:
        logger.error(
            "No se pudo avisar fallo CURP: "
            "request_id=%s source_jid vacío",
            request.id,
        )
        return

    curp = str(
        request.original_curp
        or request.identifier_key
        or ""
    ).strip().upper()

    text = (
        "⚠️ No fue posible generar el RFC\n\n"
        f"CURP: {curp}\n\n"
        "No se pudo obtener el RFC para esta CURP. "
        "Puedes intentar nuevamente más tarde."
    )

    try:
        await send_text_message(
            destination_jid=destination_jid,
            text=text,
        )

        logger.info(
            "Aviso CURP fallida enviado: "
            "request_id=%s",
            request.id,
        )

    except (
        EvolutionAPIError,
        ValueError,
    ):
        logger.exception(
            "No se pudo enviar aviso de CURP "
            "fallida: request_id=%s",
            request.id,
        )


def process_one_curp_request(
    db: Session,
    *,
    request_id: int,
) -> tuple[str, str]:
    request = db.get(
        Request,
        request_id,
    )

    if request is None:
        raise CurpRfcError(
            "REQUEST_NOT_FOUND"
        )

    if request.status not in {
        "PENDING_CURP_LOOKUP",
        "CURP_LOOKUP_RETRY",
    }:
        raise CurpRfcError(
            "REQUEST_STATUS_NOT_PROCESSABLE:"
            f"{request.status}"
        )

    original_curp = str(
        request.original_curp
        or request.identifier_key
        or ""
    ).strip().upper()

    if not original_curp:
        raise CurpRfcError(
            "REQUEST_WITHOUT_CURP"
        )

    resolve_provider(
        db,
        request,
    )

    rfc, data = convert_curp_to_rfc(
        original_curp
    )

    corrected_curp = str(
        data.get("CURP")
        or original_curp
    ).strip().upper()

    request.rfc = rfc
    request.original_curp = corrected_curp
    request.detected_name = (
        build_detected_name(data)
    )
    request.status = "PENDING_BATCH"

    db.commit()

    clear_curp_retry_state(
        request.id
    )

    return rfc, corrected_curp


async def process_pending_curps(
    db: Session,
    *,
    limit: int = 3,
    worker_slot: int = 0,
    worker_count: int = 1,
) -> CurpProcessingRunResult:
    result = CurpProcessingRunResult()

    slot, count = (
        normalize_worker_partition(
            worker_slot=worker_slot,
            worker_count=worker_count,
        )
    )

    requests = get_pending_curp_requests(
        db,
        limit=limit,
        worker_slot=slot,
        worker_count=count,
    )

    result.checked_requests = len(
        requests
    )

    if not requests:
        return result

    global_lock = redis_client.lock(
        global_curp_lock_key(
            worker_slot=slot,
            worker_count=count,
        ),
        timeout=600,
        blocking_timeout=0,
    )

    global_acquired = bool(
        global_lock.acquire(
            blocking=False
        )
    )

    if not global_acquired:
        result.skipped_locked.extend(
            request.id
            for request in requests
        )
        return result

    try:
        for request in requests:
            request_id = request.id

            attempts = get_curp_attempts(
                request_id
            )

            if attempts >= MAX_CURP_ATTEMPTS:
                request.status = (
                    "CURP_LOOKUP_FAILED"
                )
                db.commit()

                await notify_curp_lookup_failed(
                    request
                )

                result.failed_request_ids.append(
                    request_id
                )
                continue

            if not is_curp_due(
                request_id
            ):
                result.skipped_not_due.append(
                    request_id
                )
                continue

            request_lock = redis_client.lock(
                curp_lock_key(request_id),
                timeout=240,
                blocking_timeout=0,
            )

            acquired = bool(
                request_lock.acquire(
                    blocking=False
                )
            )

            if not acquired:
                result.skipped_locked.append(
                    request_id
                )
                continue

            try:
                try:
                    rfc, corrected_curp = (
                        process_one_curp_request(
                            db,
                            request_id=request_id,
                        )
                    )

                    result\
                        .processed_request_ids\
                        .append(request_id)

                    result.generated_rfcs[
                        request_id
                    ] = rfc

                    result.corrected_curps[
                        request_id
                    ] = corrected_curp

                    logger.info(
                        "CURP convertida a RFC: "
                        "request_id=%s rfc=%s",
                        request_id,
                        rfc,
                    )

                except CurpRfcError as error:
                    db.rollback()

                    refreshed = db.get(
                        Request,
                        request_id,
                    )

                    if refreshed is None:
                        result.errors.append(
                            f"request_id={request_id} "
                            "REQUEST_NOT_FOUND"
                        )
                        continue

                    if is_permanent_curp_error(
                        error
                    ):
                        refreshed.status = (
                            "CURP_LOOKUP_FAILED"
                        )
                        db.commit()

                        register_curp_failure(
                            request_id,
                            error,
                        )

                        await notify_curp_lookup_failed(
                            refreshed
                        )

                        result\
                            .failed_request_ids\
                            .append(request_id)

                    else:
                        attempt = (
                            register_curp_failure(
                                request_id,
                                error,
                            )
                        )

                        if (
                            attempt
                            >= MAX_CURP_ATTEMPTS
                        ):
                            refreshed.status = (
                                "CURP_LOOKUP_FAILED"
                            )

                            await notify_curp_lookup_failed(
                                refreshed
                            )

                            result\
                                .failed_request_ids\
                                .append(request_id)
                        else:
                            refreshed.status = (
                                "CURP_LOOKUP_RETRY"
                            )

                            result\
                                .retried_request_ids\
                                .append(request_id)

                        db.commit()

                    result.errors.append(
                        f"request_id={request_id} "
                        f"{error}"
                    )

                    logger.exception(
                        "Error procesando CURP: "
                        "request_id=%s",
                        request_id,
                    )

                except Exception as error:
                    db.rollback()

                    refreshed = db.get(
                        Request,
                        request_id,
                    )

                    attempt = register_curp_failure(
                        request_id,
                        error,
                    )

                    if refreshed is not None:
                        reached_max = (
                            attempt
                            >= MAX_CURP_ATTEMPTS
                        )

                        refreshed.status = (
                            "CURP_LOOKUP_FAILED"
                            if reached_max
                            else "CURP_LOOKUP_RETRY"
                        )
                        db.commit()

                        if reached_max:
                            await notify_curp_lookup_failed(
                                refreshed
                            )

                            result\
                                .failed_request_ids\
                                .append(request_id)
                        else:
                            result\
                                .retried_request_ids\
                                .append(request_id)

                    result.errors.append(
                        f"request_id={request_id} "
                        f"{type(error).__name__}:"
                        f"{error}"
                    )

            finally:
                try:
                    request_lock.release()
                except Exception:
                    logger.exception(
                        "No se pudo liberar lock "
                        "CURP request_id=%s",
                        request_id,
                    )

    finally:
        try:
            global_lock.release()
        except Exception:
            logger.exception(
                "No se pudo liberar lock "
                "global CURP"
            )

    return result
