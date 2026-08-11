import logging
import subprocess
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.batch import Batch, BatchItem
from app.models.admin_audit_log import AdminAuditLog
from app.models.client import Client
from app.models.daily_cutoff import DailyCutoff
from app.models.provider import Provider
from app.models.request import Request as RequestModel


load_dotenv()

logger = logging.getLogger(__name__)


from app.integrations.evolution_client import (
    EvolutionAPIError,
    send_text_message,
)
from app.services.batch_service import (
    BatchServiceError,
    send_existing_batch,
)
from app.services.curp_processing_service import (
    clear_curp_retry_state,
)
from app.services.curp_rfc_engine import (
    CurpRfcError,
    convert_curp_to_rfc,
)
from app.services.message_parser import (
    extract_curps,
    normalize_text,
)
from app.services.retry_service import (
    build_delivery_retry_text,
    clear_retry_state,
)

router = APIRouter(
    prefix="/panel",
    tags=["panel"],
)

templates = Jinja2Templates(
    directory="app/templates"
)


AVAILABLE_TIMEZONES = [
    {
        "value": "America/Mexico_City",
        "label": "Ciudad de México",
    },
    {
        "value": "America/Monterrey",
        "label": "Monterrey",
    },
    {
        "value": "America/Cancun",
        "label": "Cancún / Quintana Roo",
    },
    {
        "value": "America/Tijuana",
        "label": "Tijuana",
    },
]


def get_admin_user() -> str:
    return str(
        os.getenv("PANEL_ADMIN_USER", "")
    ).strip()


def get_admin_password_hash() -> str:
    return str(
        os.getenv(
            "PANEL_ADMIN_PASSWORD_HASH",
            "",
        )
    ).strip()


def verify_password(
    password: str,
    stored_value: str,
) -> bool:
    try:
        salt_hex, expected_hex = stored_value.split(
            ":",
            1,
        )

        calculated = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            310_000,
        ).hex()

    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(
        calculated,
        expected_hex,
    )


def ensure_csrf_token(
    request: Request,
) -> str:
    token = request.session.get(
        "panel_csrf_token"
    )

    if not token:
        token = secrets.token_urlsafe(32)
        request.session["panel_csrf_token"] = token

    return token


PANEL_TIMEZONE = ZoneInfo(
    "America/Mexico_City"
)


PANEL_STATUS_LABELS = {
    # Solicitudes
    "RECEIVED":
        "Recibida",
    "PENDING_CURP_LOOKUP":
        "Pendiente de consulta CURP",
    "CURP_LOOKUP_RETRY":
        "Reintentando consulta CURP",
    "CURP_LOOKUP_FAILED":
        "Error al consultar CURP",
    "PENDING_BATCH":
        "Pendiente de lote",
    "BATCH_CREATED":
        "Agregada a lote",
    "SENT_TO_PROVIDER":
        "Enviada al proveedor",
    "RESULT_RECEIVED":
        "Resultado recibido",
    "DELIVERY_FAILED":
        "Error de entrega",
    "DELIVERED":
        "Entregada",

    # Lotes / cortes
    "OPEN":
        "Abierto",
    "CREATED":
        "Creado",
    "SENT":
        "Enviado",
    "CLOSED":
        "Cerrado",
    "FAILED":
        "Fallido",
    "SEND_FAILED":
        "Error de envío",
    "BATCH_SEND_FAILED":
        "Error al enviar lote",

    # Estados comunes adicionales
    "ACTIVE":
        "Activo",
    "INACTIVE":
        "Inactivo",
    "PENDING":
        "Pendiente",
}


def panel_status(
    value: str | None,
) -> str:
    raw = str(
        value or ""
    ).strip().upper()

    if not raw:
        return "—"

    return PANEL_STATUS_LABELS.get(
        raw,
        raw.replace("_", " ").title(),
    )


def panel_datetime(
    value: datetime | None,
    fmt: str = "%d/%m/%Y %H:%M:%S",
) -> str:
    if value is None:
        return "—"

    if not isinstance(
        value,
        datetime,
    ):
        return str(value)

    normalized = value

    # Los timestamps de la aplicación se guardan
    # en UTC. Si el driver entrega uno naive,
    # lo interpretamos como UTC.
    if normalized.tzinfo is None:
        normalized = normalized.replace(
            tzinfo=UTC
        )

    local_value = normalized.astimezone(
        PANEL_TIMEZONE
    )

    return local_value.strftime(
        fmt
    )


templates.env.filters[
    "panel_status"
] = panel_status

templates.env.filters[
    "panel_datetime"
] = panel_datetime

templates.env.globals[
    "panel_status"
] = panel_status

templates.env.globals[
    "panel_datetime"
] = panel_datetime


def validate_csrf(
    request: Request,
    provided_token: str,
) -> bool:
    expected = str(
        request.session.get(
            "panel_csrf_token",
            "",
        )
    )

    return bool(
        expected
        and provided_token
        and hmac.compare_digest(
            expected,
            provided_token,
        )
    )


def is_authenticated(
    request: Request,
) -> bool:
    return bool(
        request.session.get(
            "panel_authenticated"
        )
    )


def require_authenticated(
    request: Request,
) -> RedirectResponse | None:
    if is_authenticated(request):
        return None

    return RedirectResponse(
        url="/panel/login",
        status_code=303,
    )


def validate_cutoff_time(
    value: str,
) -> str:
    raw = str(value or "").strip()

    try:
        parsed = datetime.strptime(
            raw,
            "%H:%M",
        )
    except ValueError as error:
        raise ValueError(
            "La hora de corte debe tener "
            "formato HH:MM."
        ) from error

    return parsed.strftime("%H:%M")


def validate_timezone(
    value: str,
) -> str:
    raw = str(value or "").strip()

    allowed_values = {
        item["value"]
        for item in AVAILABLE_TIMEZONES
    }

    if raw not in allowed_values:
        raise ValueError(
            "Zona horaria no permitida."
        )

    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError as error:
        raise ValueError(
            "Zona horaria inválida."
        ) from error

    return raw


def calculate_next_cutoff_local(
    client: Client,
    *,
    now_utc: datetime | None = None,
) -> datetime | None:
    if (
        not client.active
        or not client.daily_cutoff_enabled
    ):
        return None

    try:
        timezone = ZoneInfo(
            client.timezone
        )

        cutoff_time = datetime.strptime(
            client.daily_cutoff_time,
            "%H:%M",
        ).time()

    except (
        ValueError,
        ZoneInfoNotFoundError,
    ):
        return None

    current_utc = (
        now_utc
        if now_utc is not None
        else datetime.now(UTC)
    )

    local_now = current_utc.astimezone(
        timezone
    )

    next_cutoff = datetime.combine(
        local_now.date(),
        cutoff_time,
        tzinfo=timezone,
    )

    if next_cutoff <= local_now:
        next_cutoff += timedelta(days=1)

    return next_cutoff


def checkbox_value(
    value: str | None,
) -> bool:
    return str(value or "").lower() in {
        "on",
        "true",
        "1",
        "yes",
    }


def parse_price(value: str) -> Decimal:
    try:
        price = Decimal(
            str(value).strip()
        ).quantize(
            Decimal("0.01")
        )
    except InvalidOperation as error:
        raise ValueError(
            "Precio inválido"
        ) from error

    if price < 0:
        raise ValueError(
            "El precio no puede ser negativo"
        )

    return price


