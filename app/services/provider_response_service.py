import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.evolution_client import (
    EvolutionAPIError,
    send_text_message,
)
from app.models.client import Client
from app.models.provider import Provider
from app.models.batch import Batch, BatchItem
from app.models.request import Request
from app.services.provider_parser import (
    ParsedProviderResult,
    parse_provider_message,
)
from app.services.pdf_backend_client import (
    PdfBackendError,
    validate_rfc_idcif,
)
from app.services.idcif_validation import (
    build_idcif_failure_message,
    build_temporary_failure_message,
    is_terminal_code,
)


logger = logging.getLogger(__name__)


class ProviderResponseError(Exception):
    """Error al procesar respuestas de proveedor."""


@dataclass
class ProviderProcessingResult:
    provider_id: int
    provider_name: str
    parsed_count: int = 0
    matched_request_ids: list[int] = field(
        default_factory=list
    )
    unmatched_rfcs: list[str] = field(
        default_factory=list
    )
    already_processed_rfcs: list[str] = field(
        default_factory=list
    )
    delivered_request_ids: list[int] = field(
        default_factory=list
    )
    queued_pdf_request_ids: list[int] = field(
        default_factory=list
    )
    delivery_failed_request_ids: list[int] = field(
        default_factory=list
    )


@dataclass(frozen=True)
class ClientDeliveryGroup:
    client_id: int
    client_name: str
    client_jid: str
    request_ids: tuple[int, ...]
    text: str


def get_active_provider_by_jid(
    db: Session,
    source_jid: str,
) -> Provider | None:
    jid = str(source_jid or "").strip()

    if not jid:
        return None

    return db.scalar(
        select(Provider).where(
            Provider.whatsapp_jid == jid,
            Provider.deleted_at.is_(None),
            Provider.active.is_(True),
        )
    )


def render_provider_result_line(
    result: ParsedProviderResult,
) -> str:
    rfc = str(
        result.rfc or ""
    ).strip().upper()

    if (
        result.result_code == "OK"
        and result.idcif
    ):
        return (
            f"{rfc} "
            f"{str(result.idcif).strip()}"
        )

    # Cualquier respuesta sin IDCIF
    # se muestra al cliente como NO ID,
    # independientemente de si el proveedor
    # respondió SIN ID, SR o solamente RFC.
    return f"NO ID {rfc}"


def build_client_result_message(
    results: list[ParsedProviderResult],
) -> str:
    lines = [
        render_provider_result_line(result)
        for result in results
    ]

    if len(lines) == 1:
        return (
            "✅ Resultado recibido\n\n"
            f"{lines[0]}"
        )

    body = "\n".join(lines)

    return (
        "✅ Resultados recibidos\n\n"
        f"{body}"
    )


def find_pending_requests_for_result(
    db: Session,
    *,
    provider_id: int,
    rfc: str,
) -> list[Request]:
    pending_statuses = (
        "SENT_TO_PROVIDER",
        "PROVIDER_TIMEOUT",
    )

    # Una respuesta del proveedor solamente trae
    # el RFC, no el batch_id.
    #
    # Si ese RFC fue solicitado nuevamente días
    # después, no debemos aplicar la respuesta
    # nueva a solicitudes pendientes de lotes
    # históricos.
    #
    # Seleccionamos el lote pendiente más reciente.
    # Si varios clientes tienen el mismo RFC dentro
    # de ese lote, todos reciben el resultado.
    latest_batch_id = db.scalar(
        select(BatchItem.batch_id)
        .join(
            Request,
            Request.id == BatchItem.request_id,
        )
        .join(
            Batch,
            Batch.id == BatchItem.batch_id,
        )
        .where(
            Request.provider_id == provider_id,
            Request.rfc == rfc,
            Request.status.in_(pending_statuses),
        )
        .order_by(
            Batch.sent_at.desc(),
            Batch.id.desc(),
        )
        .limit(1)
    )

    if latest_batch_id is None:
        return []

    return list(
        db.scalars(
            select(Request)
            .join(
                BatchItem,
                BatchItem.request_id == Request.id,
            )
            .where(
                Request.provider_id == provider_id,
                Request.rfc == rfc,
                Request.status.in_(pending_statuses),
                BatchItem.batch_id == latest_batch_id,
            )
            .order_by(
                Request.sent_to_provider_at.asc(),
                Request.id.asc(),
            )
            .with_for_update(
                skip_locked=True
            )
        )
    )

