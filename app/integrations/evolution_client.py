import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings


logger = logging.getLogger(__name__)


class EvolutionAPIError(Exception):
    """Error al comunicarse con Evolution API."""


@dataclass(frozen=True)
class EvolutionSendResult:
    ok: bool
    message_id: str | None
    raw_response: dict[str, Any]


def normalize_jid(value: str) -> str:
    jid = str(value or "").strip()

    if not jid:
        raise ValueError("DESTINATION_JID_EMPTY")

    return jid


async def send_text_message(
    *,
    destination_jid: str,
    text: str,
    instance: str | None = None,
) -> EvolutionSendResult:
    destination_jid = normalize_jid(
        destination_jid
    )

    message_text = str(text or "").strip()

    if not message_text:
        raise ValueError("MESSAGE_TEXT_EMPTY")

    instance_name = (
        str(instance or settings.evolution_instance)
        .strip()
    )

    if not instance_name:
        raise ValueError("EVOLUTION_INSTANCE_EMPTY")

    base_url = str(
        settings.evolution_base_url
    ).rstrip("/")

    url = (
        f"{base_url}/message/sendText/"
        f"{instance_name}"
    )

    payload = {
        "number": destination_jid,
        "text": message_text,
    }

    headers = {
        "apikey": settings.evolution_api_key,
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(
        connect=10.0,
        read=30.0,
        write=30.0,
        pool=10.0,
    )

    try:
        async with httpx.AsyncClient(
            timeout=timeout
        ) as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
            )

    except httpx.HTTPError as error:
        logger.exception(
            "Evolution sendText connection error"
        )

        raise EvolutionAPIError(
            "EVOLUTION_CONNECTION_ERROR"
        ) from error

    try:
        response_data = response.json()
    except ValueError:
        response_data = {
            "raw_text": response.text,
        }

    if response.status_code >= 400:
        logger.error(
            "Evolution sendText failed: "
            "status=%s response=%s",
            response.status_code,
            response_data,
        )

        raise EvolutionAPIError(
            f"EVOLUTION_HTTP_{response.status_code}"
        )

    message_id = None

    if isinstance(response_data, dict):
        key = response_data.get("key")

        if isinstance(key, dict):
            message_id = str(
                key.get("id") or ""
            ).strip() or None

        if message_id is None:
            message_id = str(
                response_data.get("messageId")
                or response_data.get("id")
                or ""
            ).strip() or None

    return EvolutionSendResult(
        ok=True,
        message_id=message_id,
        raw_response=response_data,
    )
