import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.evolution_client import (
    EvolutionAPIError,
    send_document_message,
    send_text_message,
)
from app.models.client import Client
from app.models.request import Request
from app.services.pdf_backend_client import (
    PdfBackendError,
    generate_pdf_document,
)


logger = logging.getLogger(__name__)


MAX_PDF_ATTEMPTS = 3

PDF_RETRY_DELAYS_SECONDS = {
    1: 60,
    2: 180,
    3: 600,
}


@dataclass
class PdfProcessingRunResult:
    checked_requests: int = 0

    generated_request_ids: list[int] = field(
        default_factory=list
    )

    delivered_request_ids: list[int] = field(
        default_factory=list
    )

    retried_request_ids: list[int] = field(
        default_factory=list
    )

    failed_request_ids: list[int] = field(
        default_factory=list
    )

    skipped_request_ids: list[int] = field(
        default_factory=list
    )

    errors: list[str] = field(
        default_factory=list
    )


def normalize_utc(
    value: datetime,
) -> datetime:
    if value.tzinfo is None:
        return value.replace(
            tzinfo=UTC
        )

    return value.astimezone(
        UTC
    )


def build_pdf_query(
    request: Request,
) -> str:
    route = str(
        request.lookup_route
        or ""
    ).strip().upper()

    if route == (
        "CURP_NL_SEPOMEX_NO_CHECKID"
    ):
        curp = str(
            request.original_curp
            or ""
        ).strip().upper()

        if not curp:
            raise PdfBackendError(
                "PDF_CURP_EMPTY"
            )

        return curp

    if route == "RFC_CHECKID":
        rfc = str(
            request.rfc
            or ""
        ).strip().upper()

        if not rfc:
            raise PdfBackendError(
                "PDF_RFC_EMPTY"
            )

        return rfc

    if route == "DIRECT_RFC_IDCIF":
        rfc = str(
            request.rfc
            or ""
        ).strip().upper()

        idcif = str(
            request.idcif
            or ""
        ).strip()

        if not rfc or not idcif:
            raise PdfBackendError(
                "PDF_RFC_IDCIF_INCOMPLETE"
            )

        return (
            f"RFC: {rfc}\n"
            f"IDCIF: {idcif}"
        )

    raise PdfBackendError(
        "PDF_LOOKUP_ROUTE_INVALID:"
        f"{route}"
    )


def build_pdf_backend_payload(
    *,
    request: Request,
    client: Client,
) -> dict[str, object]:
    query = build_pdf_query(
        request
    )

    source_jid = str(
        request.source_jid
        or client.whatsapp_jid
        or ""
    ).strip()

    group_jid = (
        source_jid
        if source_jid.endswith("@g.us")
        else ""
    )

    return {
        "requester_number":
            source_jid,
        "requester_name":
            client.name,
        "group_jid":
            group_jid,
        "original_text":
            request.original_text,
        "query":
            query,
        "evolution_instance":
            settings.evolution_instance,
        "lookup_route":
            request.lookup_route,
        "skip_internal_stats":
            True,
    }


def claim_pdf_request_ids(
    db: Session,
    *,
    limit: int,
    now: datetime,
) -> list[int]:
    normalized_now = normalize_utc(
        now
    )

    stale_before = (
        normalized_now
        - timedelta(minutes=20)
    )

    due_request = and_(
        Request.status.in_(
            [
                "PENDING_PDF",
                "PDF_RETRY",
            ]
        ),
        or_(
            Request.pdf_next_attempt_at
            .is_(None),
            Request.pdf_next_attempt_at
            <= normalized_now,
        ),
    )

    stale_generating = and_(
        Request.status
        == "PDF_GENERATING",
        Request.pdf_started_at
        .is_not(None),
        Request.pdf_started_at
        <= stale_before,
    )

    requests = list(
        db.scalars(
            select(Request)
            .where(
                Request.delivery_format
                == "PDF",
                or_(
                    due_request,
                    stale_generating,
                ),
            )
            .order_by(
                Request.received_at.asc(),
                Request.id.asc(),
            )
            .with_for_update(
                skip_locked=True
            )
            .limit(limit)
        )
    )

    request_ids: list[int] = []

    for request in requests:
        request.status = (
            "PDF_GENERATING"
        )

        request.pdf_status = (
            "GENERATING"
        )

        request.pdf_started_at = (
            normalized_now
        )

        request.pdf_error = None

        request_ids.append(
            request.id
        )

    if request_ids:
        db.commit()

    return request_ids


def register_pdf_failure(
    db: Session,
    *,
    request_id: int,
    error: Exception,
    now: datetime,
) -> tuple[
    Request | None,
    bool,
]:
    db.rollback()

    request = db.get(
        Request,
        request_id,
    )

    if request is None:
        return None, True

    request.pdf_attempts = int(
        request.pdf_attempts
        or 0
    ) + 1

    request.pdf_error = (
        f"{type(error).__name__}:"
        f"{error}"
    )[:4000]

    request.pdf_started_at = None

    reached_maximum = (
        request.pdf_attempts
        >= MAX_PDF_ATTEMPTS
    )

    if reached_maximum:
        request.status = "PDF_FAILED"
        request.pdf_status = "FAILED"
        request.pdf_next_attempt_at = None

    else:
        request.status = "PDF_RETRY"
        request.pdf_status = "RETRY"

        delay_seconds = (
            PDF_RETRY_DELAYS_SECONDS.get(
                request.pdf_attempts,
                600,
            )
        )

        request.pdf_next_attempt_at = (
            normalize_utc(now)
            + timedelta(
                seconds=delay_seconds
            )
        )

    db.commit()

    return request, reached_maximum