def find_already_processed_request(
    db: Session,
    *,
    provider_id: int,
    rfc: str,
) -> Request | None:
    return db.scalar(
        select(Request)
        .where(
            Request.provider_id == provider_id,
            Request.rfc == rfc,
            Request.status.in_(
                [
                    "RESULT_RECEIVED",
                    "PENDING_PDF",
                    "PDF_GENERATING",
                    "PDF_RETRY",
                    "PDF_FAILED",
                    "DELIVERY_FAILED",
                    "DELIVERED",
                ]
            ),
        )
        .order_by(Request.id.desc())
        .limit(1)
    )


def _register_provider_results_legacy(
    db: Session,
    *,
    provider: Provider,
    provider_message_id: str,
    text: str,
) -> tuple[
    ProviderProcessingResult,
    list[ClientDeliveryGroup],
]:
    parsed_results = parse_provider_message(text)

    result = ProviderProcessingResult(
        provider_id=provider.id,
        provider_name=provider.name,
        parsed_count=len(parsed_results),
    )

    if not parsed_results:
        return result, []

    now = datetime.now(UTC)

    delivery_map: dict[
        int,
        list[
            tuple[
                Request,
                Client,
                ParsedProviderResult,
            ]
        ],
    ] = defaultdict(list)

    for parsed in parsed_results:
        requests = find_pending_requests_for_result(
            db,
            provider_id=provider.id,
            rfc=parsed.rfc,
        )

        if not requests:
            processed = find_already_processed_request(
                db,
                provider_id=provider.id,
                rfc=parsed.rfc,
            )

            if processed is not None:
                result.already_processed_rfcs.append(
                    parsed.rfc
                )
            else:
                result.unmatched_rfcs.append(
                    parsed.rfc
                )

            continue

        for request in requests:
            client = db.get(
                Client,
                request.client_id,
            )

            if client is None:
                logger.error(
                    "Cliente inexistente para "
                    "request_id=%s",
                    request.id,
                )
                continue

            request.provider_result = (
                parsed.raw_line
            )
            request.idcif = (
                parsed.idcif
            )
            request.result_code = (
                parsed.result_code
            )
            request.provider_replied_at = now
            request.status = (
                "RESULT_RECEIVED"
            )

            result.matched_request_ids.append(
                request.id
            )

            delivery_map[
                client.id
            ].append(
                (
                    request,
                    client,
                    parsed,
                )
            )

    db.commit()

    delivery_groups: list[ClientDeliveryGroup] = []

    for client_id, entries in delivery_map.items():
        client = entries[0][1]

        delivery_groups.append(
            ClientDeliveryGroup(
                client_id=client_id,
                client_name=client.name,
                client_jid=client.whatsapp_jid,
                request_ids=tuple(
                    entry[0].id
                    for entry in entries
                ),
                text=build_client_result_message(
                    [
                        entry[2]
                        for entry in entries
                    ]
                ),
            )
        )

    return result, delivery_groups


