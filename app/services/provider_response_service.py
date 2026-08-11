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
from app.models.request import Request
from app.services.provider_parser import (
    ParsedProviderResult,
    parse_provider_message,
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


def find_pending_request_for_result(
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
                    "SENT_TO_PROVIDER",
                    "BATCH_SEND_FAILED",
                ]
            ),
        )
        .order_by(
            Request.sent_to_provider_at.asc(),
            Request.id.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
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
                    "DELIVERY_FAILED",
                    "DELIVERED",
                ]
            ),
        )
        .order_by(Request.id.desc())
        .limit(1)
    )


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
        request = find_pending_request_for_result(
            db,
            provider_id=provider.id,
            rfc=parsed.rfc,
        )

        if request is None:
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

        client = db.get(
            Client,
            request.client_id,
        )

        if client is None:
            logger.error(
                "Cliente inexistente para request_id=%s",
                request.id,
            )
            result.unmatched_rfcs.append(
                parsed.rfc
            )
            continue

        request.provider_result = parsed.raw_line
        request.idcif = parsed.idcif
        request.result_code = parsed.result_code
        request.provider_replied_at = now
        request.status = "RESULT_RECEIVED"

        result.matched_request_ids.append(
            request.id
        )

        delivery_map[client.id].append(
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


async def process_provider_message(
    db: Session,
    *,
    provider: Provider,
    provider_message_id: str,
    text: str,
) -> ProviderProcessingResult:
    processing_result, delivery_groups = (
        register_provider_results(
            db,
            provider=provider,
            provider_message_id=provider_message_id,
            text=text,
        )
    )

    if not delivery_groups:
        return processing_result

    return await deliver_provider_results(
        db,
        processing_result=processing_result,
        delivery_groups=delivery_groups,
        evolution_instance=provider.evolution_instance,
    )
