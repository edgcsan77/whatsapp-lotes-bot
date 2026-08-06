import hmac
from dataclasses import dataclass
from typing import Any


SUPPORTED_EVENTS = {
    "MESSAGES_UPSERT",
    "messages.upsert",
    "messages-upsert",
}


@dataclass(frozen=True)
class EvolutionIncomingMessage:
    instance: str
    event: str
    message_id: str
    source_jid: str
    sender_jid: str | None
    sender_name: str | None
    text: str
    from_me: bool


def nested_get(
    payload: dict[str, Any],
    *path: str,
    default: Any = None,
) -> Any:
    current: Any = payload

    for key in path:
        if not isinstance(current, dict):
            return default

        current = current.get(key)

        if current is None:
            return default

    return current


def first_non_empty(*values: Any) -> str:
    for value in values:
        normalized = str(value or "").strip()

        if normalized:
            return normalized

    return ""


def extract_message_text(
    message: dict[str, Any],
) -> str:
    return first_non_empty(
        message.get("conversation"),
        nested_get(
            message,
            "extendedTextMessage",
            "text",
        ),
        nested_get(
            message,
            "imageMessage",
            "caption",
        ),
        nested_get(
            message,
            "videoMessage",
            "caption",
        ),
        nested_get(
            message,
            "documentMessage",
            "caption",
        ),
        nested_get(
            message,
            "buttonsResponseMessage",
            "selectedDisplayText",
        ),
        nested_get(
            message,
            "listResponseMessage",
            "title",
        ),
    )


def normalize_event(value: Any) -> str:
    return str(value or "").strip()


def is_supported_event(event: str) -> bool:
    return event in SUPPORTED_EVENTS


def secrets_match(
    provided: str | None,
    expected: str,
) -> bool:
    provided_value = str(provided or "")
    expected_value = str(expected or "")

    if not provided_value or not expected_value:
        return False

    return hmac.compare_digest(
        provided_value,
        expected_value,
    )


def parse_evolution_payload(
    payload: dict[str, Any],
) -> EvolutionIncomingMessage | None:
    if not isinstance(payload, dict):
        return None

    event = normalize_event(
        first_non_empty(
            payload.get("event"),
            payload.get("type"),
        )
    )

    if not is_supported_event(event):
        return None

    instance = first_non_empty(
        payload.get("instance"),
        nested_get(
            payload,
            "data",
            "instance",
        ),
        nested_get(
            payload,
            "data",
            "instanceName",
        ),
    )

    data = payload.get("data")

    if not isinstance(data, dict):
        data = {}

    key = data.get("key")

    if not isinstance(key, dict):
        key = {}

    message = data.get("message")

    if not isinstance(message, dict):
        message = {}

    message_id = first_non_empty(
        key.get("id"),
        data.get("id"),
        payload.get("messageId"),
    )

    source_jid = first_non_empty(
        key.get("remoteJid"),
        data.get("remoteJid"),
        data.get("chatId"),
    )

    sender_jid = first_non_empty(
        key.get("participant"),
        key.get("participantAlt"),
        data.get("participant"),
        data.get("sender"),
    ) or None

    sender_name = first_non_empty(
        data.get("pushName"),
        data.get("senderName"),
        payload.get("senderName"),
    ) or None

    from_me = bool(
        key.get("fromMe")
        or data.get("fromMe")
    )

    text = extract_message_text(
        message
    )

    if not (
        instance
        and message_id
        and source_jid
    ):
        return None

    return EvolutionIncomingMessage(
        instance=instance,
        event=event,
        message_id=message_id,
        source_jid=source_jid,
        sender_jid=sender_jid,
        sender_name=sender_name,
        text=text,
        from_me=from_me,
    )