def register_provider_results(
    db: Session,
    *,
    provider: Provider,
    provider_message_id: str,
    text: str,
) -> tuple[
    ProviderProcessingResult,
    list[ClientDeliveryGroup],
]:
    processing_result, delivery_groups = (
        _register_provider_results_legacy(
            db,
            provider=provider,
            provider_message_id=
                provider_message_id,
            text=text,
        )
    )

    if not (
        processing_result
        .matched_request_ids
    ):
        return (
            processing_result,
            delivery_groups,
        )

    now = datetime.now(UTC)

    pdf_request_ids: set[int] = set()

    for request_id in (
        processing_result
        .matched_request_ids
    ):
        request_item = db.get(
            Request,
            request_id,
        )

        if request_item is None:
            continue

        wants_pdf = (
            str(
                request_item
                .delivery_format
                or ""
            )
            .strip()
            .upper()
            == "PDF"
            and str(
                request_item.result_code
                or ""
            )
            .strip()
            .upper()
            == "OK"
            and bool(
                str(
                    request_item.idcif
                    or ""
                ).strip()
            )
        )

        if not wants_pdf:
            continue

        request_item.status = (
            "PENDING_PDF"
        )

        request_item.pdf_status = (
            "PENDING"
        )

        request_item.lookup_route = (
            "DIRECT_RFC_IDCIF"
        )

        request_item.pdf_error = None
        request_item.pdf_started_at = None

        request_item.pdf_next_attempt_at = (
            now
        )

        pdf_request_ids.add(
            request_item.id
        )

        processing_result\
            .queued_pdf_request_ids\
            .append(
                request_item.id
            )

    if not pdf_request_ids:
        return (
            processing_result,
            delivery_groups,
        )

    db.commit()

    filtered_groups: list[
        ClientDeliveryGroup
    ] = []

    for group in delivery_groups:
        remaining_ids = tuple(
            request_id
            for request_id
            in group.request_ids
            if request_id
            not in pdf_request_ids
        )

        if not remaining_ids:
            continue

        if (
            len(remaining_ids)
            == len(group.request_ids)
        ):
            filtered_groups.append(
                group
            )

            continue

        remaining_results: list[
            ParsedProviderResult
        ] = []

        for request_id in remaining_ids:
            request_item = db.get(
                Request,
                request_id,
            )

            if request_item is None:
                continue

            parsed_candidates = (
                parse_provider_message(
                    request_item
                    .provider_result
                    or ""
                )
            )

            selected = next(
                (
                    candidate
                    for candidate
                    in parsed_candidates
                    if candidate.rfc
                    == request_item.rfc
                ),
                None,
            )

            if selected is None:
                selected = (
                    ParsedProviderResult(
                        rfc=str(
                            request_item.rfc
                            or request_item
                            .identifier_key
                            or ""
                        )
                        .strip()
                        .upper(),
                        raw_value=(
                            str(
                                request_item.idcif
                                or ""
                            ).strip()
                            or None
                        ),
                        idcif=(
                            str(
                                request_item.idcif
                                or ""
                            ).strip()
                            or None
                        ),
                        result_code=str(
                            request_item
                            .result_code
                            or "RFC_ONLY"
                        )
                        .strip()
                        .upper(),
                        raw_line=(
                            request_item
                            .provider_result
                            or ""
                        ),
                    )
                )

            remaining_results.append(
                selected
            )

        if not remaining_results:
            continue

        filtered_groups.append(
            ClientDeliveryGroup(
                client_id=
                    group.client_id,
                client_name=
                    group.client_name,
                client_jid=
                    group.client_jid,
                request_ids=
                    remaining_ids,
                text=(
                    build_client_result_message(
                        remaining_results
                    )
                ),
            )
        )

    return (
        processing_result,
        filtered_groups,
    )




async def deliver_provider_results(
    db: Session,
    *,
    processing_result: ProviderProcessingResult,
    delivery_groups: list[ClientDeliveryGroup],
    evolution_instance: str,
) -> ProviderProcessingResult:
    for group in delivery_groups:
        try:
            await send_text_message(
                destination_jid=group.client_jid,
                text=group.text,
                instance=evolution_instance,
            )

        except (
            EvolutionAPIError,
            ValueError,
        ):
            logger.exception(
                "No se pudo entregar resultado "
                "al cliente_id=%s",
                group.client_id,
            )

            requests = list(
                db.scalars(
                    select(Request).where(
                        Request.id.in_(
                            group.request_ids
                        )
                    )
                )
            )

            for request in requests:
                request.status = "DELIVERY_FAILED"

                processing_result\
                    .delivery_failed_request_ids\
                    .append(request.id)

            db.commit()

            # Import local para evitar dependencia circular.
            from app.services.retry_service import (
                register_retry_failure,
            )

            for request in requests:
                register_retry_failure(
                    "delivery",
                    request.id,
                    "INITIAL_DELIVERY_FAILED",
                )

            continue

        now = datetime.now(UTC)

        requests = list(
            db.scalars(
                select(Request).where(
                    Request.id.in_(
                        group.request_ids
                    )
                )
            )
        )

        for request in requests:
            request.status = "DELIVERED"
            request.delivered_at = now

            processing_result\
                .delivered_request_ids\
                .append(request.id)

        db.commit()

    return processing_result