async def notify_pdf_failed(
    *,
    request: Request,
    client: Client,
) -> None:
    identifier = str(
        request.rfc
        or request.original_curp
        or request.identifier_key
        or ""
    ).strip().upper()

    text = (
        "⚠️ No fue posible generar "
        "la constancia\n\n"
        f"Dato: {identifier}\n\n"
        "La solicitud agotó sus "
        "reintentos automáticos."
    )

    try:
        await send_text_message(
            destination_jid=
                client.whatsapp_jid,
            text=text,
            instance=
                settings.evolution_instance,
        )

    except (
        EvolutionAPIError,
        ValueError,
    ):
        logger.exception(
            "No se pudo avisar PDF_FAILED "
            "request_id=%s",
            request.id,
        )


async def process_one_pdf_request(
    db: Session,
    *,
    request_id: int,
    now: datetime,
    result: PdfProcessingRunResult,
) -> None:
    request = db.get(
        Request,
        request_id,
    )

    if request is None:
        result.skipped_request_ids.append(
            request_id
        )
        return

    if request.status != "PDF_GENERATING":
        result.skipped_request_ids.append(
            request_id
        )
        return

    client = db.get(
        Client,
        request.client_id,
    )

    if client is None:
        error = PdfBackendError(
            "PDF_CLIENT_NOT_FOUND"
        )

        _, reached_maximum = (
            register_pdf_failure(
                db,
                request_id=request_id,
                error=error,
                now=now,
            )
        )

        if reached_maximum:
            result.failed_request_ids.append(
                request_id
            )
        else:
            result.retried_request_ids.append(
                request_id
            )

        result.errors.append(
            f"request_id={request_id} "
            f"{error}"
        )

        return

    try:
        pdf_url = str(
            request.pdf_url
            or ""
        ).strip()

        filename = str(
            request.pdf_filename
            or ""
        ).strip()

        if not pdf_url:
            payload = (
                build_pdf_backend_payload(
                    request=request,
                    client=client,
                )
            )

            backend_result = (
                await generate_pdf_document(
                    payload=payload
                )
            )

            pdf_url = (
                backend_result.pdf_url
            )

            filename = (
                backend_result.filename
            )

            request.pdf_url = pdf_url

            request.pdf_filename = (
                Path(filename).name
                or "constancia.pdf"
            )

            db.commit()

            result\
                .generated_request_ids\
                .append(request.id)

        if not filename:
            filename = (
                request.pdf_filename
                or "constancia.pdf"
            )

        send_result = (
            await send_document_message(
                destination_jid=
                    client.whatsapp_jid,
                media_url=pdf_url,
                file_name=
                    Path(filename).name,
                instance=
                    settings.evolution_instance,
            )
        )

        request = db.get(
            Request,
            request_id,
        )

        if request is None:
            return

        request.status = "DELIVERED"
        request.pdf_status = "DELIVERED"
        request.pdf_error = None
        request.pdf_started_at = None
        request.pdf_next_attempt_at = None
        request.delivered_at = (
            normalize_utc(now)
        )

        request.pdf_delivered_message_id = (
            send_result.message_id
        )

        if (
            str(
                request.service_type
                or ""
            )
            .strip()
            .upper()
            == "RFC_GENERIC"
        ):
            request.result_code = "OK"

            if not request.provider_result:
                request.provider_result = (
                    "PDF_GENERIC"
                )

        db.commit()

        result.delivered_request_ids.append(
            request_id
        )

    except Exception as error:
        failed_request, reached_maximum = (
            register_pdf_failure(
                db,
                request_id=request_id,
                error=error,
                now=now,
            )
        )

        result.errors.append(
            f"request_id={request_id} "
            f"{type(error).__name__}:"
            f"{error}"
        )

        logger.exception(
            "Error generando/entregando PDF "
            "request_id=%s",
            request_id,
        )

        if reached_maximum:
            result.failed_request_ids.append(
                request_id
            )

            if failed_request is not None:
                await notify_pdf_failed(
                    request=failed_request,
                    client=client,
                )

        else:
            result.retried_request_ids.append(
                request_id
            )


async def process_pending_pdfs(
    db: Session,
    *,
    limit: int = 3,
    now: datetime | None = None,
) -> PdfProcessingRunResult:
    result = PdfProcessingRunResult()

    current_time = normalize_utc(
        now or datetime.now(UTC)
    )

    safe_limit = max(
        1,
        min(int(limit), 20),
    )

    request_ids = claim_pdf_request_ids(
        db,
        limit=safe_limit,
        now=current_time,
    )

    result.checked_requests = len(
        request_ids
    )

    for request_id in request_ids:
        await process_one_pdf_request(
            db,
            request_id=request_id,
            now=current_time,
            result=result,
        )

    return result
