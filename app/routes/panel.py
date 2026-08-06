import hashlib
import hmac
import os
import secrets
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.client import Client


load_dotenv()

router = APIRouter(
    prefix="/panel",
    tags=["panel"],
)

templates = Jinja2Templates(
    directory="app/templates"
)


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

    url = "/panel"

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
    "",
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
            select(Client).order_by(
                Client.active.desc(),
                Client.name.asc(),
            )
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="panel/clients.html",
        context={
            "request": request,
            "title": "Clientes",
            "active_page": "clients",
            "clients": clients,
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
            daily_cutoff_time.strip(),
        timezone=timezone.strip(),
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

    try:
        price = parse_price(
            price_per_request
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
        daily_cutoff_time.strip()
    )
    client.timezone = timezone.strip()
    client.active = checkbox_value(active)

    try:
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