def _rebuild_delivery_groups_excluding(
    db: Session,
    *,
    delivery_groups: list[ClientDeliveryGroup],
    excluded_ids: set[int],
) -> list[ClientDeliveryGroup]:
    if not excluded_ids:
        return delivery_groups

    output: list[ClientDeliveryGroup] = []

    for group in delivery_groups:
        remaining_ids = tuple(
            request_id
            for request_id in group.request_ids
            if request_id not in excluded_ids
        )

        if not remaining_ids:
            continue

        if len(remaining_ids) == len(group.request_ids):
            output.append(group)
            continue

        parsed_results: list[ParsedProviderResult] = []

        for request_id in remaining_ids:
            request_item = db.get(Request, request_id)
            if request_item is None:
                continue

            candidates = parse_provider_message(request_item.provider_result or "")
            selected = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.rfc == request_item.rfc
                ),
                None,
            )

            if selected is None:
                selected = ParsedProviderResult(
                    rfc=str(
                        request_item.rfc
                        or request_item.identifier_key
                        or ""
                    ).strip().upper(),
                    raw_value=(str(request_item.idcif or "").strip() or None),
                    idcif=(str(request_item.idcif or "").strip() or None),
                    result_code=str(
                        request_item.result_code or "RFC_ONLY"
                    ).strip().upper(),
                    raw_line=request_item.provider_result or "",
                )

            parsed_results.append(selected)

        if not parsed_results:
            continue

        output.append(
            ClientDeliveryGroup(
                client_id=group.client_id,
                client_name=group.client_name,
                client_jid=group.client_jid,
                request_ids=remaining_ids,
                text=build_client_result_message(parsed_results),
            )
        )

    return output


