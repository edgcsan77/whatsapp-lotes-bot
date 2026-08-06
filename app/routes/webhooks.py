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
from app.services.evolution_webhook import (
    parse_evolution_payload,
    secrets_match,
)
from app.services.request_service import (
    ClientInactiveError,
    ClientNotFoundError,
    IncomingWhatsAppMessage,
    register_client_message,
)


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

    parsed = parse_evolution_payload(
        payload
    )

    if parsed is None:
        return {
            "ok": True,
            "ignored": True,
            "reason": "UNSUPPORTED_OR_INCOMPLETE_EVENT",
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

    return {
        "ok": True,
        "ignored": False,
        "client_id": result.client_id,
        "client_name": result.client_name,
        "parsed_count": result.parsed_count,
        "created_count": result.created_count,
        "created_ids": result.created_ids,
        "duplicate_count": result.duplicate_count,
        "duplicate_identifiers":
            result.duplicate_identifiers,
        "ignored_curps": result.ignored_curps,
        "no_identifiers_found":
            result.no_identifiers_found,
    }
