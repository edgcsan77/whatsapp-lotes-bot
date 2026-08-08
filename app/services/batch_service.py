from dataclasses import dataclass
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


class BatchServiceError(Exception):
    """Error al crear o enviar un lote."""


@dataclass(frozen=True)
class BatchCreationResult:
    batch_id: int
    provider_id: int
    provider_name: str
    provider_jid: str
    request_ids: list[int]
    outbound_text: str


@dataclass(frozen=True)
class BatchSendResult:
    batch_id: int
    request_count: int
    provider_message_id: str | None
    status: str


def build_batch_text(
    requests: list[Request],
) -> str:
    identifiers: list[str] = []

    for request in requests:
        identifier = str(
            request.rfc
            or request.identifier_key
            or ""
        ).strip().upper()

        if identifier:
            identifiers.append(identifier)

    if not identifiers:
        raise BatchServiceError(
            "BATCH_WITHOUT_IDENTIFIERS"
        )

    return "\n".join(identifiers)


def create_pending_batch(
    db: Session,
    *,
    provider_id: int,
    client_id: int | None = None,
    client_ids: list[int] | tuple[int, ...] | None = None,
    max_items: int | None = None,
) -> BatchCreationResult:
    provider = db.get(
        Provider,
        provider_id,
    )

    if provider is None:
        raise BatchServiceError(
            "PROVIDER_NOT_FOUND"
        )

    if not provider.active:
        raise BatchServiceError(
            "PROVIDER_INACTIVE"
        )

    limit = max_items or 50

    query = (
        select(Request)
        .where(
            Request.provider_id == provider.id,
            Request.status == "PENDING_BATCH",
            Request.rfc.is_not(None),
        )
        .order_by(
            Request.received_at.asc(),
            Request.id.asc(),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )

    if (
        client_id is not None
        and client_ids is not None
    ):
        raise BatchServiceError(
            "CLIENT_FILTER_CONFLICT"
        )

    if client_id is not None:
        query = query.where(
            Request.client_id == client_id
        )

    if client_ids is not None:
        normalized_client_ids = [
            int(item)
            for item in client_ids
        ]

        if not normalized_client_ids:
            raise BatchServiceError(
                "EMPTY_CLIENT_IDS"
            )

        query = query.where(
            Request.client_id.in_(
                normalized_client_ids
            )
        )

    requests = list(
        db.scalars(query)
    )

    if not requests:
        raise BatchServiceError(
            "NO_PENDING_REQUESTS"
        )

    client_ids = {
        request.client_id
        for request in requests
    }

    batch_client_id = (
        next(iter(client_ids))
        if len(client_ids) == 1
        else None
    )

    outbound_text = build_batch_text(
        requests
    )

    batch = Batch(
        client_id=batch_client_id,
        provider_id=provider.id,
        status="CREATED",
        request_count=len(requests),
        outbound_text=outbound_text,
    )

    db.add(batch)
    db.flush()

    for position, request in enumerate(
        requests,
        start=1,
    ):
        db.add(
            BatchItem(
                batch_id=batch.id,
                request_id=request.id,
                position=position,
            )
        )

        request.status = "BATCH_CREATED"

    db.commit()

    return BatchCreationResult(
        batch_id=batch.id,
        provider_id=provider.id,
        provider_name=provider.name,
        provider_jid=provider.whatsapp_jid,
        request_ids=[
            request.id
            for request in requests
        ],
        outbound_text=outbound_text,
    )


async def send_existing_batch(
    db: Session,
    *,
    batch_id: int,
) -> BatchSendResult:
    batch = db.get(
        Batch,
        batch_id,
    )

    if batch is None:
        raise BatchServiceError(
            "BATCH_NOT_FOUND"
        )

    if batch.status == "SENT":
        raise BatchServiceError(
            "BATCH_ALREADY_SENT"
        )

    provider = db.get(
        Provider,
        batch.provider_id,
    )

    if provider is None:
        raise BatchServiceError(
            "PROVIDER_NOT_FOUND"
        )

    if not provider.active:
        raise BatchServiceError(
            "PROVIDER_INACTIVE"
        )

    items = list(
        db.scalars(
            select(BatchItem)
            .where(
                BatchItem.batch_id == batch.id
            )
            .order_by(BatchItem.position.asc())
        )
    )

    if not items:
        raise BatchServiceError(
            "BATCH_WITHOUT_ITEMS"
        )

    request_ids = [
        item.request_id
        for item in items
    ]

    requests = list(
        db.scalars(
            select(Request)
            .where(
                Request.id.in_(request_ids)
            )
        )
    )

    request_map = {
        request.id: request
        for request in requests
    }

    try:
        send_result = await send_text_message(
            destination_jid=provider.whatsapp_jid,
            text=batch.outbound_text or "",
            instance=provider.evolution_instance,
        )

    except EvolutionAPIError as error:
        batch.status = "SEND_FAILED"

        for request in requests:
            request.status = "BATCH_SEND_FAILED"

        db.commit()

        # Import local para evitar dependencia circular.
        from app.services.retry_service import (
            register_retry_failure,
        )

        register_retry_failure(
            "batch",
            batch.id,
            error,
        )

        raise

    now = datetime.now(UTC)

    batch.status = "SENT"
    batch.provider_message_id = (
        send_result.message_id
    )
    batch.sent_at = now

    for item in items:
        request = request_map.get(
            item.request_id
        )

        if request is None:
            continue

        request.status = "SENT_TO_PROVIDER"
        request.sent_to_provider_at = now

    db.commit()

    return BatchSendResult(
        batch_id=batch.id,
        request_count=len(items),
        provider_message_id=(
            send_result.message_id
        ),
        status=batch.status,
    )
