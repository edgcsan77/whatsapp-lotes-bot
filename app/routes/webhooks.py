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
    parse_evolution_delete_payload,
    parse_evolution_payload,
    secrets_match,
)
from app.services.provider_response_service import (
    get_active_provider_by_jid,
    process_provider_message,
)
from app.services.whatsapp_admin_service import (
    process_whatsapp_admin_command,
)

from app.services.delayed_client_message_service import (
    DelayedClientMessage,
    cancel_client_message,
    enqueue_client_message,
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

    deleted = parse_evolution_delete_payload(
        payload
    )

    if deleted is not None:
        if (
            deleted.instance
            != settings.evolution_instance
        ):
            return {
                "ok": True,
                "ignored": True,
                "reason": "INSTANCE_NOT_ALLOWED",
                "instance": deleted.instance,
            }

        cancelled = cancel_client_message(
            instance=deleted.instance,
            source_jid=deleted.source_jid,
            message_id=deleted.message_id,
        )

        logger.info(
            "MESSAGES_DELETE recibido "
            "message_id=%s source_jid=%s "
            "cancelled=%s",
            deleted.message_id,
            deleted.source_jid,
            cancelled,
        )

        return {
            "ok": True,
            "ignored": False,
            "message_type":
                "CLIENT_MESSAGE_DELETE",
            "message_id":
                deleted.message_id,
            "cancelled": cancelled,
        }

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

    if not parsed.text:
        return {
            "ok": True,
            "ignored": True,
            "reason": "MESSAGE_WITHOUT_TEXT",
        }

    # Comandos administrativos WhatsApp.
    # Se procesan ANTES de proveedor/cliente
    # para poder autorizar chats aún no
    # registrados.
    admin_command_result = (
        process_whatsapp_admin_command(
            db,
            source_jid=parsed.source_jid,
            sender_jid=parsed.sender_jid,
            sender_name=parsed.sender_name,
            text=parsed.text,
            from_me=parsed.from_me,
        )
    )

    if admin_command_result.handled:
        command_response_sent = False
        command_response_error = None

        if admin_command_result.response_text:
            try:
                command_send_result = (
                    await send_text_message(
                        destination_jid=
                            parsed.source_jid,
                        text=(
                            admin_command_result
                            .response_text
                        ),
                        instance=parsed.instance,
                    )
                )

                command_response_sent = (
                    command_send_result.ok
                )

            except (
                EvolutionAPIError,
                ValueError,
            ) as error:
                command_response_error = (
                    str(error)
                )

                logger.exception(
                    "No se pudo responder "
                    "comando admin WhatsApp "
                    "message_id=%s",
                    parsed.message_id,
                )

        return {
            "ok": True,
            "ignored": False,
            "message_type":
                "WHATSAPP_ADMIN_COMMAND",
            "response_sent":
                command_response_sent,
            "response_error":
                command_response_error,
        }

    # Los mensajes enviados desde el propio
    # WhatsApp del bot solo pueden llegar hasta
    # el procesador de comandos administrativos.
    #
    # Si no fueron reconocidos como comando,
    # se ignoran para evitar reprocesar mensajes
    # que el propio bot acaba de enviar.
    if parsed.from_me:
        return {
            "ok": True,
            "ignored": True,
            "reason": "MESSAGE_FROM_BOT",
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

    # Si no es proveedor ni comando administrativo,
    # se trata como posible mensaje de cliente.
    #
    # NO se crea todavía ninguna Request.
    # Se conserva durante 60 segundos en Redis para
    # permitir que el usuario lo elimine de WhatsApp.
    due_at = enqueue_client_message(
        DelayedClientMessage(
            instance=parsed.instance,
            message_id=parsed.message_id,
            source_jid=parsed.source_jid,
            sender_jid=parsed.sender_jid,
            sender_name=parsed.sender_name,
            text=parsed.text,
        ),
        delay_seconds=60,
    )

    logger.info(
        "Mensaje cliente puesto en espera "
        "message_id=%s source_jid=%s "
        "delay_seconds=60 due_at=%s",
        parsed.message_id,
        parsed.source_jid,
        due_at,
    )

    return {
        "ok": True,
        "ignored": False,
        "message_type":
            "CLIENT_REQUEST_DELAYED",
        "message_id": parsed.message_id,
        "delay_seconds": 60,
    }

    # Código histórico conservado temporalmente
    # debajo para facilitar rollback.
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

    invalid_curp_notice_sent = False
    invalid_curp_notice_error = None

    if result.invalid_curps:
        lines = [
            "⚠️ *CURP con formato incorrecto*",
            "",
        ]

        for invalid_curp in (
            result.invalid_curps
        ):
            lines.append(
                f"• {invalid_curp}"
            )

        lines.extend(
            [
                "",
                (
                    "Verifica que la CURP tenga "
                    "18 caracteres y esté escrita "
                    "correctamente."
                ),
            ]
        )

        try:
            invalid_send_result = (
                await send_text_message(
                    destination_jid=
                        parsed.source_jid,
                    text="\n".join(lines),
                    instance=parsed.instance,
                )
            )

            invalid_curp_notice_sent = (
                invalid_send_result.ok
            )

        except (
            EvolutionAPIError,
            ValueError,
        ) as error:
            invalid_curp_notice_error = (
                str(error)
            )

            logger.exception(
                "No se pudo enviar aviso "
                "de CURP inválida "
                "message_id=%s "
                "source_jid=%s",
                parsed.message_id,
                parsed.source_jid,
            )

    recent_duplicate_notice_sent = False
    recent_duplicate_notice_error = None

    duplicate_lines: list[str] = []

    if result.recent_in_progress_identifiers:
        duplicate_lines.extend(
            [
                "⏳ *Solicitud ya en proceso*",
                "",
            ]
        )

        for identifier in (
            result.recent_in_progress_identifiers
        ):
            duplicate_lines.append(
                f"• {identifier}"
            )

        duplicate_lines.extend(
            [
                "",
                (
                    "Esta solicitud ya está siendo "
                    "procesada."
                ),
            ]
        )

    if result.recent_processed_identifiers:
        if duplicate_lines:
            duplicate_lines.extend(
                [
                    "",
                    "──────────",
                    "",
                ]
            )

        duplicate_lines.extend(
            [
                "⚠️ *Solicitud duplicada*",
                "",
            ]
        )

        for identifier in (
            result.recent_processed_identifiers
        ):
            duplicate_lines.append(
                f"• {identifier}"
            )

        duplicate_lines.extend(
            [
                "",
                (
                    "Esta solicitud ya ha sido "
                    "procesada en las últimas "
                    "24 horas."
                ),
            ]
        )

    if duplicate_lines:
        try:
            duplicate_send_result = (
                await send_text_message(
                    destination_jid=
                        parsed.source_jid,
                    text="\n".join(
                        duplicate_lines
                    ),
                    instance=parsed.instance,
                )
            )

            recent_duplicate_notice_sent = (
                duplicate_send_result.ok
            )

        except (
            EvolutionAPIError,
            ValueError,
        ) as error:
            recent_duplicate_notice_error = (
                str(error)
            )

            logger.exception(
                "No se pudo enviar aviso "
                "de solicitud duplicada 24h "
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
        "recent_in_progress_identifiers":
            result.recent_in_progress_identifiers,
        "recent_processed_identifiers":
            result.recent_processed_identifiers,
        "recent_duplicate_notice_sent":
            recent_duplicate_notice_sent,
        "recent_duplicate_notice_error":
            recent_duplicate_notice_error,
        "ignored_curps": result.ignored_curps,
        "invalid_curps":
            result.invalid_curps,
        "invalid_curp_reasons":
            result.invalid_curp_reasons,
        "invalid_curp_notice_sent":
            invalid_curp_notice_sent,
        "invalid_curp_notice_error":
            invalid_curp_notice_error,
        "no_identifiers_found":
            result.no_identifiers_found,
        "acknowledgement_sent":
            acknowledgement_sent,
        "acknowledgement_message_id":
            acknowledgement_message_id,
        "acknowledgement_error":
            acknowledgement_error,
    }