async def _validate_matched_idcif_before_delivery(
    db: Session,
    *,
    processing_result: ProviderProcessingResult,
    delivery_groups: list[ClientDeliveryGroup],
    provider_jid: str,
    evolution_instance: str,
) -> list[ClientDeliveryGroup]:
    targets: list[Request] = []

    for request_id in processing_result.matched_request_ids:
        request_item = db.get(Request, request_id)
        if request_item is None:
            continue

        if (
            str(request_item.result_code or "").strip().upper() == "OK"
            and bool(str(request_item.idcif or "").strip())
        ):
            targets.append(request_item)

    if not targets:
        return delivery_groups

    unique_pairs = {
        (
            str(item.rfc or "").strip().upper(),
            str(item.idcif or "").strip(),
        )
        for item in targets
    }

    async def validate_pair(pair):
        rfc, idcif = pair
        try:
            value = await validate_rfc_idcif(rfc=rfc, idcif=idcif)
            return pair, value, None
        except PdfBackendError as error:
            return pair, None, error

    validated = await asyncio.gather(
        *[validate_pair(pair) for pair in unique_pairs]
    )

    by_pair = {
        pair: (validation, error)
        for pair, validation, error in validated
    }

    excluded_ids: set[int] = set()

    provider_notices: list[str] = []
    provider_notice_keys: set[
        tuple[str, str, str]
    ] = set()

    temporary_notices: dict[int, list[str]] = defaultdict(list)
    client_jids: dict[int, str] = {}

    for request_item in targets:
        client = db.get(Client, request_item.client_id)
        if client is None:
            continue

        client_jids[client.id] = client.whatsapp_jid
        pair = (
            str(request_item.rfc or "").strip().upper(),
            str(request_item.idcif or "").strip(),
        )

        validation, error = by_pair[pair]

        if validation is not None and validation.valid:
            continue

        if (
            validation is not None
            and validation.terminal
            and is_terminal_code(validation.code)
        ):
            code = str(
                validation.code
                or ""
            ).strip().upper()

            # El proveedor dio una respuesta RFC+IDCIF,
            # pero SAT la rechazó.
            #
            # La petición NO termina: vuelve a quedar
            # esperando corrección del mismo proveedor.
            request_item.status = "SENT_TO_PROVIDER"
            request_item.result_code = code

            # Reinicia la ventana de espera desde el momento
            # en que avisamos al proveedor que debe corregir.
            request_item.sent_to_provider_at = datetime.now(UTC)
            request_item.provider_replied_at = None

            # Cancela cualquier PDF que hubiera quedado
            # programado antes de la validación SAT.
            request_item.pdf_status = None
            request_item.pdf_next_attempt_at = None
            request_item.pdf_started_at = None
            request_item.pdf_error = None
            request_item.pdf_url = None
            request_item.pdf_filename = None
            request_item.pdf_delivered_message_id = None
            request_item.pdf_attempts = 0
            request_item.delivered_at = None

            # NO cambiar sale_price.
            # Todavía no es una petición terminada.

            excluded_ids.add(request_item.id)

            if request_item.id in processing_result.queued_pdf_request_ids:
                processing_result.queued_pdf_request_ids.remove(
                    request_item.id
                )

            notice_key = (
                pair[0],
                pair[1],
                code,
            )

            if notice_key not in provider_notice_keys:
                provider_notice_keys.add(
                    notice_key
                )

                provider_notices.append(
                    build_idcif_failure_message(
                        rfc=pair[0],
                        idcif=pair[1],
                        code=code,
                    )
                )

            continue

        # Error temporal:
        # - PDF conserva su mecanismo propio de retry.
        # - TEXT queda pendiente de una NUEVA validación SAT.
        #   NO se vuelve a pedir el resultado al proveedor.
        #   NO se avisa error al cliente todavía.
        is_pdf = str(
            request_item.delivery_format or ""
        ).strip().upper() == "PDF"

        if is_pdf:
            continue

        request_item.status = "IDCIF_VALIDATION_RETRY"
        request_item.result_code = "SAT_TEMPORAL_ERROR"

        # Conserva provider_result, RFC, IDCIF y sale_price.
        # El resultado del proveedor ya existe; solamente falta
        # revalidarlo cuando SAT/backend vuelva a responder.
        excluded_ids.add(request_item.id)

        from app.services.retry_service import (
            register_retry_failure,
        )

        register_retry_failure(
            "idcif_validation",
            request_item.id,
            error or "SAT_TEMPORAL_ERROR",
        )

        logger.warning(
            "IDCIF validation temporal; programado retry "
            "request_id=%s rfc=%s idcif=%s",
            request_item.id,
            pair[0],
            pair[1],
        )

    db.commit()

    if provider_notices:
        try:
            await send_text_message(
                destination_jid=provider_jid,
                text="\n\n".join(
                    provider_notices
                ),
                instance=evolution_instance,
            )
        except (EvolutionAPIError, ValueError):
            logger.exception(
                "No se pudo avisar rechazo "
                "RFC/IDCIF al proveedor jid=%s",
                provider_jid,
            )

    for client_id, messages in temporary_notices.items():
        try:
            await send_text_message(
                destination_jid=client_jids[client_id],
                text="\n\n".join(messages),
                instance=evolution_instance,
            )
        except (EvolutionAPIError, ValueError):
            logger.exception(
                "No se pudo avisar error temporal IDCIF client_id=%s",
                client_id,
            )

    return _rebuild_delivery_groups_excluding(
        db,
        delivery_groups=delivery_groups,
        excluded_ids=excluded_ids,
    )


async def process_provider_message(
    db: Session,
    *,
    provider: Provider,
    provider_message_id: str,
    text: str,
) -> ProviderProcessingResult:
    processing_result, delivery_groups = register_provider_results(
        db,
        provider=provider,
        provider_message_id=provider_message_id,
        text=text,
    )

    delivery_groups = await _validate_matched_idcif_before_delivery(
        db,
        processing_result=processing_result,
        delivery_groups=delivery_groups,
        provider_jid=provider.whatsapp_jid,
        evolution_instance=provider.evolution_instance,
    )

    if not delivery_groups:
        return processing_result

    return await deliver_provider_results(
        db,
        processing_result=processing_result,
        delivery_groups=delivery_groups,
        evolution_instance=provider.evolution_instance,
    )
