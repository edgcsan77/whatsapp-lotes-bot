from dataclasses import dataclass, field
from decimal import Decimal
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.request import Request
from app.services.message_parser import parse_client_message
from app.services.generic_request_parser import (
    extract_generic_requests,
    strip_generic_requests,
)
from app.services.direct_request_parser import (
    extract_direct_requests,
    strip_direct_requests,
)
from app.services.curp_validator import (
    extract_curp_like_candidates,
    validate_curp_format,
)
from app.services.rfc_validator import (
    extract_rfc_like_candidates,
    validate_rfc_format,
)


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
    recent_in_progress_identifiers: list[str] = field(
        default_factory=list
    )
    recent_processed_identifiers: list[str] = field(
        default_factory=list
    )
    ignored_curps: list[str] = field(default_factory=list)
    invalid_curps: list[str] = field(default_factory=list)
    invalid_curp_reasons: dict[str, str] = field(
        default_factory=dict
    )
    invalid_rfcs: list[str] = field(
        default_factory=list
    )
    invalid_rfc_reasons: dict[str, str] = field(
        default_factory=dict
    )
    generic_not_enabled_identifiers: list[
        str
    ] = field(
        default_factory=list
    )
    direct_not_enabled_identifiers: list[
        str
    ] = field(
        default_factory=list
    )
    no_identifiers_found: bool = False

    @property
    def created_count(self) -> int:
        return len(self.created_ids)

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicate_identifiers)


IN_PROGRESS_REQUEST_STATUSES = {
    "RECEIVED",
    "PENDING_CURP_LOOKUP",
    "CURP_LOOKUP_RETRY",
    "PENDING_BATCH",
    "BATCH_CREATED",
    "SENT_TO_PROVIDER",
    "PROVIDER_TIMEOUT",
    "RESULT_RECEIVED",
    "PENDING_PDF",
    "PDF_GENERATING",
    "PDF_RETRY",
}


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
            Client.whatsapp_jid == source_jid,
            Client.deleted_at.is_(None),
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


