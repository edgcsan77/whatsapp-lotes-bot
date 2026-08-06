import logging
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request as FastAPIRequest,
)
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.integrations.evolution_client import (
    EvolutionAPIError,
    send_text_message,
)
from app.services.acknowledgement_service import (
    build_request_acknowledgement,
)
from app.services.evolution_webhook import (
    parse_evolution_payload,
    secrets_match,
)
from app.services.provider_response_service import (
    get_active_provider_by_jid,
    process_provider_message,
)
from app.services.request_service import (
    ClientInactiveError,
    ClientNotFoundError,
    IncomingWhatsAppMessage,
    register_client_message,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
)


@router.post("/evolution")
async def evolution_webhook(
    request: FastAPIRequest,
    db: Session = Depends(get_db),
    x_webhook_secret: str | None = Header(
        default=None,
        alias="X-Webhook-Secret",
    ),
) -> dict[str, Any]:
    if not secrets_match(
        x_webhook_secret,
        settings.evolution_webhook_secret,
    ):
        raise HTTPException(
            status_code=401,
            detail="INVALID_WEBHOOK_SECRET",
        )

    try:
        payload = await request.json()
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail="INVALID_JSON",
        ) from error

    parsed = parse_evolution_payload(payload)

    if parsed is None:
        return {
            "ok": True,
            "ignored": True,
            "reason":
                "UNSUPPORTED_OR_INCOMPLETE_EVENT",
        }

    if parsed.instance != settings.evolution_instance:
        return {
            "ok": True,
            "ignored": True,
            "reason": "INSTANCE_NOT_ALLOWED",
            "instance": parsed.instance,
        }

    if parsed.from_me:
        return {
            "ok": True,
            "ignored": True,
            "reason": "MESSAGE_FROM_BOT",
        }

    if not parsed.text:
        return {
            "ok": True,
            "ignored": True,
            "reason": "MESSAGE_WITHOUT_TEXT",
        }

    # Primero revisamos si el mensaje viene
    # de un grupo proveedor.
    provider = get_active_provider_by_jid(
        db,
        parsed.source_jid,
    )

    if provider is not None:
        provider_result = (
            await process_provider_message(
                db,
                provider=provider,
                provider_message_id=
                    parsed.message_id,
                text=parsed.text,
            )
        )

        return {
            "ok": True,
            "ignored": False,
            "message_type":
                "PROVIDER_RESPONSE",
            "provider_id":
                provider_result.provider_id,
            "provider_name":
                provider_result.provider_name,
            "parsed_count":
                provider_result.parsed_count,
            "matched_request_ids":
                provider_result.matched_request_ids,
            "unmatched_rfcs":
                provider_result.unmatched_rfcs,
            "already_processed_rfcs":
                provider_result\
                    .already_processed_rfcs,
            "delivered_request_ids":
                provider_result\
                    .delivered_request_ids,
            "delivery_failed_request_ids":
                provider_result\
                    .delivery_failed_request_ids,
        }

    # Si no es proveedor, se procesa como cliente.
    try:
        result = register_client_message(
            db,
            IncomingWhatsAppMessage(
                message_id=parsed.message_id,
                source_jid=parsed.source_jid,
                sender_jid=parsed.sender_jid,
                sender_name=parsed.sender_name,
                text=parsed.text,
            ),
        )

    except ClientNotFoundError:
        return {
            "ok": True,
            "ignored": True,
            "reason": "CLIENT_NOT_REGISTERED",
            "source_jid": parsed.source_jid,
        }

    except ClientInactiveError:
        return {
            "ok": True,
            "ignored": True,
            "reason": "CLIENT_INACTIVE",
            "source_jid": parsed.source_jid,
        }

    acknowledgement_sent = False
    acknowledgement_error = None
    acknowledgement_message_id = None

    if result.created_identifiers:
        acknowledgement_text = (
            build_request_acknowledgement(
                result.created_identifiers
            )
        )

        if acknowledgement_text:
            try:
                send_result = await send_text_message(
                    destination_jid=
                        parsed.source_jid,
                    text=acknowledgement_text,
                    instance=parsed.instance,
                )

                acknowledgement_sent = (
                    send_result.ok
                )

                acknowledgement_message_id = (
                    send_result.message_id
                )

            except (
                EvolutionAPIError,
                ValueError,
            ) as error:
                acknowledgement_error = str(error)

                logger.exception(
                    "No se pudo enviar confirmación "
                    "message_id=%s source_jid=%s",
                    parsed.message_id,
                    parsed.source_jid,
                )

    return {
        "ok": True,
        "ignored": False,
        "message_type": "CLIENT_REQUEST",
        "client_id": result.client_id,
        "client_name": result.client_name,
        "parsed_count": result.parsed_count,
        "created_count": result.created_count,
        "created_ids": result.created_ids,
        "created_identifiers":
            result.created_identifiers,
        "duplicate_count": result.duplicate_count,
        "duplicate_identifiers":
            result.duplicate_identifiers,
        "ignored_curps": result.ignored_curps,
        "no_identifiers_found":
            result.no_identifiers_found,
        "acknowledgement_sent":
            acknowledgement_sent,
        "acknowledgement_message_id":
            acknowledgement_message_id,
        "acknowledgement_error":
            acknowledgement_error,
    }
