from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.request import Request
from app.services.message_parser import parse_client_message


class RequestRegistrationError(Exception):
    """Error base al registrar mensajes de clientes."""


class ClientNotFoundError(RequestRegistrationError):
    """El chat o grupo no está registrado como cliente."""


class ClientInactiveError(RequestRegistrationError):
    """El cliente existe, pero está desactivado."""


@dataclass(frozen=True)
class IncomingWhatsAppMessage:
    message_id: str
    source_jid: str
    sender_jid: str | None
    sender_name: str | None
    text: str


@dataclass
class RegistrationResult:
    client_id: int
    client_name: str
    parsed_count: int = 0
    created_ids: list[int] = field(default_factory=list)
    created_identifiers: list[str] = field(default_factory=list)
    duplicate_identifiers: list[str] = field(default_factory=list)
    ignored_curps: list[str] = field(default_factory=list)
    no_identifiers_found: bool = False

    @property
    def created_count(self) -> int:
        return len(self.created_ids)

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicate_identifiers)


def normalize_required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()

    if not normalized:
        raise ValueError(f"{field_name}_EMPTY")

    return normalized


def get_client_by_source_jid(
    db: Session,
    source_jid: str,
) -> Client:
    source_jid = normalize_required(
        source_jid,
        "SOURCE_JID",
    )

    client = db.scalar(
        select(Client).where(
            Client.whatsapp_jid == source_jid
        )
    )

    if client is None:
        raise ClientNotFoundError(
            f"CLIENT_NOT_FOUND:{source_jid}"
        )

    if not client.active:
        raise ClientInactiveError(
            f"CLIENT_INACTIVE:{source_jid}"
        )

    return client


def register_client_message(
    db: Session,
    message: IncomingWhatsAppMessage,
) -> RegistrationResult:
    message_id = normalize_required(
        message.message_id,
        "MESSAGE_ID",
    )

    source_jid = normalize_required(
        message.source_jid,
        "SOURCE_JID",
    )

    client = get_client_by_source_jid(
        db=db,
        source_jid=source_jid,
    )

    parsed_items = parse_client_message(
        message.text
    )

    result = RegistrationResult(
        client_id=client.id,
        client_name=client.name,
        parsed_count=len(parsed_items),
        no_identifiers_found=not parsed_items,
    )

    if not parsed_items:
        return result

    seen_ignored_curps: set[str] = set()

    for parsed in parsed_items:
        identifier_key = parsed.identifier.strip().upper()

        for ignored_curp in parsed.ignored_curps:
            ignored_curp = ignored_curp.strip().upper()

            if ignored_curp not in seen_ignored_curps:
                seen_ignored_curps.add(ignored_curp)
                result.ignored_curps.append(
                    ignored_curp
                )

        existing_id = db.scalar(
            select(Request.id).where(
                Request.whatsapp_message_id
                == message_id,
                Request.identifier_key
                == identifier_key,
            )
        )

        if existing_id is not None:
            result.duplicate_identifiers.append(
                identifier_key
            )
            continue

        if parsed.identifier_type == "RFC":
            request_status = "PENDING_BATCH"
            rfc = parsed.rfc
            original_curp = None
        else:
            request_status = "PENDING_CURP_LOOKUP"
            rfc = None
            original_curp = parsed.curp

        request = Request(
            client_id=client.id,
            provider_id=client.default_provider_id,
            whatsapp_message_id=message_id,
            identifier_key=identifier_key,
            source_jid=source_jid,
            sender_jid=(
                str(message.sender_jid).strip()
                if message.sender_jid
                else None
            ),
            sender_name=(
                str(message.sender_name).strip()
                if message.sender_name
                else None
            ),
            original_text=str(message.text or ""),
            input_type=parsed.identifier_type,
            rfc=rfc,
            original_curp=original_curp,
            detected_name=parsed.detected_name,
            status=request_status,
            sale_price=(
                client.price_per_request
                or Decimal("0.00")
            ),
        )

        try:
            # SAVEPOINT: si existe una carrera por webhook
            # duplicado, solo se revierte esta solicitud.
            with db.begin_nested():
                db.add(request)
                db.flush()

        except IntegrityError:
            result.duplicate_identifiers.append(
                identifier_key
            )
            continue

        result.created_ids.append(request.id)
        result.created_identifiers.append(
            identifier_key
        )

    db.commit()

    return result