def _register_client_message_legacy(
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

    curp_candidates = (
        extract_curp_like_candidates(
            message.text
        )
    )

    invalid_curps: list[str] = []
    invalid_curp_reasons: dict[
        str,
        str,
    ] = {}

    for candidate in curp_candidates:
        valid, reason = (
            validate_curp_format(
                candidate
            )
        )

        if valid:
            continue

        invalid_curps.append(
            candidate
        )

        invalid_curp_reasons[
            candidate
        ] = reason

    rfc_candidates = (
        extract_rfc_like_candidates(
            message.text
        )
    )

    invalid_rfcs: list[str] = []
    invalid_rfc_reasons: dict[
        str,
        str,
    ] = {}

    for candidate in rfc_candidates:
        valid, reason = (
            validate_rfc_format(
                candidate
            )
        )

        if valid:
            continue

        invalid_rfcs.append(
            candidate
        )

        invalid_rfc_reasons[
            candidate
        ] = reason

    result = RegistrationResult(
        client_id=client.id,
        client_name=client.name,
        parsed_count=len(parsed_items),
        invalid_curps=invalid_curps,
        invalid_curp_reasons=
            invalid_curp_reasons,
        invalid_rfcs=invalid_rfcs,
        invalid_rfc_reasons=
            invalid_rfc_reasons,
        no_identifiers_found=(
            not parsed_items
            and not invalid_curps
            and not invalid_rfcs
        ),
    )

    if not parsed_items:
        return result

    seen_ignored_curps: set[str] = set()

    for parsed in parsed_items:
        identifier_key = parsed.identifier.strip().upper()

        if parsed.identifier_type == "RFC":
            valid, reason = (
                validate_rfc_format(
                    identifier_key
                )
            )

            if not valid:
                if (
                    identifier_key
                    not in result.invalid_rfcs
                ):
                    result.invalid_rfcs.append(
                        identifier_key
                    )

                result.invalid_rfc_reasons[
                    identifier_key
                ] = reason

                continue

        if parsed.identifier_type == "CURP":
            valid, reason = (
                validate_curp_format(
                    identifier_key
                )
            )

            if not valid:
                if (
                    identifier_key
                    not in result.invalid_curps
                ):
                    result.invalid_curps.append(
                        identifier_key
                    )

                result.invalid_curp_reasons[
                    identifier_key
                ] = reason

                continue

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
            # Mismo webhook/message_id:
            # idempotencia técnica. No genera
            # aviso adicional al cliente.
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

    original_text = str(
        message.text or ""
    )

    # El par RFC + IDCIF expresa una intención
    # inequívoca de constancia directa. Se extrae
    # antes de -G y antes del parser de localización
    # para que jamás termine en proveedor/lote.
    direct_items = (
        extract_direct_requests(
            original_text
        )
    )

    text_without_direct = (
        strip_direct_requests(
            original_text
        )
        if direct_items
        else original_text
    )

    generic_items = (
        extract_generic_requests(
            text_without_direct
        )
    )

    if generic_items:
        localization_text = (
            strip_generic_requests(
                text_without_direct
            )
        )
    else:
        localization_text = (
            text_without_direct
        )

    if direct_items or generic_items:
        legacy_message = (
            IncomingWhatsAppMessage(
                message_id=
                    message.message_id,
                source_jid=
                    message.source_jid,
                sender_jid=
                    message.sender_jid,
                sender_name=
                    message.sender_name,
                text=
                    localization_text,
            )
        )
    else:
        legacy_message = message

    # El flujo normal continúa usando exactamente
    # la registración histórica sobre el texto que
    # no pertenece a servicios PDF especiales.
    result = (
        _register_client_message_legacy(
            db,
            legacy_message,
        )
    )

    result.parsed_count += (
        len(direct_items)
        + len(generic_items)
    )

    if direct_items or generic_items:
        result.no_identifiers_found = False

    # Snapshot del permiso de PDF automático IDCIF
    # únicamente para solicitudes normales.
    if result.created_ids:
        created_requests = list(
            db.scalars(
                select(Request).where(
                    Request.id.in_(
                        result.created_ids
                    )
                )
            )
        )

        for request_item in created_requests:
            request_item.service_type = (
                request_item.service_type
                or "RFC_IDCIF"
            )

            if direct_items or generic_items:
                request_item.original_text = (
                    original_text
                )

            if (
                request_item.service_type
                == "RFC_IDCIF"
                and client.idcif_pdf_enabled
            ):
                request_item.delivery_format = (
                    "PDF"
                )
                request_item.lookup_route = (
                    "DIRECT_RFC_IDCIF"
                )

        db.commit()

    if not direct_items and not generic_items:
        return result

    sender_jid = (
        str(message.sender_jid).strip()
        if message.sender_jid
        else None
    )

    sender_name = (
        str(message.sender_name).strip()
        if message.sender_name
        else None
    )

    now = datetime.now(UTC)

    # ----------------------------------------------------------
    # CONSTANCIA_DIRECTA
    # ----------------------------------------------------------
    for direct in direct_items:
        rfc = direct.rfc.strip().upper()
        idcif = direct.idcif.strip()
        display_identifier = (
            direct.display_identifier
        )

        valid, reason = (
            validate_rfc_format(rfc)
        )

        if not valid:
            if rfc not in result.invalid_rfcs:
                result.invalid_rfcs.append(rfc)
            result.invalid_rfc_reasons[rfc] = reason
            continue

        if not client.direct_pdf_enabled:
            result\
                .direct_not_enabled_identifiers\
                .append(display_identifier)
            continue

        existing_request_id = db.scalar(
            select(Request.id).where(
                Request.whatsapp_message_id
                == message_id,
                Request.identifier_key
                == rfc,
                Request.service_type
                == "CONSTANCIA_DIRECTA",
            )
        )

        if existing_request_id is not None:
            result.duplicate_identifiers.append(
                display_identifier
            )
            continue

        request_item = Request(
            client_id=client.id,
            provider_id=None,
            whatsapp_message_id=message_id,
            identifier_key=rfc,
            source_jid=source_jid,
            sender_jid=sender_jid,
            sender_name=sender_name,
            original_text=original_text,
            input_type="RFC",
            service_type="CONSTANCIA_DIRECTA",
            delivery_format="PDF",
            lookup_route="DIRECT_RFC_IDCIF",
            rfc=rfc,
            original_curp=None,
            detected_name=None,
            idcif=idcif,
            status="PENDING_PDF",
            pdf_status="PENDING",
            pdf_next_attempt_at=now,
            sale_price=(
                client.direct_price_per_request
                or Decimal("0.00")
            ),
        )

        try:
            with db.begin_nested():
                db.add(request_item)
                db.flush()
        except IntegrityError:
            result.duplicate_identifiers.append(
                display_identifier
            )
            continue

        result.created_ids.append(
            request_item.id
        )
        # Marcador interno para que el ACK sepa
        # que este identificador es directo. Nunca
        # se guarda así en DB.
        result.created_identifiers.append(
            f"DIRECT:{rfc}:{idcif}"
        )

    # ----------------------------------------------------------
    # RFC_GENERIC (-G), comportamiento existente.
    # ----------------------------------------------------------
    for generic in generic_items:
        identifier = (
            generic.identifier
            .strip()
            .upper()
        )

        display_identifier = (
            generic.display_identifier
        )

        if (
            generic.identifier_type
            == "CURP"
        ):
            valid, reason = (
                validate_curp_format(
                    identifier
                )
            )

            if not valid:
                if (
                    identifier
                    not in result.invalid_curps
                ):
                    result.invalid_curps.append(
                        identifier
                    )

                result.invalid_curp_reasons[
                    identifier
                ] = reason
                continue

        else:
            valid, reason = (
                validate_rfc_format(
                    identifier
                )
            )

            if not valid:
                if (
                    identifier
                    not in result.invalid_rfcs
                ):
                    result.invalid_rfcs.append(
                        identifier
                    )

                result.invalid_rfc_reasons[
                    identifier
                ] = reason
                continue

        if not client.generic_pdf_enabled:
            result\
                .generic_not_enabled_identifiers\
                .append(display_identifier)
            continue

        existing_request_id = db.scalar(
            select(Request.id).where(
                Request.whatsapp_message_id
                == message_id,
                Request.identifier_key
                == identifier,
                Request.service_type
                == "RFC_GENERIC",
            )
        )

        if existing_request_id is not None:
            result.duplicate_identifiers.append(
                display_identifier
            )
            continue

        request_item = Request(
            client_id=client.id,
            provider_id=None,
            whatsapp_message_id=
                message_id,
            identifier_key=
                identifier,
            source_jid=
                source_jid,
            sender_jid=
                sender_jid,
            sender_name=
                sender_name,
            original_text=
                original_text,
            input_type=
                generic.identifier_type,
            service_type=
                "RFC_GENERIC",
            delivery_format=
                "PDF",
            lookup_route=
                generic.lookup_route,
            rfc=
                generic.rfc,
            original_curp=
                generic.curp,
            detected_name=None,
            status=
                "PENDING_PDF",
            pdf_status=
                "PENDING",
            pdf_next_attempt_at=
                now,
            sale_price=(
                client
                .generic_price_per_request
                or Decimal("0.00")
            ),
        )

        try:
            with db.begin_nested():
                db.add(request_item)
                db.flush()
        except IntegrityError:
            result.duplicate_identifiers.append(
                display_identifier
            )
            continue

        result.created_ids.append(
            request_item.id
        )
        result.created_identifiers.append(
            display_identifier
        )

    db.commit()
    return result