def redirect_clients(
    *,
    message: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    query_parts: list[str] = []

    if message:
        query_parts.append(
            f"message={quote(message)}"
        )

    if error:
        query_parts.append(
            f"error={quote(error)}"
        )

    url = "/panel/clients"

    if query_parts:
        url += "?" + "&".join(query_parts)

    return RedirectResponse(
        url=url,
        status_code=303,
    )


@router.get(
    "/login",
    response_class=HTMLResponse,
)
def login_page(
    request: Request,
):
    if is_authenticated(request):
        return RedirectResponse(
            url="/panel",
            status_code=303,
        )

    return templates.TemplateResponse(
        request=request,
        name="panel/login.html",
        context={
            "request": request,
            "title": "Iniciar sesión",
            "csrf_token":
                ensure_csrf_token(request),
            "error": None,
        },
    )


@router.post(
    "/login",
    response_class=HTMLResponse,
)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf(
        request,
        csrf_token,
    ):
        return templates.TemplateResponse(
            request=request,
            name="panel/login.html",
            context={
                "request": request,
                "title": "Iniciar sesión",
                "csrf_token":
                    ensure_csrf_token(request),
                "error":
                    "La sesión expiró. Intenta nuevamente.",
            },
            status_code=400,
        )

    valid_user = hmac.compare_digest(
        username.strip(),
        get_admin_user(),
    )

    valid_password = verify_password(
        password,
        get_admin_password_hash(),
    )

    if not (
        valid_user
        and valid_password
    ):
        return templates.TemplateResponse(
            request=request,
            name="panel/login.html",
            context={
                "request": request,
                "title": "Iniciar sesión",
                "csrf_token":
                    ensure_csrf_token(request),
                "error":
                    "Usuario o contraseña incorrectos.",
            },
            status_code=401,
        )

    request.session.clear()
    request.session[
        "panel_authenticated"
    ] = True

    ensure_csrf_token(request)

    return RedirectResponse(
        url="/panel",
        status_code=303,
    )


@router.get("/logout")
def logout(
    request: Request,
) -> RedirectResponse:
    request.session.clear()

    return RedirectResponse(
        url="/panel/login",
        status_code=303,
    )


@router.get(
    "/clients",
    response_class=HTMLResponse,
)
def clients_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    clients = list(
        db.scalars(
            select(Client)
            .where(
                Client.deleted_at.is_(None)
            )
            .order_by(
                Client.active.desc(),
                Client.name.asc(),
            )
        )
    )

    daily_cutoffs = list(
        db.scalars(
            select(DailyCutoff)
            .order_by(
                DailyCutoff.period_end.desc(),
                DailyCutoff.id.desc(),
            )
            .limit(100)
        )
    )

    client_names = {
        client.id: client.name
        for client in clients
    }

    next_cutoffs = {
        client.id: calculate_next_cutoff_local(
            client
        )
        for client in clients
    }

    client_request_counts = {
        int(client_id): int(total)
        for client_id, total in db.execute(
            select(
                RequestModel.client_id,
                func.count(RequestModel.id),
            )
            .where(
                RequestModel.client_id.is_not(None),
                RequestModel.status == "DELIVERED",
                RequestModel.result_code == "OK",
                RequestModel.idcif.is_not(None),
                RequestModel.idcif != "",
            )
            .group_by(
                RequestModel.client_id
            )
        ).all()
    }

    return templates.TemplateResponse(
        request=request,
        name="panel/clients.html",
        context={
            "request": request,
            "title": "Clientes",
            "active_page": "clients",
            "clients": clients,
            "daily_cutoffs": daily_cutoffs,
            "client_names": client_names,
            "next_cutoffs": next_cutoffs,
            "client_request_counts":
                client_request_counts,
            "available_timezones":
                AVAILABLE_TIMEZONES,
            "csrf_token":
                ensure_csrf_token(request),
            "message": message,
            "error": error,
        },
    )


@router.post("/clients/create")
def create_client(
    request: Request,
    name: str = Form(...),
    source_type: str = Form(...),
    whatsapp_jid: str = Form(...),
    price_per_request: str = Form(...),
    batch_interval_minutes: int = Form(...),
    batch_max_items: int = Form(...),
    daily_cutoff_time: str = Form(...),
    timezone: str = Form(...),
    csrf_token: str = Form(...),
    batch_enabled: str | None = Form(None),
    daily_cutoff_enabled: str | None = Form(
        None
    ),
    active: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return redirect_clients(
            error="Token de seguridad inválido."
        )

    source_type = source_type.strip().lower()

    if source_type not in {
        "group",
        "private",
    }:
        return redirect_clients(
            error="Tipo de cliente inválido."
        )

    if not name.strip():
        return redirect_clients(
            error="El nombre es obligatorio."
        )

    jid = whatsapp_jid.strip()

    if not jid:
        return redirect_clients(
            error="El JID es obligatorio."
        )

    if source_type == "group" and not jid.endswith(
        "@g.us"
    ):
        return redirect_clients(
            error="El JID del grupo debe terminar en @g.us."
        )

    if not 1 <= batch_interval_minutes <= 1440:
        return redirect_clients(
            error="Intervalo de lote inválido."
        )

    if not 1 <= batch_max_items <= 1000:
        return redirect_clients(
            error="Máximo por lote inválido."
        )

    try:
        price = parse_price(
            price_per_request
        )

        validated_cutoff_time = (
            validate_cutoff_time(
                daily_cutoff_time
            )
        )

        validated_timezone = (
            validate_timezone(
                timezone
            )
        )

    except ValueError as error:
        return redirect_clients(
            error=str(error)
        )

    client = Client(
        name=name.strip(),
        source_type=source_type,
        whatsapp_jid=jid,
        price_per_request=price,
        batch_enabled=checkbox_value(
            batch_enabled
        ),
        batch_interval_minutes=
            batch_interval_minutes,
        batch_max_items=batch_max_items,
        daily_cutoff_enabled=checkbox_value(
            daily_cutoff_enabled
        ),
        daily_cutoff_time=
            validated_cutoff_time,
        timezone=validated_timezone,
        active=checkbox_value(active),
    )

    db.add(client)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

        return redirect_clients(
            error=(
                "Ese JID ya está registrado "
                "como cliente."
            )
        )

    return redirect_clients(
        message="Cliente agregado correctamente."
    )


@router.post(
    "/clients/{client_id}/delete"
)
def delete_client(
    client_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return redirect_clients(
            error="Token de seguridad inválido."
        )

    client = db.get(
        Client,
        client_id,
    )

    if client is None:
        return redirect_clients(
            error="Cliente no encontrado."
        )

    if client.deleted_at is not None:
        return redirect_clients(
            message=(
                "El cliente ya estaba eliminado."
            )
        )

    client_name = client.name
    client_jid = client.whatsapp_jid

    # Eliminación lógica:
    # conservamos solicitudes, lotes,
    # cortes y auditoría histórica.
    client.active = False
    client.batch_enabled = False
    client.daily_cutoff_enabled = False
    client.deleted_at = datetime.now(UTC)

    register_admin_audit(
        db,
        request,
        action="CLIENT_DELETED",
        entity_type="CLIENT",
        entity_id=client.id,
        summary=(
            f"Cliente #{client.id} eliminado"
        ),
        details=(
            f"Nombre: {client_name}\n"
            f"JID: {client_jid}\n"
            "Tipo: eliminación lógica; "
            "historial conservado"
        ),
    )

    db.commit()

    return redirect_clients(
        message=(
            f"Cliente «{client_name}» "
            "eliminado correctamente. "
            "Su historial se conservó."
        )
    )


@router.post(
    "/clients/{client_id}/update"
)
def update_client(
    client_id: int,
    request: Request,
    name: str = Form(...),
    source_type: str = Form(...),
    whatsapp_jid: str = Form(...),
    price_per_request: str = Form(...),
    batch_interval_minutes: int = Form(...),
    batch_max_items: int = Form(...),
    daily_cutoff_time: str = Form(...),
    timezone: str = Form(...),
    csrf_token: str = Form(...),
    batch_enabled: str | None = Form(None),
    daily_cutoff_enabled: str | None = Form(
        None
    ),
    active: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return redirect_clients(
            error="Token de seguridad inválido."
        )

    client = db.get(
        Client,
        client_id,
    )

    if client is None:
        return redirect_clients(
            error="Cliente no encontrado."
        )

    client_before = {
        "name":
            client.name,
        "source_type":
            client.source_type,
        "whatsapp_jid":
            client.whatsapp_jid,
        "price_per_request":
            str(client.price_per_request),
        "batch_enabled":
            client.batch_enabled,
        "batch_interval_minutes":
            client.batch_interval_minutes,
        "batch_max_items":
            client.batch_max_items,
        "daily_cutoff_enabled":
            client.daily_cutoff_enabled,
        "daily_cutoff_time":
            client.daily_cutoff_time,
        "timezone":
            client.timezone,
        "active":
            client.active,
    }

    try:
        price = parse_price(
            price_per_request
        )

        validated_cutoff_time = (
            validate_cutoff_time(
                daily_cutoff_time
            )
        )

        validated_timezone = (
            validate_timezone(
                timezone
            )
        )

    except ValueError as error:
        return redirect_clients(
            error=str(error)
        )

    if not 1 <= batch_interval_minutes <= 1440:
        return redirect_clients(
            error="Intervalo de lote inválido."
        )

    if not 1 <= batch_max_items <= 1000:
        return redirect_clients(
            error="Máximo por lote inválido."
        )

    client.name = name.strip()
    client.source_type = source_type.strip()
    client.whatsapp_jid = (
        whatsapp_jid.strip()
    )
    client.price_per_request = price
    client.batch_enabled = checkbox_value(
        batch_enabled
    )
    client.batch_interval_minutes = (
        batch_interval_minutes
    )
    client.batch_max_items = (
        batch_max_items
    )
    client.daily_cutoff_enabled = (
        checkbox_value(
            daily_cutoff_enabled
        )
    )
    client.daily_cutoff_time = (
        validated_cutoff_time
    )
    client.timezone = (
        validated_timezone
    )
    client.active = checkbox_value(
        active
    )

    client_after = {
        "name":
            client.name,
        "source_type":
            client.source_type,
        "whatsapp_jid":
            client.whatsapp_jid,
        "price_per_request":
            str(client.price_per_request),
        "batch_enabled":
            client.batch_enabled,
        "batch_interval_minutes":
            client.batch_interval_minutes,
        "batch_max_items":
            client.batch_max_items,
        "daily_cutoff_enabled":
            client.daily_cutoff_enabled,
        "daily_cutoff_time":
            client.daily_cutoff_time,
        "timezone":
            client.timezone,
        "active":
            client.active,
    }

    changed_fields = []

    for key in client_before:
        if (
            client_before[key]
            != client_after[key]
        ):
            changed_fields.append(
                f"{key}: "
                f"{client_before[key]} -> "
                f"{client_after[key]}"
            )

    try:
        if changed_fields:
            register_admin_audit(
                db,
                request,
                action="CLIENT_UPDATED",
                entity_type="CLIENT",
                entity_id=client.id,
                summary=(
                    f"Cliente #{client.id} "
                    "actualizado"
                ),
                details="\\n".join(
                    changed_fields
                ),
            )

        db.commit()

    except IntegrityError:
        db.rollback()

        return redirect_clients(
            error=(
                "No se pudo guardar. "
                "El JID ya pertenece a otro cliente."
            )
        )

    return redirect_clients(
        message="Cliente actualizado correctamente."
    )


def register_admin_audit(
    db: Session,
    request: Request,
    *,
    action: str,
    entity_type: str,
    entity_id: int | None,
    summary: str,
    details: str | None = None,
) -> AdminAuditLog:
    admin_user = str(
        request.session.get(
            "panel_admin_user",
            "",
        )
        or get_admin_user()
    ).strip()

    forwarded_for = str(
        request.headers.get(
            "x-forwarded-for",
            "",
        )
    ).strip()

    if forwarded_for:
        ip_address = (
            forwarded_for
            .split(",", 1)[0]
            .strip()
        )
    elif request.client:
        ip_address = (
            request.client.host
        )
    else:
        ip_address = None

    log = AdminAuditLog(
        admin_user=admin_user or "admin",
        action=str(action).strip().upper(),
        entity_type=(
            str(entity_type)
            .strip()
            .upper()
        ),
        entity_id=entity_id,
        summary=str(summary).strip(),
        details=(
            str(details).strip()
            if details
            else None
        ),
        ip_address=ip_address,
    )

    db.add(log)

    return log


def _systemctl_show(
    unit_name: str,
    properties: list[str],
) -> dict[str, str]:
    command = [
        "systemctl",
        "show",
        unit_name,
    ]

    for prop in properties:
        command.extend(
            ["--property", prop]
        )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            env={
                **os.environ,
                "TZ": "America/Mexico_City",
            },
        )
    except Exception as exc:
        return {
            "error": str(exc),
        }

    if result.returncode != 0:
        return {
            "error": (
                result.stderr.strip()
                or "No fue posible consultar systemd."
            ),
        }

    values: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if "=" not in line:
            continue

        key, value = line.split(
            "=",
            1,
        )

        values[key] = value

    return values


def _format_systemd_time(
    value: str | None,
) -> str:
    raw = str(
        value or ""
    ).strip()

    if not raw or raw in {
        "n/a",
        "0",
        "—",
    }:
        return "—"

    parts = raw.split()

    # systemd normalmente devuelve:
    # Fri 2026-08-07 23:25:29 CST
    if (
        len(parts) >= 3
        and len(parts[1]) == 10
        and parts[1][4] == "-"
        and parts[1][7] == "-"
    ):
        try:
            parsed = datetime.strptime(
                f"{parts[1]} {parts[2]}",
                "%Y-%m-%d %H:%M:%S",
            )

            return parsed.strftime(
                "%d/%m/%Y %H:%M:%S"
            )
        except ValueError:
            pass

    return raw


def _service_health(
    unit_name: str,
) -> dict[str, str | bool]:
    data = _systemctl_show(
        unit_name,
        [
            "ActiveState",
            "SubState",
            "Result",
            "ExecMainStatus",
        ],
    )

    if data.get("error"):
        return {
            "unit": unit_name,
            "healthy": False,
            "active_state": "unknown",
            "sub_state": "unknown",
            "result": "unknown",
            "error": data["error"],
        }

    active_state = data.get(
        "ActiveState",
        "unknown",
    )

    result = data.get(
        "Result",
        "",
    )

    healthy = (
        active_state == "active"
        and result in {
            "",
            "success",
        }
    )

    return {
        "unit": unit_name,
        "healthy": healthy,
        "active_state": active_state,
        "sub_state": data.get(
            "SubState",
            "unknown",
        ),
        "result": result or "—",
        "error": "",
    }


def _timer_health(
    timer_name: str,
    service_name: str,
) -> dict[str, str | bool]:
    timer = _systemctl_show(
        timer_name,
        [
            "ActiveState",
            "SubState",
            "LastTriggerUSec",
            "NextElapseUSecRealtime",
            "NextElapseUSecMonotonic",
        ],
    )

    service = _systemctl_show(
        service_name,
        [
            "Result",
            "ExecMainStatus",
            "ActiveState",
        ],
    )

    if timer.get("error"):
        return {
            "timer": timer_name,
            "service": service_name,
            "healthy": False,
            "active_state": "unknown",
            "sub_state": "unknown",
            "last_result": "unknown",
            "last_trigger": "—",
            "next_trigger": "—",
            "error": timer["error"],
        }

    timer_active = (
        timer.get("ActiveState")
        == "active"
    )

    last_result = service.get(
        "Result",
        "",
    )

    last_ok = last_result in {
        "",
        "success",
    }

    last_trigger = _format_systemd_time(
        timer.get(
            "LastTriggerUSec"
        )
    )

    next_realtime = str(
        timer.get(
            "NextElapseUSecRealtime",
            "",
        )
        or ""
    ).strip()

    next_monotonic = str(
        timer.get(
            "NextElapseUSecMonotonic",
            "",
        )
        or ""
    ).strip()

    if next_realtime:
        next_trigger = (
            _format_systemd_time(
                next_realtime
            )
        )
    elif (
        timer_active
        and next_monotonic
        and next_monotonic != "0"
    ):
        next_trigger = "Programado"
    else:
        next_trigger = "—"

    return {
        "timer": timer_name,
        "service": service_name,
        "healthy": (
            timer_active
            and last_ok
        ),
        "active_state": timer.get(
            "ActiveState",
            "unknown",
        ),
        "sub_state": timer.get(
            "SubState",
            "unknown",
        ),
        "last_result": (
            last_result or "—"
        ),
        "last_trigger":
            last_trigger,
        "next_trigger":
            next_trigger,
        "error": "",
    }




@router.get(
    "/audit",
    response_class=HTMLResponse,
)
def audit_page(
    request: Request,
    action: str | None = None,
    entity_type: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    page_size = 50
    page = max(page, 1)

    normalized_action = str(
        action or ""
    ).strip().upper()

    normalized_entity_type = str(
        entity_type or ""
    ).strip().upper()

    statement = select(
        AdminAuditLog
    )

    if normalized_action:
        statement = statement.where(
            AdminAuditLog.action
            == normalized_action
        )

    if normalized_entity_type:
        statement = statement.where(
            AdminAuditLog.entity_type
            == normalized_entity_type
        )

    total = int(
        db.scalar(
            select(func.count())
            .select_from(
                statement
                .order_by(None)
                .subquery()
            )
        )
        or 0
    )

    total_pages = max(
        1,
        (total + page_size - 1)
        // page_size,
    )

    if page > total_pages:
        page = total_pages

    logs = list(
        db.scalars(
            statement
            .order_by(
                AdminAuditLog
                .created_at.desc(),
                AdminAuditLog.id.desc(),
            )
            .offset(
                (page - 1)
                * page_size
            )
            .limit(page_size)
        )
    )

    action_options = list(
        db.scalars(
            select(
                AdminAuditLog.action
            )
            .distinct()
            .order_by(
                AdminAuditLog.action
            )
        )
    )

    entity_options = list(
        db.scalars(
            select(
                AdminAuditLog.entity_type
            )
            .distinct()
            .order_by(
                AdminAuditLog.entity_type
            )
        )
    )

    query_params = []

    if normalized_action:
        query_params.append(
            "action="
            + quote(normalized_action)
        )

    if normalized_entity_type:
        query_params.append(
            "entity_type="
            + quote(
                normalized_entity_type
            )
        )

    return templates.TemplateResponse(
        request=request,
        name="panel/audit.html",
        context={
            "request": request,
            "title":
                "Auditoría",
            "active_page":
                "audit",
            "logs":
                logs,
            "action_options":
                action_options,
            "entity_options":
                entity_options,
            "active_filters": {
                "action":
                    normalized_action,
                "entity_type":
                    normalized_entity_type,
            },
            "pagination": {
                "page": page,
                "total_pages":
                    total_pages,
                "total": total,
            },
            "filter_query_string":
                "&".join(
                    query_params
                ),
        },
    )


@router.get(
    "/cutoffs",
    response_class=HTMLResponse,
)
def cutoffs_page(
    request: Request,
    client_id: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    raw_client_id = str(
        client_id or ""
    ).strip()

    if raw_client_id:
        try:
            client_id = int(
                raw_client_id
            )
        except ValueError:
            client_id = None
    else:
        client_id = None

    page_size = 25
    page = max(page, 1)

    clients = list(
        db.scalars(
            select(Client)
            .order_by(
                Client.active.desc(),
                Client.name.asc(),
            )
        )
    )

    client_names = {
        client.id: client.name
        for client in clients
    }

    normalized_status = str(
        status or ""
    ).strip().upper()

    normalized_date_from = str(
        date_from or ""
    ).strip()

    normalized_date_to = str(
        date_to or ""
    ).strip()

    cutoff_statement = select(
        DailyCutoff
    )

    if client_id is not None:
        cutoff_statement = (
            cutoff_statement.where(
                DailyCutoff.client_id
                == client_id
            )
        )

    if normalized_status:
        cutoff_statement = (
            cutoff_statement.where(
                DailyCutoff.status
                == normalized_status
            )
        )

    filter_timezone = ZoneInfo(
        "America/Mexico_City"
    )

    try:
        if normalized_date_from:
            start_local = datetime.strptime(
                normalized_date_from,
                "%Y-%m-%d",
            ).replace(
                tzinfo=filter_timezone
            )

            cutoff_statement = (
                cutoff_statement.where(
                    DailyCutoff.period_end
                    >= start_local.astimezone(
                        UTC
                    )
                )
            )

        if normalized_date_to:
            end_local = (
                datetime.strptime(
                    normalized_date_to,
                    "%Y-%m-%d",
                ).replace(
                    tzinfo=filter_timezone
                )
                + timedelta(days=1)
            )

            cutoff_statement = (
                cutoff_statement.where(
                    DailyCutoff.period_end
                    < end_local.astimezone(
                        UTC
                    )
                )
            )

    except ValueError:
        normalized_date_from = ""
        normalized_date_to = ""

    filtered_cutoffs_subquery = (
        cutoff_statement
        .order_by(None)
        .subquery()
    )

    summary_row = db.execute(
        select(
            func.count(),
            func.coalesce(
                func.sum(
                    filtered_cutoffs_subquery
                    .c.idcif_count
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    filtered_cutoffs_subquery
                    .c.total_requests
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    filtered_cutoffs_subquery
                    .c.delivered_count
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    filtered_cutoffs_subquery
                    .c.pending_count
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    filtered_cutoffs_subquery
                    .c.failed_count
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    filtered_cutoffs_subquery
                    .c.total_amount
                ),
                Decimal("0.00"),
            ),
        )
    ).one()

    total_cutoffs = int(
        summary_row[0] or 0
    )

    cutoff_summary = {
        "cutoffs": total_cutoffs,
        "idcif": int(
            summary_row[1] or 0
        ),
        "requests": int(
            summary_row[2] or 0
        ),
        "delivered": int(
            summary_row[3] or 0
        ),
        "pending": int(
            summary_row[4] or 0
        ),
        "failed": int(
            summary_row[5] or 0
        ),
        "amount": (
            summary_row[6]
            or Decimal("0.00")
        ),
    }

    total_pages = max(
        1,
        (
            total_cutoffs
            + page_size
            - 1
        )
        // page_size,
    )

    if page > total_pages:
        page = total_pages

    offset = (
        page - 1
    ) * page_size

    cutoffs = list(
        db.scalars(
            cutoff_statement
            .order_by(
                DailyCutoff.period_end.desc(),
                DailyCutoff.id.desc(),
            )
            .offset(offset)
            .limit(page_size)
        )
    )

    status_options = list(
        db.scalars(
            select(DailyCutoff.status)
            .distinct()
            .order_by(
                DailyCutoff.status
            )
        )
    )

    active_filters = {
        "client_id": client_id,
        "status": normalized_status,
        "date_from":
            normalized_date_from,
        "date_to":
            normalized_date_to,
    }

    query_params = []

    if client_id is not None:
        query_params.append(
            f"client_id={client_id}"
        )

    if normalized_status:
        query_params.append(
            "status="
            + quote(normalized_status)
        )

    if normalized_date_from:
        query_params.append(
            "date_from="
            + quote(
                normalized_date_from
            )
        )

    if normalized_date_to:
        query_params.append(
            "date_to="
            + quote(
                normalized_date_to
            )
        )

    filter_query_string = "&".join(
        query_params
    )

    return templates.TemplateResponse(
        request=request,
        name="panel/cutoffs.html",
        context={
            "request": request,
            "title": "Cortes",
            "active_page": "cutoffs",
            "cutoffs": cutoffs,
            "clients": clients,
            "client_names":
                client_names,
            "status_options":
                status_options,
            "active_filters":
                active_filters,
            "cutoff_summary":
                cutoff_summary,
            "pagination": {
                "page": page,
                "total_pages":
                    total_pages,
                "total": total_cutoffs,
                "page_size": page_size,
            },
            "filter_query_string":
                filter_query_string,
        },
    )


@router.get(
    "/system",
    response_class=HTMLResponse,
)
def system_status_page(
    request: Request,
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    web_status = _service_health(
        "whatsapp-lotes-web.service"
    )

    automations = [
        {
            "name": "Lotes",
            "description":
                "Agrupa y envía solicitudes al proveedor.",
            **_timer_health(
                "whatsapp-lotes-batches.timer",
                "whatsapp-lotes-batches.service",
            ),
        },
        {
            "name": "Reintentos",
            "description":
                "Procesa reintentos pendientes.",
            **_timer_health(
                "whatsapp-lotes-retries.timer",
                "whatsapp-lotes-retries.service",
            ),
        },
        {
            "name": "CURP",
            "description":
                "Consulta CURP y obtiene RFC automáticamente.",
            **_timer_health(
                "whatsapp-lotes-curp.timer",
                "whatsapp-lotes-curp.service",
            ),
        },
        {
            "name": "Cortes",
            "description":
                "Genera y envía los cortes diarios.",
            **_timer_health(
                "whatsapp-lotes-cutoffs.timer",
                "whatsapp-lotes-cutoffs.service",
            ),
        },
    ]

    system_healthy = (
        bool(web_status["healthy"])
        and all(
            bool(item["healthy"])
            for item in automations
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="panel/system.html",
        context={
            "request": request,
            "title": "Estado del sistema",
            "active_page": "system",
            "web_status": web_status,
            "automations": automations,
            "system_healthy":
                system_healthy,
        },
    )


@router.get("")
@router.get("/")
def panel_root() -> RedirectResponse:
    return RedirectResponse(
        url="/panel/dashboard",
        status_code=303,
    )


@router.get(
    "/dashboard",
    response_class=HTMLResponse,
)
def dashboard_page(
    request: Request,
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    dashboard_timezone = ZoneInfo(
        "America/Mexico_City"
    )

    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(
        dashboard_timezone
    )

    selected_range = str(
        request.query_params.get(
            "range",
            "today",
        )
    ).strip().lower()

    allowed_ranges = {
        "today",
        "yesterday",
        "7d",
        "month",
    }

    if selected_range not in allowed_ranges:
        selected_range = "today"

    today_start_local = datetime.combine(
        now_local.date(),
        datetime.min.time(),
        tzinfo=dashboard_timezone,
    )

    if selected_range == "yesterday":
        period_start_local = (
            today_start_local
            - timedelta(days=1)
        )
        period_end_local = today_start_local
        range_label = "Ayer"

    elif selected_range == "7d":
        period_start_local = (
            today_start_local
            - timedelta(days=6)
        )
        period_end_local = (
            today_start_local
            + timedelta(days=1)
        )
        range_label = "Últimos 7 días"

    elif selected_range == "month":
        period_start_local = (
            today_start_local.replace(
                day=1
            )
        )
        period_end_local = (
            today_start_local
            + timedelta(days=1)
        )
        range_label = "Mes actual"

    else:
        period_start_local = today_start_local
        period_end_local = (
            today_start_local
            + timedelta(days=1)
        )
        range_label = "Hoy"

    period_start_utc = (
        period_start_local.astimezone(UTC)
    )

    period_end_utc = (
        period_end_local.astimezone(UTC)
    )

    range_requests = list(
        db.scalars(
            select(RequestModel)
            .where(
                RequestModel.received_at
                >= period_start_utc,
                RequestModel.received_at
                < period_end_utc,
            )
            .order_by(
                RequestModel.received_at.desc(),
                RequestModel.id.desc(),
            )
        )
    )

    range_batches = list(
        db.scalars(
            select(Batch)
            .where(
                Batch.created_at
                >= period_start_utc,
                Batch.created_at
                < period_end_utc,
            )
            .order_by(
                Batch.created_at.desc(),
                Batch.id.desc(),
            )
        )
    )

    clients = list(
        db.scalars(
            select(Client)
        )
    )

    providers = list(
        db.scalars(
            select(Provider)
        )
    )

    client_names = {
        client.id: client.name
        for client in clients
    }

    provider_names = {
        provider.id: provider.name
        for provider in providers
    }

    delivered_requests = [
        item
        for item in range_requests
        if item.status == "DELIVERED"
    ]

    failed_statuses = {
        "CURP_LOOKUP_FAILED",
    }

    failed_requests = [
        item
        for item in range_requests
        if item.status in failed_statuses
    ]

    pending_requests = [
        item
        for item in range_requests
        if item.status not in (
            {"DELIVERED"} | failed_statuses
        )
    ]

    idcif_requests = [
        item
        for item in delivered_requests
        if (
            str(
                item.result_code or ""
            ).strip().upper()
            == "OK"
            and str(
                item.idcif or ""
            ).strip()
        )
    ]

    chart_days = []

    current_day = (
        period_start_local.date()
    )

    last_day = (
        period_end_local
        - timedelta(microseconds=1)
    ).date()

    while current_day <= last_day:
        chart_days.append(
            current_day
        )
        current_day += timedelta(days=1)

    daily_requests = {
        day: 0
        for day in chart_days
    }

    daily_idcif = {
        day: 0
        for day in chart_days
    }

    for item in range_requests:
        received_at = item.received_at

        if received_at.tzinfo is None:
            received_at = received_at.replace(
                tzinfo=UTC
            )

        local_day = (
            received_at
            .astimezone(
                dashboard_timezone
            )
            .date()
        )

        if local_day in daily_requests:
            daily_requests[local_day] += 1

    for item in idcif_requests:
        reference_time = (
            item.delivered_at
            or item.provider_replied_at
            or item.received_at
        )

        if reference_time is None:
            continue

        if reference_time.tzinfo is None:
            reference_time = (
                reference_time.replace(
                    tzinfo=UTC
                )
            )

        local_day = (
            reference_time
            .astimezone(
                dashboard_timezone
            )
            .date()
        )

        if local_day in daily_idcif:
            daily_idcif[local_day] += 1

    max_daily_requests = max(
        daily_requests.values(),
        default=0,
    )

    max_daily_idcif = max(
        daily_idcif.values(),
        default=0,
    )

    request_chart = []

    idcif_chart = []

    for day in chart_days:
        request_value = (
            daily_requests[day]
        )

        idcif_value = (
            daily_idcif[day]
        )

        request_chart.append(
            {
                "label":
                    day.strftime("%d/%m"),
                "value":
                    request_value,
                "height":
                    (
                        round(
                            (
                                request_value
                                / max_daily_requests
                            )
                            * 100
                        )
                        if max_daily_requests
                        else 0
                    ),
            }
        )

        idcif_chart.append(
            {
                "label":
                    day.strftime("%d/%m"),
                "value":
                    idcif_value,
                "height":
                    (
                        round(
                            (
                                idcif_value
                                / max_daily_idcif
                            )
                            * 100
                        )
                        if max_daily_idcif
                        else 0
                    ),
            }
        )

    delivered_amount = sum(
        (
            item.sale_price
            for item in delivered_requests
        ),
        Decimal("0.00"),
    )

    delivery_seconds = []

    for item in delivered_requests:
        if (
            item.received_at is None
            or item.delivered_at is None
        ):
            continue

        received_at = item.received_at
        delivered_at = item.delivered_at

        if received_at.tzinfo is None:
            received_at = (
                received_at.replace(
                    tzinfo=UTC
                )
            )

        if delivered_at.tzinfo is None:
            delivered_at = (
                delivered_at.replace(
                    tzinfo=UTC
                )
            )

        seconds = (
            delivered_at.astimezone(UTC)
            - received_at.astimezone(UTC)
        ).total_seconds()

        if seconds >= 0:
            delivery_seconds.append(
                seconds
            )

    if delivery_seconds:
        average_delivery_seconds = (
            sum(delivery_seconds)
            / len(delivery_seconds)
        )
    else:
        average_delivery_seconds = None

    if average_delivery_seconds is None:
        average_delivery_text = "—"
    elif average_delivery_seconds < 60:
        average_delivery_text = (
            f"{average_delivery_seconds:.0f} s"
        )
    elif average_delivery_seconds < 3600:
        average_delivery_text = (
            f"{average_delivery_seconds / 60:.1f} min"
        )
    else:
        average_delivery_text = (
            f"{average_delivery_seconds / 3600:.1f} h"
        )

    sent_batches = [
        batch
        for batch in range_batches
        if batch.status == "SENT"
    ]

    recent_requests = range_requests[:10]

    recent_cutoffs = list(
        db.scalars(
            select(DailyCutoff)
            .where(
                DailyCutoff.period_end
                >= period_start_utc,
                DailyCutoff.period_end
                < period_end_utc,
            )
            .order_by(
                DailyCutoff.period_end.desc(),
                DailyCutoff.id.desc(),
            )
            .limit(5)
        )
    )

    dashboard_stats = {
        "requests": len(range_requests),
        "idcif": len(idcif_requests),
        "delivered": len(
            delivered_requests
        ),
        "pending": len(
            pending_requests
        ),
        "failed": len(
            failed_requests
        ),
        "amount": delivered_amount,
        "batches": len(sent_batches),
        "average_delivery":
            average_delivery_text,
    }

    return templates.TemplateResponse(
        request=request,
        name="panel/dashboard.html",
        context={
            "request": request,
            "title": "Dashboard",
            "active_page": "dashboard",
            "dashboard_stats":
                dashboard_stats,
            "recent_requests":
                recent_requests,
            "recent_cutoffs":
                recent_cutoffs,
            "client_names":
                client_names,
            "provider_names":
                provider_names,
            "dashboard_date":
                now_local.strftime(
                    "%d/%m/%Y"
                ),
            "dashboard_timezone":
                "Ciudad de México",
            "selected_range":
                selected_range,
            "range_label":
                range_label,
            "request_chart":
                request_chart,
            "idcif_chart":
                idcif_chart,
        },
    )





@router.post(
    "/batches/{batch_id}/retry"
)
async def batch_retry_manual(
    request: Request,
    batch_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return RedirectResponse(
            url=(
                f"/panel/batches/{batch_id}"
                "?action_status=error"
                "&action_message=CSRF inválido"
            ),
            status_code=303,
        )

    batch = db.get(
        Batch,
        batch_id,
    )

    if batch is None:
        return RedirectResponse(
            url="/panel/operations",
            status_code=303,
        )

    if batch.status != "SEND_FAILED":
        return RedirectResponse(
            url=(
                f"/panel/batches/{batch_id}"
                "?action_status=error"
                "&action_message="
                "El lote no está en SEND_FAILED"
            ),
            status_code=303,
        )

    try:
        await send_existing_batch(
            db,
            batch_id=batch.id,
        )

    except (
        EvolutionAPIError,
        BatchServiceError,
        ValueError,
    ):
        return RedirectResponse(
            url=(
                f"/panel/batches/{batch_id}"
                "?action_status=error"
                "&action_message="
                "No fue posible reenviar el lote"
            ),
            status_code=303,
        )

    clear_retry_state(
        "batch",
        batch.id,
    )

    register_admin_audit(
        db,
        request,
        action="BATCH_RETRIED",
        entity_type="BATCH",
        entity_id=batch.id,
        summary=(
            f"Lote #{batch.id} reenviado "
            f"manualmente"
        ),
        details=(
            f"provider_id={batch.provider_id}\n"
            f"client_id={batch.client_id}\n"
            f"request_count={batch.request_count}\n"
            f"status={batch.status}"
        ),
    )

    db.commit()

    return RedirectResponse(
        url=(
            f"/panel/batches/{batch_id}"
            "?action_status=success"
            "&action_message="
            "Lote reenviado correctamente"
        ),
        status_code=303,
    )


@router.get(
    "/batches/{batch_id}",
    response_class=HTMLResponse,
)
def batch_detail_page(
    request: Request,
    batch_id: int,
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    csrf_token = ensure_csrf_token(
        request
    )

    action_status = str(
        request.query_params.get(
            "action_status",
            "",
        )
    ).strip()

    action_message = str(
        request.query_params.get(
            "action_message",
            "",
        )
    ).strip()

    batch = db.get(
        Batch,
        batch_id,
    )

    if batch is None:
        return RedirectResponse(
            url="/panel/operations",
            status_code=303,
        )

    client = None

    if batch.client_id is not None:
        client = db.get(
            Client,
            batch.client_id,
        )

    provider = db.get(
        Provider,
        batch.provider_id,
    )

    batch_items = list(
        db.scalars(
            select(BatchItem)
            .where(
                BatchItem.batch_id
                == batch.id
            )
            .order_by(
                BatchItem.position.asc(),
                BatchItem.id.asc(),
            )
        )
    )

    request_ids = [
        item.request_id
        for item in batch_items
    ]

    requests_by_id = {}

    if request_ids:
        request_rows = list(
            db.scalars(
                select(RequestModel)
                .where(
                    RequestModel.id.in_(
                        request_ids
                    )
                )
            )
        )

        requests_by_id = {
            item.id: item
            for item in request_rows
        }

    batch_requests = []

    for batch_item in batch_items:
        request_item = requests_by_id.get(
            batch_item.request_id
        )

        if request_item is None:
            continue

        batch_requests.append(
            {
                "position":
                    batch_item.position,
                "request":
                    request_item,
            }
        )

    delivered_count = sum(
        1
        for item in batch_requests
        if item["request"].status
        == "DELIVERED"
    )

    pending_count = sum(
        1
        for item in batch_requests
        if item["request"].status
        not in {
            "DELIVERED",
            "CURP_LOOKUP_FAILED",
        }
    )

    failed_count = sum(
        1
        for item in batch_requests
        if item["request"].status
        == "CURP_LOOKUP_FAILED"
    )

    batch_status_label = panel_status(
        batch.status
    )
    return templates.TemplateResponse(
        request=request,
        name="panel/batch_detail.html",
        context={
            "request": request,
            "title":
                f"Lote #{batch.id}",
            "active_page": "operations",
            "batch": batch,
            "client": client,
            "provider": provider,
            "batch_requests":
                batch_requests,
            "batch_status_label":
                batch_status_label,
            "delivered_count":
                delivered_count,
            "pending_count":
                pending_count,
            "failed_count":
                failed_count,
            "csrf_token":
                csrf_token,
            "action_status":
                action_status,
            "action_message":
                action_message,
        },
    )





@router.post(
    "/requests/{request_id}/retry-curp"
)
def request_retry_curp(
    request: Request,
    request_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message=CSRF inválido"
            ),
            status_code=303,
        )

    request_item = db.get(
        RequestModel,
        request_id,
    )

    if request_item is None:
        return RedirectResponse(
            url="/panel/operations",
            status_code=303,
        )

    if (
        request_item.status
        != "CURP_LOOKUP_FAILED"
    ):
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message="
                "La CURP no está en estado fallido"
            ),
            status_code=303,
        )

    if not request_item.original_curp:
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message="
                "La solicitud no tiene CURP original"
            ),
            status_code=303,
        )

    clear_curp_retry_state(
        request_item.id
    )

    request_item.status = (
        "PENDING_CURP_LOOKUP"
    )

    register_admin_audit(
        db,
        request,
        action="REQUEST_CURP_REQUEUED",
        entity_type="REQUEST",
        entity_id=request_item.id,
        summary=(
            f"CURP programada nuevamente "
            f"para solicitud #{request_item.id}"
        ),
        details=(
            "CURP_LOOKUP_FAILED -> "
            "PENDING_CURP_LOOKUP\n"
            f"curp={request_item.original_curp or ''}"
        ),
    )

    db.commit()

    return RedirectResponse(
        url=(
            f"/panel/requests/{request_id}"
            "?action_status=success"
            "&action_message="
            "CURP programada para reprocesamiento"
        ),
        status_code=303,
    )


@router.post(
    "/requests/{request_id}/retry-delivery"
)
async def request_retry_delivery(
    request: Request,
    request_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message=CSRF inválido"
            ),
            status_code=303,
        )

    request_item = db.get(
        RequestModel,
        request_id,
    )

    if request_item is None:
        return RedirectResponse(
            url="/panel/operations",
            status_code=303,
        )

    if request_item.status != "DELIVERY_FAILED":
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message="
                "La solicitud no está en DELIVERY_FAILED"
            ),
            status_code=303,
        )

    client = db.get(
        Client,
        request_item.client_id,
    )

    provider = None

    if request_item.provider_id:
        provider = db.get(
            Provider,
            request_item.provider_id,
        )

    if client is None or provider is None:
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message="
                "Cliente o proveedor no disponible"
            ),
            status_code=303,
        )

    try:
        text = build_delivery_retry_text(
            [request_item]
        )

        await send_text_message(
            destination_jid=
                client.whatsapp_jid,
            text=text,
            instance=
                provider.evolution_instance,
        )

    except (
        EvolutionAPIError,
        ValueError,
    ):
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message="
                "El reintento de entrega falló"
            ),
            status_code=303,
        )

    request_item.status = "DELIVERED"
    request_item.delivered_at = (
        datetime.now(UTC)
    )

    clear_retry_state(
        "delivery",
        request_item.id,
    )

    register_admin_audit(
        db,
        request,
        action="REQUEST_DELIVERY_RETRIED",
        entity_type="REQUEST",
        entity_id=request_item.id,
        summary=(
            f"Entrega recuperada manualmente "
            f"para solicitud #{request_item.id}"
        ),
        details=(
            "DELIVERY_FAILED -> DELIVERED\n"
            f"client_id={request_item.client_id}\n"
            f"provider_id={request_item.provider_id}\n"
            f"rfc={request_item.rfc or ''}"
        ),
    )

    db.commit()

    return RedirectResponse(
        url=(
            f"/panel/requests/{request_id}"
            "?action_status=success"
            "&action_message="
            "Entrega recuperada correctamente"
        ),
        status_code=303,
    )


