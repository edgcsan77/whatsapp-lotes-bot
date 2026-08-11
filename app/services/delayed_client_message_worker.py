import asyncio
import logging
import uuid
from collections import defaultdict

from app.database import SessionLocal
from app.integrations.evolution_client import (
    EvolutionAPIError,
    send_text_message,
)
from app.services.acknowledgement_service import (
    build_request_acknowledgement,
)
from app.services.delayed_client_message_service import (
    ACK_RETRY_DELAYS,
    DelayedClientMessage,
    PendingAckRetry,
    enqueue_ack_retry,
    get_due_ack_retry_keys,
    get_due_keys,
    get_pending_ack_retry,
    get_pending_message,
    remove_ack_retry_key,
    remove_pending_key,
)
from app.services.request_service import (
    ClientInactiveError,
    ClientNotFoundError,
    IncomingWhatsAppMessage,
    register_client_message,
)


logger = logging.getLogger(__name__)


def unique_preserving_order(
    values: list[str],
) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = str(
            value or ""
        ).strip().upper()

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        output.append(normalized)

    return output


async def send_group_message(
    *,
    destination_jid: str,
    instance: str,
    text: str,
    log_label: str,
) -> bool:
    if not str(text or "").strip():
        return False

    try:
        result = await send_text_message(
            destination_jid=destination_jid,
            text=text,
            instance=instance,
        )

        return bool(result.ok)

    except (
        EvolutionAPIError,
        ValueError,
    ):
        logger.exception(
            "No se pudo enviar %s "
            "source_jid=%s",
            log_label,
            destination_jid,
        )

        return False


async def process_pending_group(
    entries: list[
        tuple[str, DelayedClientMessage]
    ],
) -> int:
    if not entries:
        return 0

    first_pending = entries[0][1]

    destination_jid = (
        first_pending.source_jid
    )
    instance = first_pending.instance

    created_identifiers: list[str] = []
    invalid_curps: list[str] = []
    recent_in_progress: list[str] = []
    recent_processed: list[str] = []

    successful_keys: list[str] = []

    db = SessionLocal()

    try:
        for key, pending in entries:
            try:
                result = register_client_message(
                    db,
                    IncomingWhatsAppMessage(
                        message_id=
                            pending.message_id,
                        source_jid=
                            pending.source_jid,
                        sender_jid=
                            pending.sender_jid,
                        sender_name=
                            pending.sender_name,
                        text=
                            pending.text,
                    ),
                )

            except ClientNotFoundError:
                logger.info(
                    "Mensaje retrasado ignorado: "
                    "CLIENT_NOT_REGISTERED "
                    "message_id=%s "
                    "source_jid=%s",
                    pending.message_id,
                    pending.source_jid,
                )

                remove_pending_key(key)
                continue

            except ClientInactiveError:
                logger.info(
                    "Mensaje retrasado ignorado: "
                    "CLIENT_INACTIVE "
                    "message_id=%s "
                    "source_jid=%s",
                    pending.message_id,
                    pending.source_jid,
                )

                remove_pending_key(key)
                continue

            except Exception:
                db.rollback()

                logger.exception(
                    "Error procesando mensaje "
                    "retrasado key=%s",
                    key,
                )

                # Este mensaje permanece en Redis
                # para poder reintentarse.
                continue

            created_identifiers.extend(
                result.created_identifiers
            )

            invalid_curps.extend(
                result.invalid_curps
            )

            recent_in_progress.extend(
                result
                .recent_in_progress_identifiers
            )

            recent_processed.extend(
                result
                .recent_processed_identifiers
            )

            successful_keys.append(key)

            logger.info(
                "Mensaje de grupo retrasado "
                "registrado "
                "message_id=%s "
                "source_jid=%s "
                "created_count=%s",
                pending.message_id,
                pending.source_jid,
                result.created_count,
            )

        created_identifiers = (
            unique_preserving_order(
                created_identifiers
            )
        )

        invalid_curps = (
            unique_preserving_order(
                invalid_curps
            )
        )

        recent_in_progress = (
            unique_preserving_order(
                recent_in_progress
            )
        )

        recent_processed = (
            unique_preserving_order(
                recent_processed
            )
        )

        if created_identifiers:
            acknowledgement_text = (
                build_request_acknowledgement(
                    created_identifiers
                )
            )

            ack_sent = (
                await send_group_message(
                    destination_jid=
                        destination_jid,
                    instance=instance,
                    text=acknowledgement_text,
                    log_label="ACK agrupado",
                )
            )

            if not ack_sent:
                retry_id = (
                    uuid.uuid4().hex
                )

                due_at = enqueue_ack_retry(
                    PendingAckRetry(
                        retry_id=retry_id,
                        instance=instance,
                        source_jid=
                            destination_jid,
                        text=
                            acknowledgement_text,
                        attempt=0,
                    )
                )

                logger.warning(
                    "ACK agrupado puesto "
                    "en cola de reintento "
                    "retry_id=%s "
                    "source_jid=%s "
                    "due_at=%s",
                    retry_id,
                    destination_jid,
                    due_at,
                )

        if invalid_curps:
            lines = [
                "⚠️ *CURP con formato incorrecto*",
                "",
            ]

            for invalid_curp in invalid_curps:
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

            await send_group_message(
                destination_jid=
                    destination_jid,
                instance=instance,
                text="\n".join(lines),
                log_label=
                    "aviso CURP inválida agrupado",
            )

        duplicate_lines: list[str] = []

        if recent_in_progress:
            duplicate_lines.extend(
                [
                    "⏳ *Solicitud ya en proceso*",
                    "",
                ]
            )

            for identifier in (
                recent_in_progress
            ):
                duplicate_lines.append(
                    f"• {identifier}"
                )

            duplicate_lines.extend(
                [
                    "",
                    (
                        "Esta solicitud ya está "
                        "siendo procesada."
                    ),
                ]
            )

        if recent_processed:
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

            for identifier in recent_processed:
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
            await send_group_message(
                destination_jid=
                    destination_jid,
                instance=instance,
                text="\n".join(
                    duplicate_lines
                ),
                log_label=
                    "aviso duplicado agrupado",
            )

        for key in successful_keys:
            remove_pending_key(key)

        logger.info(
            "Grupo retrasado procesado "
            "source_jid=%s "
            "messages=%s "
            "created=%s "
            "invalid=%s "
            "in_progress=%s "
            "processed_duplicates=%s",
            destination_jid,
            len(successful_keys),
            len(created_identifiers),
            len(invalid_curps),
            len(recent_in_progress),
            len(recent_processed),
        )

        return len(successful_keys)

    finally:
        db.close()