@router.post(
    "/requests/{request_id}/resend"
)
async def request_resend_result(
    request: Request,
    request_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message=CSRF inválido"
            ),
            status_code=303,
        )

    request_item = db.get(
        RequestModel,
        request_id,
    )

    if request_item is None:
        return RedirectResponse(
            url="/panel/operations",
            status_code=303,
        )

    if (
        not request_item.provider_result
        and not request_item.result_code
    ):
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message="
                "La solicitud todavía no tiene resultado"
            ),
            status_code=303,
        )

    client = db.get(
        Client,
        request_item.client_id,
    )

    provider = None

    if request_item.provider_id:
        provider = db.get(
            Provider,
            request_item.provider_id,
        )

    if client is None or provider is None:
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message="
                "Cliente o proveedor no disponible"
            ),
            status_code=303,
        )

    try:
        text = build_delivery_retry_text(
            [request_item]
        )

        await send_text_message(
            destination_jid=
                client.whatsapp_jid,
            text=text,
            instance=
                provider.evolution_instance,
        )

    except (
        EvolutionAPIError,
        ValueError,
    ):
        return RedirectResponse(
            url=(
                f"/panel/requests/{request_id}"
                "?action_status=error"
                "&action_message="
                "No fue posible reenviar el resultado"
            ),
            status_code=303,
        )

    # Reenvío manual de una solicitud ya entregada:
    # NO modifica precio, corte, status ni delivered_at.

    register_admin_audit(
        db,
        request,
        action="REQUEST_RESULT_RESENT",
        entity_type="REQUEST",
        entity_id=request_item.id,
        summary=(
            f"Resultado reenviado para "
            f"solicitud #{request_item.id}"
        ),
        details=(
            f"status={request_item.status}\n"
            f"client_id={request_item.client_id}\n"
            f"provider_id={request_item.provider_id}\n"
            f"rfc={request_item.rfc or ''}\n"
            f"result_code={request_item.result_code or ''}"
        ),
    )

    db.commit()

    return RedirectResponse(
        url=(
            f"/panel/requests/{request_id}"
            "?action_status=success"
            "&action_message="
            "Resultado reenviado correctamente"
        ),
        status_code=303,
    )