async def process_due_ack_retries(
    *,
    limit: int = 500,
) -> int:
    keys = get_due_ack_retry_keys(
        limit=limit
    )

    processed = 0

    for key in keys:
        retry = get_pending_ack_retry(
            key
        )

        if retry is None:
            remove_ack_retry_key(
                key
            )
            continue

        sent = await send_group_message(
            destination_jid=
                retry.source_jid,
            instance=
                retry.instance,
            text=
                retry.text,
            log_label=
                "ACK agrupado reintentado",
        )

        if sent:
            remove_ack_retry_key(
                key
            )

            processed += 1

            logger.info(
                "ACK agrupado reenviado "
                "correctamente "
                "retry_id=%s "
                "attempt=%s "
                "source_jid=%s",
                retry.retry_id,
                retry.attempt + 1,
                retry.source_jid,
            )

            continue

        next_attempt = (
            retry.attempt + 1
        )

        if next_attempt >= len(
            ACK_RETRY_DELAYS
        ):
            logger.error(
                "ACK agrupado agotó "
                "reintentos "
                "retry_id=%s "
                "attempts=%s "
                "source_jid=%s",
                retry.retry_id,
                next_attempt,
                retry.source_jid,
            )

            remove_ack_retry_key(
                key
            )

            continue

        remove_ack_retry_key(
            key
        )

        due_at = enqueue_ack_retry(
            PendingAckRetry(
                retry_id=
                    retry.retry_id,
                instance=
                    retry.instance,
                source_jid=
                    retry.source_jid,
                text=
                    retry.text,
                attempt=
                    next_attempt,
            )
        )

        logger.warning(
            "ACK agrupado seguirá "
            "en retry "
            "retry_id=%s "
            "attempt=%s "
            "due_at=%s",
            retry.retry_id,
            next_attempt,
            due_at,
        )

    return processed


async def process_due_messages(
    *,
    limit: int = 5000,
) -> int:
    keys = get_due_keys(
        limit=limit
    )

    groups: dict[
        tuple[str, str, float],
        list[
            tuple[
                str,
                DelayedClientMessage,
            ]
        ],
    ] = defaultdict(list)

    for key in keys:
        pending = get_pending_message(
            key
        )

        if pending is None:
            remove_pending_key(key)

            logger.info(
                "Mensaje retrasado ya no existe: "
                "key=%s",
                key,
            )

            continue

        # Para mensajes creados antes de esta
        # actualización group_due_at puede ser None.
        # En ese caso agrupamos los que ya estén
        # vencidos del mismo cliente.
        group_due_at = (
            float(pending.group_due_at)
            if pending.group_due_at
            is not None
            else 0.0
        )

        group_id = (
            pending.instance,
            pending.source_jid,
            group_due_at,
        )

        groups[group_id].append(
            (
                key,
                pending,
            )
        )

    processed = 0

    for entries in groups.values():
        processed += (
            await process_pending_group(
                entries
            )
        )

    return processed


async def run_worker() -> tuple[int, int]:
    messages_processed = (
        await process_due_messages()
    )

    ack_retries_processed = (
        await process_due_ack_retries()
    )

    return (
        messages_processed,
        ack_retries_processed,
    )


def main() -> None:
    (
        messages_processed,
        ack_retries_processed,
    ) = asyncio.run(
        run_worker()
    )

    print(
        "DELAYED_MESSAGES_PROCESSED="
        f"{messages_processed} "
        "ACK_RETRIES_PROCESSED="
        f"{ack_retries_processed}"
    )


if __name__ == "__main__":
    main()