@router.get(
    "/requests/{request_id}",
    response_class=HTMLResponse,
)
def request_detail_page(
    request: Request,
    request_id: int,
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    csrf_token = ensure_csrf_token(
        request
    )

    action_status = str(
        request.query_params.get(
            "action_status",
            "",
        )
    ).strip()

    action_message = str(
        request.query_params.get(
            "action_message",
            "",
        )
    ).strip()

    request_item = db.get(
        RequestModel,
        request_id,
    )

    if request_item is None:
        return RedirectResponse(
            url="/panel/operations",
            status_code=303,
        )

    client = db.get(
        Client,
        request_item.client_id,
    )

    provider = None

    if request_item.provider_id:
        provider = db.get(
            Provider,
            request_item.provider_id,
        )

    batch_item = db.scalar(
        select(BatchItem)
        .where(
            BatchItem.request_id
            == request_item.id
        )
        .limit(1)
    )

    batch = None

    if batch_item is not None:
        batch = db.get(
            Batch,
            batch_item.batch_id,
        )

    status_label = panel_status(
        request_item.status
    )
    timeline = [
        {
            "label": "Solicitud recibida",
            "done": (
                request_item.received_at
                is not None
            ),
            "time": request_item.received_at,
        },
        {
            "label": (
                "RFC identificado"
                if request_item.input_type == "RFC"
                else "CURP procesada / RFC obtenido"
            ),
            "done": bool(
                request_item.rfc
            ),
            "time": None,
        },
        {
            "label": "Agregada a lote",
            "done": batch_item is not None,
            "time": (
                batch.created_at
                if batch is not None
                else None
            ),
        },
        {
            "label": "Enviada al proveedor",
            "done": (
                request_item.sent_to_provider_at
                is not None
            ),
            "time":
                request_item.sent_to_provider_at,
        },
        {
            "label": "Respuesta del proveedor",
            "done": (
                request_item.provider_replied_at
                is not None
            ),
            "time":
                request_item.provider_replied_at,
        },
        {
            "label": "Entregada al cliente",
            "done": (
                request_item.delivered_at
                is not None
            ),
            "time": request_item.delivered_at,
        },
    ]

    return templates.TemplateResponse(
        request=request,
        name="panel/request_detail.html",
        context={
            "request": request,
            "title":
                f"Solicitud #{request_item.id}",
            "active_page": "operations",
            "request_item": request_item,
            "client": client,
            "provider": provider,
            "batch": batch,
            "batch_item": batch_item,
            "status_label": status_label,
            "timeline": timeline,
            "csrf_token": csrf_token,
            "action_status": action_status,
            "action_message": action_message,
        },
    )


@router.get(
    "/operations",
    response_class=HTMLResponse,
)
def operations_page(
    request: Request,
    client_id: str | None = None,
    status: str | None = None,
    input_type: str | None = None,
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    batch_page: int = 1,
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    raw_client_id = str(
        client_id or ""
    ).strip()

    if raw_client_id:
        try:
            client_id = int(
                raw_client_id
            )
        except ValueError:
            client_id = None
    else:
        client_id = None

    request_page_size = 25
    batch_page_size = 20

    page = max(page, 1)
    batch_page = max(batch_page, 1)

    clients = list(
        db.scalars(
            select(Client).order_by(
                Client.active.desc(),
                Client.name.asc(),
            )
        )
    )

    providers = list(
        db.scalars(
            select(Provider).order_by(
                Provider.active.desc(),
                Provider.name.asc(),
            )
        )
    )

    client_names = {
        client.id: client.name
        for client in clients
    }

    provider_names = {
        provider.id: provider.name
        for provider in providers
    }

    request_statement = select(
        RequestModel
    )

    normalized_status = str(
        status or ""
    ).strip().upper()

    normalized_input_type = str(
        input_type or ""
    ).strip().upper()

    normalized_query = str(
        query or ""
    ).strip().upper()

    if client_id is not None:
        request_statement = (
            request_statement.where(
                RequestModel.client_id
                == client_id
            )
        )

    if normalized_status:
        request_statement = (
            request_statement.where(
                RequestModel.status
                == normalized_status
            )
        )

    if normalized_input_type in {
        "RFC",
        "CURP",
    }:
        request_statement = (
            request_statement.where(
                RequestModel.input_type
                == normalized_input_type
            )
        )

    if normalized_query:
        search_pattern = (
            f"%{normalized_query}%"
        )

        request_statement = (
            request_statement.where(
                or_(
                    RequestModel.rfc.ilike(
                        search_pattern
                    ),
                    RequestModel.original_curp.ilike(
                        search_pattern
                    ),
                    RequestModel.identifier_key.ilike(
                        search_pattern
                    ),
                    RequestModel.idcif.ilike(
                        search_pattern
                    ),
                    RequestModel.detected_name.ilike(
                        search_pattern
                    ),
                )
            )
        )

    filter_timezone = ZoneInfo(
        "America/Mexico_City"
    )

    normalized_date_from = str(
        date_from or ""
    ).strip()

    normalized_date_to = str(
        date_to or ""
    ).strip()

    try:
        if normalized_date_from:
            start_local = datetime.strptime(
                normalized_date_from,
                "%Y-%m-%d",
            ).replace(
                tzinfo=filter_timezone
            )

            request_statement = (
                request_statement.where(
                    RequestModel.received_at
                    >= start_local.astimezone(
                        UTC
                    )
                )
            )

        if normalized_date_to:
            end_local = (
                datetime.strptime(
                    normalized_date_to,
                    "%Y-%m-%d",
                ).replace(
                    tzinfo=filter_timezone
                )
                + timedelta(days=1)
            )

            request_statement = (
                request_statement.where(
                    RequestModel.received_at
                    < end_local.astimezone(
                        UTC
                    )
                )
            )

    except ValueError:
        normalized_date_from = ""
        normalized_date_to = ""

    request_count_statement = (
        select(func.count())
        .select_from(
            request_statement
            .order_by(None)
            .subquery()
        )
    )

    total_requests = int(
        db.scalar(
            request_count_statement
        )
        or 0
    )

    request_total_pages = max(
        1,
        (
            total_requests
            + request_page_size
            - 1
        )
        // request_page_size,
    )

    if page > request_total_pages:
        page = request_total_pages

    request_offset = (
        page - 1
    ) * request_page_size

    recent_requests = list(
        db.scalars(
            request_statement
            .order_by(
                RequestModel.received_at.desc(),
                RequestModel.id.desc(),
            )
            .offset(request_offset)
            .limit(request_page_size)
        )
    )

    batch_statement = select(Batch)

    if client_id is not None:
        batch_statement = (
            batch_statement.where(
                Batch.client_id == client_id
            )
        )

    batch_count_statement = (
        select(func.count())
        .select_from(
            batch_statement
            .order_by(None)
            .subquery()
        )
    )

    total_batches = int(
        db.scalar(
            batch_count_statement
        )
        or 0
    )

    batch_total_pages = max(
        1,
        (
            total_batches
            + batch_page_size
            - 1
        )
        // batch_page_size,
    )

    if batch_page > batch_total_pages:
        batch_page = batch_total_pages

    batch_offset = (
        batch_page - 1
    ) * batch_page_size

    recent_batches = list(
        db.scalars(
            batch_statement
            .order_by(
                Batch.created_at.desc(),
                Batch.id.desc(),
            )
            .offset(batch_offset)
            .limit(batch_page_size)
        )
    )

    status_options = list(
        db.scalars(
            select(RequestModel.status)
            .distinct()
            .order_by(RequestModel.status)
        )
    )

    request_counts = {
        "total": total_requests,
        "delivered": sum(
            1
            for item in recent_requests
            if item.status == "DELIVERED"
        ),
        "pending": sum(
            1
            for item in recent_requests
            if item.status not in {
                "DELIVERED",
                "CURP_LOOKUP_FAILED",
            }
        ),
        "failed": sum(
            1
            for item in recent_requests
            if item.status
            == "CURP_LOOKUP_FAILED"
        ),
    }

    active_filters = {
        "client_id": client_id,
        "status": normalized_status,
        "input_type": normalized_input_type,
        "query": normalized_query,
        "date_from": normalized_date_from,
        "date_to": normalized_date_to,
    }

    query_params = []

    if client_id is not None:
        query_params.append(
            f"client_id={client_id}"
        )

    if normalized_status:
        query_params.append(
            "status="
            + quote(normalized_status)
        )

    if normalized_input_type:
        query_params.append(
            "input_type="
            + quote(normalized_input_type)
        )

    if normalized_query:
        query_params.append(
            "query="
            + quote(normalized_query)
        )

    if normalized_date_from:
        query_params.append(
            "date_from="
            + quote(normalized_date_from)
        )

    if normalized_date_to:
        query_params.append(
            "date_to="
            + quote(normalized_date_to)
        )

    filter_query_string = "&".join(
        query_params
    )

    recent_batch_ids = [
        batch.id
        for batch in recent_batches
    ]

    batch_client_counts: dict[int, int] = {}

    if recent_batch_ids:
        batch_client_counts = {
            int(batch_id): int(total)
            for batch_id, total in db.execute(
                select(
                    BatchItem.batch_id,
                    func.count(
                        func.distinct(
                            RequestModel.client_id
                        )
                    ),
                )
                .join(
                    RequestModel,
                    RequestModel.id
                    == BatchItem.request_id,
                )
                .where(
                    BatchItem.batch_id.in_(
                        recent_batch_ids
                    )
                )
                .group_by(
                    BatchItem.batch_id
                )
            ).all()
        }

    return templates.TemplateResponse(
        request=request,
        name="panel/operations.html",
        context={
            "request": request,
            "title": "Operación",
            "active_page": "operations",
            "recent_requests": recent_requests,
            "recent_batches": recent_batches,
            "batch_client_counts":
                batch_client_counts,
            "clients": clients,
            "status_options": status_options,
            "client_names": client_names,
            "provider_names": provider_names,
            "request_counts": request_counts,
            "active_filters": active_filters,
            "request_pagination": {
                "page": page,
                "total_pages": request_total_pages,
                "total": total_requests,
                "page_size": request_page_size,
            },
            "batch_pagination": {
                "page": batch_page,
                "total_pages": batch_total_pages,
                "total": total_batches,
                "page_size": batch_page_size,
            },
            "filter_query_string":
                filter_query_string,
        },
    )




@router.get(
    "/curp-rfc",
    response_class=HTMLResponse,
)
def curp_rfc_page(
    request: Request,
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    return templates.TemplateResponse(
        request=request,
        name="panel/curp_rfc.html",
        context={
            "request": request,
            "title": "Convertidor CURP → RFC",
            "active_page": "curp_rfc",
            "csrf_token":
                ensure_csrf_token(request),
            "curps_value": "",
            "results": [],
            "success_count": 0,
            "failed_count": 0,
            "error": None,
        },
    )


@router.post(
    "/curp-rfc",
    response_class=HTMLResponse,
)
def curp_rfc_convert(
    request: Request,
    curps: str = Form(...),
    csrf_token: str = Form(...),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    raw_value = str(
        curps or ""
    ).strip()

    context = {
        "request": request,
        "title": "Convertidor CURP → RFC",
        "active_page": "curp_rfc",
        "csrf_token":
            ensure_csrf_token(request),
        "curps_value": raw_value,
        "results": [],
        "success_count": 0,
        "failed_count": 0,
        "error": None,
    }

    if not validate_csrf(
        request,
        csrf_token,
    ):
        context["error"] = (
            "Token de seguridad inválido. "
            "Actualiza la página e intenta nuevamente."
        )

        return templates.TemplateResponse(
            request=request,
            name="panel/curp_rfc.html",
            context=context,
            status_code=400,
        )

    # Acepta una CURP por línea.
    #
    # También tolera comas y punto y coma
    # como separadores.
    normalized_input = (
        raw_value
        .replace(",", "\n")
        .replace(";", "\n")
    )

    entries: list[str] = []

    for raw_line in (
        normalized_input.splitlines()
    ):
        value = normalize_text(
            raw_line
        )

        value = (
            value
            .replace(" ", "")
            .strip()
        )

        if value:
            entries.append(value)

    if not entries:
        context["error"] = (
            "Ingresa al menos una CURP."
        )

        return templates.TemplateResponse(
            request=request,
            name="panel/curp_rfc.html",
            context=context,
            status_code=400,
        )

    # Quitar repetidas conservando orden.
    unique_entries: list[str] = []
    seen: set[str] = set()

    for value in entries:
        if value in seen:
            continue

        seen.add(value)
        unique_entries.append(value)

    MAX_CURPS_PER_QUERY = 25

    if (
        len(unique_entries)
        > MAX_CURPS_PER_QUERY
    ):
        context["error"] = (
            "Puedes procesar máximo "
            f"{MAX_CURPS_PER_QUERY} CURP "
            "por consulta."
        )

        return templates.TemplateResponse(
            request=request,
            name="panel/curp_rfc.html",
            context=context,
            status_code=400,
        )

    results: list[dict[str, str]] = []

    for curp_value in unique_entries:
        detected_curps = extract_curps(
            curp_value
        )

        if (
            len(detected_curps) != 1
            or detected_curps[0]
            != curp_value
        ):
            results.append(
                {
                    "curp": curp_value,
                    "rfc": "",
                    "status": "FORMATO_INVALIDO",
                    "message":
                        "Formato de CURP inválido.",
                }
            )

            continue

        try:
            rfc, _person_data = (
                convert_curp_to_rfc(
                    curp_value
                )
            )

            normalized_rfc = str(
                rfc or ""
            ).strip().upper()

            if normalized_rfc:
                results.append(
                    {
                        "curp": curp_value,
                        "rfc": normalized_rfc,
                        "status": "OK",
                        "message": "Convertida",
                    }
                )

            else:
                results.append(
                    {
                        "curp": curp_value,
                        "rfc": "",
                        "status": "ERROR",
                        "message":
                            "No fue posible obtener el RFC.",
                    }
                )

        except CurpRfcError as error:
            error_text = str(error)

            if (
                "La CURP no se encuentra "
                "en la base de datos"
                in error_text
            ):
                message = (
                    "CURP no encontrada "
                    "en la base de datos."
                )

            else:
                message = (
                    "No fue posible obtener "
                    "el RFC."
                )

            results.append(
                {
                    "curp": curp_value,
                    "rfc": "",
                    "status": "NO_ENCONTRADA",
                    "message": message,
                }
            )

        except Exception as error:
            logger.exception(
                "Error convertidor múltiple "
                "CURP->RFC curp=%s error=%s",
                curp_value,
                error,
            )

            results.append(
                {
                    "curp": curp_value,
                    "rfc": "",
                    "status": "ERROR",
                    "message":
                        "Error temporal del servicio.",
                }
            )

    success_count = sum(
        1
        for item in results
        if item["status"] == "OK"
    )

    failed_count = (
        len(results)
        - success_count
    )

    context["results"] = results
    context["success_count"] = (
        success_count
    )
    context["failed_count"] = (
        failed_count
    )

    return templates.TemplateResponse(
        request=request,
        name="panel/curp_rfc.html",
        context=context,
    )


@router.get(
    "/messages",
    response_class=HTMLResponse,
)
def messages_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    return templates.TemplateResponse(
        request=request,
        name="panel/messages.html",
        context={
            "request": request,
            "title": "Mensajes",
            "active_page": "messages",
            "csrf_token":
                ensure_csrf_token(request),
            "message": message,
            "error": error,
        },
    )


@router.post("/messages/send")
async def send_mass_message(
    request: Request,
    target_type: str = Form(...),
    broadcast_message: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return RedirectResponse(
            url=(
                "/panel/messages?"
                "error="
                + quote(
                    "Token de seguridad inválido."
                )
            ),
            status_code=303,
        )

    normalized_target = str(
        target_type or ""
    ).strip().lower()

    if normalized_target not in {
        "all",
        "group",
        "private",
    }:
        return RedirectResponse(
            url=(
                "/panel/messages?"
                "error="
                + quote(
                    "Tipo de destinatario inválido."
                )
            ),
            status_code=303,
        )

    text_message = str(
        broadcast_message or ""
    ).strip()

    if not text_message:
        return RedirectResponse(
            url=(
                "/panel/messages?"
                "error="
                + quote(
                    "El mensaje no puede estar vacío."
                )
            ),
            status_code=303,
        )

    if len(text_message) > 4096:
        return RedirectResponse(
            url=(
                "/panel/messages?"
                "error="
                + quote(
                    "El mensaje supera los 4096 caracteres."
                )
            ),
            status_code=303,
        )

    conditions = [
        Client.active.is_(True),
        Client.deleted_at.is_(None),
    ]

    if normalized_target == "group":
        conditions.append(
            Client.source_type == "group"
        )

    elif normalized_target == "private":
        conditions.append(
            Client.source_type == "private"
        )

    clients = list(
        db.scalars(
            select(Client)
            .where(*conditions)
            .order_by(Client.id.asc())
        )
    )

    # Evitamos enviar dos veces si por alguna
    # razón hubiera JIDs repetidos.
    recipients: list[Client] = []
    seen_jids: set[str] = set()

    for client in clients:
        jid = str(
            client.whatsapp_jid or ""
        ).strip()

        if not jid:
            continue

        if jid in seen_jids:
            continue

        seen_jids.add(jid)
        recipients.append(client)

    if not recipients:
        return RedirectResponse(
            url=(
                "/panel/messages?"
                "error="
                + quote(
                    "No hay destinatarios activos "
                    "para esa selección."
                )
            ),
            status_code=303,
        )

    sent_count = 0
    failed_count = 0
    failed_clients: list[str] = []

    for client in recipients:
        try:
            result = await send_text_message(
                destination_jid=
                    client.whatsapp_jid,
                text=text_message,
                instance=
                    settings.evolution_instance,
            )

            if result.ok:
                sent_count += 1
            else:
                failed_count += 1
                failed_clients.append(
                    client.name
                )

        except (
            EvolutionAPIError,
            ValueError,
        ):
            failed_count += 1
            failed_clients.append(
                client.name
            )

            logger.exception(
                "Falló mensaje masivo "
                "client_id=%s jid=%s",
                client.id,
                client.whatsapp_jid,
            )

    target_labels = {
        "all": "Todos",
        "group": "Grupos",
        "private": "Chats privados",
    }

    details = (
        f"Destino: "
        f"{target_labels[normalized_target]}\n"
        f"Destinatarios: {len(recipients)}\n"
        f"Enviados: {sent_count}\n"
        f"Fallidos: {failed_count}"
    )

    if failed_clients:
        details += (
            "\nFallidos: "
            + ", ".join(
                failed_clients[:50]
            )
        )

    register_admin_audit(
        db,
        request,
        action="BROADCAST_SENT",
        entity_type="BROADCAST",
        entity_id=None,
        summary=(
            "Mensaje masivo enviado: "
            f"{sent_count} correctos, "
            f"{failed_count} fallidos"
        ),
        details=details,
    )

    db.commit()

    result_message = (
        "Envío terminado. "
        f"Enviados: {sent_count}. "
        f"Fallidos: {failed_count}."
    )

    return RedirectResponse(
        url=(
            "/panel/messages?"
            "message="
            + quote(result_message)
        ),
        status_code=303,
    )


def redirect_providers(
    *,
    message: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    query_parts: list[str] = []

    if message:
        query_parts.append(
            f"message={quote(message)}"
        )

    if error:
        query_parts.append(
            f"error={quote(error)}"
        )

    url = "/panel/providers"

    if query_parts:
        url += "?" + "&".join(query_parts)

    return RedirectResponse(
        url=url,
        status_code=303,
    )


def validate_provider_jid(
    whatsapp_jid: str,
) -> str:
    jid = str(whatsapp_jid or "").strip()

    if not jid:
        raise ValueError(
            "El JID del proveedor es obligatorio."
        )

    if not jid.endswith("@g.us"):
        raise ValueError(
            "El JID del proveedor debe terminar en @g.us."
        )

    return jid


@router.get(
    "/providers",
    response_class=HTMLResponse,
)
def providers_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    providers = list(
        db.scalars(
            select(Provider)
            .where(
                Provider.deleted_at.is_(None)
            )
            .order_by(
                Provider.active.desc(),
                Provider.priority.asc(),
                Provider.name.asc(),
            )
        )
    )

    provider_request_counts = {
        int(provider_id): int(total)
        for provider_id, total in db.execute(
            select(
                RequestModel.provider_id,
                func.count(RequestModel.id),
            )
            .where(
                RequestModel.provider_id.is_not(None),
                RequestModel.status == "DELIVERED",
                RequestModel.result_code == "OK",
                RequestModel.idcif.is_not(None),
                RequestModel.idcif != "",
            )
            .group_by(
                RequestModel.provider_id
            )
        ).all()
    }

    return templates.TemplateResponse(
        request=request,
        name="panel/providers.html",
        context={
            "request": request,
            "title": "Proveedores",
            "active_page": "providers",
            "providers": providers,
            "provider_request_counts":
                provider_request_counts,
            "csrf_token":
                ensure_csrf_token(request),
            "message": message,
            "error": error,
        },
    )


@router.post("/providers/create")
def create_provider(
    request: Request,
    name: str = Form(...),
    whatsapp_jid: str = Form(...),
    evolution_instance: str = Form(...),
    response_header: str = Form(""),
    priority: int = Form(...),
    timeout_minutes: int = Form(...),
    notes: str = Form(""),
    csrf_token: str = Form(...),
    active: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return redirect_providers(
            error="Token de seguridad inválido."
        )

    if not name.strip():
        return redirect_providers(
            error="El nombre es obligatorio."
        )

    try:
        jid = validate_provider_jid(
            whatsapp_jid
        )
    except ValueError as validation_error:
        return redirect_providers(
            error=str(validation_error)
        )

    instance_name = evolution_instance.strip()

    if not instance_name:
        return redirect_providers(
            error="La instancia de Evolution es obligatoria."
        )

    if not 1 <= priority <= 999:
        return redirect_providers(
            error="La prioridad debe estar entre 1 y 999."
        )

    if not 1 <= timeout_minutes <= 1440:
        return redirect_providers(
            error=(
                "El tiempo de espera debe estar "
                "entre 1 y 1440 minutos."
            )
        )

    provider = Provider(
        name=name.strip(),
        whatsapp_jid=jid,
        evolution_instance=instance_name,
        response_header=(
            response_header.strip() or None
        ),
        priority=priority,
        timeout_minutes=timeout_minutes,
        notes=notes.strip() or None,
        active=checkbox_value(active),
    )

    db.add(provider)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

        return redirect_providers(
            error=(
                "Ese JID ya está registrado "
                "como proveedor."
            )
        )

    return redirect_providers(
        message="Proveedor agregado correctamente."
    )


@router.post(
    "/providers/{provider_id}/delete"
)
def delete_provider(
    provider_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return redirect_providers(
            error="Token de seguridad inválido."
        )

    provider = db.get(
        Provider,
        provider_id,
    )

    if provider is None:
        return redirect_providers(
            error="Proveedor no encontrado."
        )

    if provider.deleted_at is not None:
        return redirect_providers(
            message=(
                "El proveedor ya estaba eliminado."
            )
        )

    provider_name = provider.name
    provider_jid = provider.whatsapp_jid

    provider.active = False
    provider.deleted_at = datetime.now(UTC)

    register_admin_audit(
        db,
        request,
        action="PROVIDER_DELETED",
        entity_type="PROVIDER",
        entity_id=provider.id,
        summary=(
            f"Proveedor #{provider.id} eliminado"
        ),
        details=(
            f"Nombre: {provider_name}\n"
            f"JID: {provider_jid}\n"
            "Tipo: eliminación lógica; "
            "historial conservado"
        ),
    )

    db.commit()

    return redirect_providers(
        message=(
            f"Proveedor «{provider_name}» "
            "eliminado correctamente. "
            "Su historial se conservó."
        )
    )


@router.post(
    "/providers/{provider_id}/update"
)
def update_provider(
    provider_id: int,
    request: Request,
    name: str = Form(...),
    whatsapp_jid: str = Form(...),
    evolution_instance: str = Form(...),
    response_header: str = Form(""),
    priority: int = Form(...),
    timeout_minutes: int = Form(...),
    notes: str = Form(""),
    csrf_token: str = Form(...),
    active: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    redirect = require_authenticated(
        request
    )

    if redirect:
        return redirect

    if not validate_csrf(
        request,
        csrf_token,
    ):
        return redirect_providers(
            error="Token de seguridad inválido."
        )

    provider = db.get(
        Provider,
        provider_id,
    )

    if provider is None:
        return redirect_providers(
            error="Proveedor no encontrado."
        )

    provider_before = {
        "name":
            provider.name,
        "whatsapp_jid":
            provider.whatsapp_jid,
        "evolution_instance":
            provider.evolution_instance,
        "response_header":
            provider.response_header,
        "priority":
            provider.priority,
        "timeout_minutes":
            provider.timeout_minutes,
        "notes":
            provider.notes,
        "active":
            provider.active,
    }

    try:
        jid = validate_provider_jid(
            whatsapp_jid
        )

    except ValueError as validation_error:
        return redirect_providers(
            error=str(validation_error)
        )

    instance_name = evolution_instance.strip()

    if not name.strip():
        return redirect_providers(
            error="El nombre es obligatorio."
        )

    if not instance_name:
        return redirect_providers(
            error=(
                "La instancia de Evolution "
                "es obligatoria."
            )
        )

    if not 1 <= priority <= 999:
        return redirect_providers(
            error=(
                "La prioridad debe estar "
                "entre 1 y 999."
            )
        )

    if not 1 <= timeout_minutes <= 1440:
        return redirect_providers(
            error=(
                "El tiempo de espera debe estar "
                "entre 1 y 1440 minutos."
            )
        )

    provider.name = name.strip()
    provider.whatsapp_jid = jid
    provider.evolution_instance = (
        instance_name
    )
    provider.response_header = (
        response_header.strip() or None
    )
    provider.priority = priority
    provider.timeout_minutes = (
        timeout_minutes
    )
    provider.notes = (
        notes.strip() or None
    )
    provider.active = (
        checkbox_value(active)
    )

    provider_after = {
        "name":
            provider.name,
        "whatsapp_jid":
            provider.whatsapp_jid,
        "evolution_instance":
            provider.evolution_instance,
        "response_header":
            provider.response_header,
        "priority":
            provider.priority,
        "timeout_minutes":
            provider.timeout_minutes,
        "notes":
            provider.notes,
        "active":
            provider.active,
    }

    changed_fields = []

    for key in provider_before:
        if (
            provider_before[key]
            != provider_after[key]
        ):
            changed_fields.append(
                f"{key}: "
                f"{provider_before[key]} -> "
                f"{provider_after[key]}"
            )

    try:
        if changed_fields:
            register_admin_audit(
                db,
                request,
                action="PROVIDER_UPDATED",
                entity_type="PROVIDER",
                entity_id=provider.id,
                summary=(
                    f"Proveedor #{provider.id} "
                    "actualizado"
                ),
                details="\\n".join(
                    changed_fields
                ),
            )

        db.commit()

    except IntegrityError:
        db.rollback()

        return redirect_providers(
            error=(
                "No se pudo guardar. "
                "El JID ya pertenece a "
                "otro proveedor."
            )
        )

    return redirect_providers(
        message=(
            "Proveedor actualizado correctamente."
        )
    )


