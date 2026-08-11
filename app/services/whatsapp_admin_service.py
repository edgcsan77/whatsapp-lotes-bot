import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.admin_audit_log import AdminAuditLog
from app.models.client import Client
from app.models.provider import Provider


@dataclass
class WhatsAppAdminResult:
    handled: bool
    response_text: str | None = None


def canonical_jid(
    value: str | None,
) -> str:
    jid = str(value or "").strip().lower()

    if not jid:
        return ""

    # Número simple:
    # 5218991234567 -> ...@s.whatsapp.net
    if jid.isdigit():
        return f"{jid}@s.whatsapp.net"

    if "@" not in jid:
        return jid

    local, domain = jid.split("@", 1)

    # JID multidispositivo:
    # 5218991234567:12@s.whatsapp.net
    # -> 5218991234567@s.whatsapp.net
    if domain == "s.whatsapp.net":
        local = local.split(":", 1)[0]

    return f"{local}@{domain}"


def get_admin_jids() -> set[str]:
    raw = str(
        settings.whatsapp_admin_jids or ""
    )

    return {
        canonical_jid(item)
        for item in raw.split(",")
        if canonical_jid(item)
    }


def actor_jid(
    *,
    source_jid: str,
    sender_jid: str | None,
) -> str:
    # Grupo:
    # sender_jid = persona que escribió.
    #
    # Privado:
    # sender_jid normalmente viene vacío y
    # source_jid es el usuario.
    if str(source_jid).endswith("@g.us"):
        return canonical_jid(sender_jid)

    return canonical_jid(
        sender_jid or source_jid
    )


def is_whatsapp_admin(
    *,
    source_jid: str,
    sender_jid: str | None,
) -> bool:
    actor = actor_jid(
        source_jid=source_jid,
        sender_jid=sender_jid,
    )

    return (
        bool(actor)
        and actor in get_admin_jids()
    )


def parse_price(
    value: str | None,
) -> Decimal | None:
    raw = str(value or "").strip()

    if not raw:
        return None

    try:
        price = Decimal(raw)
    except InvalidOperation:
        return None

    if price < Decimal("0"):
        return None

    return price.quantize(
        Decimal("0.01")
    )


def private_target_jid(
    value: str,
) -> str | None:
    raw = str(value or "").strip()

    if raw.endswith("@s.whatsapp.net"):
        return canonical_jid(raw)

    digits = re.sub(
        r"\D",
        "",
        raw,
    )

    if len(digits) < 10:
        return None

    return canonical_jid(
        f"{digits}@s.whatsapp.net"
    )


def default_client_name(
    *,
    target_jid: str,
    source_type: str,
    sender_name: str | None,
) -> str:
    if (
        source_type == "private"
        and sender_name
    ):
        return str(sender_name).strip()[:160]

    identifier = (
        target_jid.split("@", 1)[0][-8:]
    )

    if source_type == "group":
        return f"Grupo WhatsApp {identifier}"

    return f"Chat WhatsApp {identifier}"


def get_default_provider(
    db: Session,
) -> Provider | None:
    return db.scalar(
        select(Provider)
        .where(
            Provider.active.is_(True),
            Provider.deleted_at.is_(None),
        )
        .order_by(
            Provider.priority.asc(),
            Provider.id.asc(),
        )
        .limit(1)
    )


def audit(
    db: Session,
    *,
    actor: str,
    action: str,
    client: Client,
    summary: str,
    details: str | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            admin_user=f"whatsapp:{actor}",
            action=action,
            entity_type="CLIENT",
            entity_id=client.id,
            summary=summary,
            details=details,
            ip_address=None,
        )
    )


def authorize_client(
    db: Session,
    *,
    target_jid: str,
    source_type: str,
    actor: str,
    sender_name: str | None,
    price: Decimal | None,
    name: str | None,
) -> str:
    provider = get_default_provider(db)

    if provider is None:
        return (
            "⛔ No existe un proveedor activo.\n"
            "Configura primero un proveedor "
            "desde el panel."
        )

    client = db.scalar(
        select(Client).where(
            Client.whatsapp_jid
            == target_jid
        )
    )

    requested_name = (
        str(name or "").strip()[:160]
    )

    if client is None:
        client = Client(
            name=(
                requested_name
                or default_client_name(
                    target_jid=target_jid,
                    source_type=source_type,
                    sender_name=sender_name,
                )
            ),
            source_type=source_type,
            whatsapp_jid=target_jid,
            default_provider_id=provider.id,
            price_per_request=(
                price
                if price is not None
                else Decimal("0.00")
            ),
            batch_enabled=True,
            batch_interval_minutes=
                settings
                .default_batch_interval_minutes,
            batch_max_items=
                settings
                .default_batch_max_items,
            daily_cutoff_enabled=True,
            daily_cutoff_time=
                settings
                .default_daily_cutoff_time,
            timezone="America/Mexico_City",
            active=True,
        )

        db.add(client)

        try:
            db.flush()
        except IntegrityError:
            db.rollback()

            return (
                "⛔ No fue posible registrar "
                "el cliente."
            )

        audit(
            db,
            actor=actor,
            action=
                "CLIENT_AUTHORIZED_WHATSAPP",
            client=client,
            summary=(
                f"Cliente #{client.id} "
                "autorizado por WhatsApp"
            ),
            details=(
                f"JID: {target_jid}\n"
                f"Tipo: {source_type}\n"
                f"Precio: "
                f"{client.price_per_request}"
            ),
        )

        db.commit()

        return (
            "✅ *Cliente autorizado*\n\n"
            f"Nombre: {client.name}\n"
            "Estado: Activo"
        )

    # Ya existe: reactivamos y opcionalmente
    # actualizamos nombre/precio.
    client.active = True
    client.deleted_at = None
    client.batch_enabled = True
    client.daily_cutoff_enabled = True
    client.source_type = source_type
    client.default_provider_id = (
        provider.id
    )

    if requested_name:
        client.name = requested_name

    if price is not None:
        client.price_per_request = price

    audit(
        db,
        actor=actor,
        action=
            "CLIENT_REAUTHORIZED_WHATSAPP",
        client=client,
        summary=(
            f"Cliente #{client.id} "
            "reactivado por WhatsApp"
        ),
        details=(
            f"JID: {target_jid}\n"
            f"Precio: "
            f"{client.price_per_request}"
        ),
    )

    db.commit()

    return (
        "✅ *Cliente autorizado*\n\n"
        f"Nombre: {client.name}\n"
        "Estado: Activo"
    )


def deactivate_client(
    db: Session,
    *,
    target_jid: str,
    actor: str,
) -> str:
    client = db.scalar(
        select(Client).where(
            Client.whatsapp_jid
            == target_jid
        )
    )

    if client is None:
        return (
            "⚠️ Ese chat no está "
            "registrado como cliente."
        )

    if not client.active:
        return (
            "ℹ️ Ese cliente ya estaba "
            "desactivado."
        )

    client.active = False

    audit(
        db,
        actor=actor,
        action=
            "CLIENT_DEAUTHORIZED_WHATSAPP",
        client=client,
        summary=(
            f"Cliente #{client.id} "
            "desautorizado por WhatsApp"
        ),
        details=f"JID: {target_jid}",
    )

    db.commit()

    return (
        "✅ *Cliente desautorizado*\n\n"
        f"Nombre: {client.name}\n"
        "Estado: Inactivo"
    )


def audit_provider(
    db: Session,
    *,
    actor: str,
    action: str,
    provider: Provider,
    summary: str,
    details: str | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            admin_user=f"whatsapp:{actor}",
            action=action,
            entity_type="PROVIDER",
            entity_id=provider.id,
            summary=summary,
            details=details,
            ip_address=None,
        )
    )


def authorize_provider(
    db: Session,
    *,
    target_jid: str,
    actor: str,
    priority: int,
    timeout_minutes: int,
    name: str,
) -> str:
    target_jid = str(
        target_jid or ""
    ).strip()

    if not target_jid.endswith("@g.us"):
        return (
            "⚠️ Este comando solo puede "
            "utilizarse dentro de un grupo "
            "de WhatsApp."
        )

    if not 1 <= priority <= 999:
        return (
            "⚠️ La prioridad debe estar "
            "entre 1 y 999."
        )

    if not 1 <= timeout_minutes <= 1440:
        return (
            "⚠️ El tiempo de espera debe "
            "estar entre 1 y 1440 minutos."
        )

    provider_name = str(
        name or ""
    ).strip()[:160]

    if not provider_name:
        return (
            "⚠️ El nombre del proveedor "
            "es obligatorio."
        )

    provider = db.scalar(
        select(Provider).where(
            Provider.whatsapp_jid
            == target_jid
        )
    )

    if provider is None:
        provider = Provider(
            name=provider_name,
            whatsapp_jid=target_jid,
            evolution_instance=
                settings.evolution_instance,
            response_header=None,
            priority=priority,
            timeout_minutes=timeout_minutes,
            notes=(
                "Autorizado mediante "
                "comando WhatsApp"
            ),
            active=True,
            deleted_at=None,
        )

        db.add(provider)

        try:
            db.flush()

        except IntegrityError:
            db.rollback()

            return (
                "⛔ No fue posible registrar "
                "el proveedor."
            )

        audit_provider(
            db,
            actor=actor,
            action=
                "PROVIDER_AUTHORIZED_WHATSAPP",
            provider=provider,
            summary=(
                f"Proveedor #{provider.id} "
                "autorizado por WhatsApp"
            ),
            details=(
                f"JID: {target_jid}\n"
                f"Prioridad: {priority}\n"
                f"Espera: {timeout_minutes}\n"
                f"Instancia: "
                f"{settings.evolution_instance}"
            ),
        )

        db.commit()

        return (
            "✅ *Proveedor autorizado*\n\n"
            f"Nombre: {provider.name}\n"
            f"Prioridad: "
            f"{provider.priority}\n"
            f"Espera: "
            f"{provider.timeout_minutes} min\n"
            f"Instancia: "
            f"{provider.evolution_instance}\n"
            "Estado: Activo"
        )

    was_deleted = (
        provider.deleted_at is not None
    )

    provider.name = provider_name
    provider.priority = priority
    provider.timeout_minutes = (
        timeout_minutes
    )
    provider.evolution_instance = (
        settings.evolution_instance
    )
    provider.active = True
    provider.deleted_at = None

    audit_provider(
        db,
        actor=actor,
        action=(
            "PROVIDER_RESTORED_WHATSAPP"
            if was_deleted
            else
            "PROVIDER_REAUTHORIZED_WHATSAPP"
        ),
        provider=provider,
        summary=(
            f"Proveedor #{provider.id} "
            + (
                "restaurado por WhatsApp"
                if was_deleted
                else
                "reactivado por WhatsApp"
            )
        ),
        details=(
            f"JID: {target_jid}\n"
            f"Prioridad: {priority}\n"
            f"Espera: {timeout_minutes}\n"
            f"Instancia: "
            f"{settings.evolution_instance}"
        ),
    )

    db.commit()

    return (
        "✅ *Proveedor autorizado*\n\n"
        f"Nombre: {provider.name}\n"
        f"Prioridad: "
        f"{provider.priority}\n"
        f"Espera: "
        f"{provider.timeout_minutes} min\n"
        f"Instancia: "
        f"{provider.evolution_instance}\n"
        + (
            "Estado: Restaurado y activo"
            if was_deleted
            else "Estado: Activo"
        )
    )


def deactivate_provider(
    db: Session,
    *,
    target_jid: str,
    actor: str,
) -> str:
    target_jid = str(
        target_jid or ""
    ).strip()

    if not target_jid.endswith("@g.us"):
        return (
            "⚠️ Este comando solo puede "
            "utilizarse dentro de un grupo."
        )

    provider = db.scalar(
        select(Provider).where(
            Provider.whatsapp_jid
            == target_jid
        )
    )

    if provider is None:
        return (
            "⚠️ Este grupo no está "
            "registrado como proveedor."
        )

    if provider.deleted_at is not None:
        return (
            "ℹ️ Este proveedor está eliminado.\n"
            "Usa /autorizarprov para restaurarlo."
        )

    if not provider.active:
        return (
            "ℹ️ Este proveedor ya estaba "
            "desactivado."
        )

    provider.active = False

    audit_provider(
        db,
        actor=actor,
        action=
            "PROVIDER_DEAUTHORIZED_WHATSAPP",
        provider=provider,
        summary=(
            f"Proveedor #{provider.id} "
            "desautorizado por WhatsApp"
        ),
        details=(
            f"JID: {target_jid}"
        ),
    )

    db.commit()

    return (
        "✅ *Proveedor desautorizado*\n\n"
        f"Nombre: {provider.name}\n"
        "Estado: Inactivo"
    )


def delete_provider_whatsapp(
    db: Session,
    *,
    target_jid: str,
    actor: str,
) -> str:
    target_jid = str(
        target_jid or ""
    ).strip()

    if not target_jid.endswith("@g.us"):
        return (
            "⚠️ Este comando solo puede "
            "utilizarse dentro de un grupo."
        )

    provider = db.scalar(
        select(Provider).where(
            Provider.whatsapp_jid
            == target_jid
        )
    )

    if provider is None:
        return (
            "⚠️ Este grupo no está "
            "registrado como proveedor."
        )

    if provider.deleted_at is not None:
        return (
            "ℹ️ Este proveedor ya estaba "
            "eliminado."
        )

    provider.active = False
    provider.deleted_at = (
        datetime.now(UTC)
    )

    audit_provider(
        db,
        actor=actor,
        action=
            "PROVIDER_DELETED_WHATSAPP",
        provider=provider,
        summary=(
            f"Proveedor #{provider.id} "
            "eliminado por WhatsApp"
        ),
        details=(
            f"JID: {target_jid}\n"
            "Tipo: eliminación lógica; "
            "historial conservado"
        ),
    )

    db.commit()

    return (
        "✅ *Proveedor eliminado*\n\n"
        f"Nombre: {provider.name}\n"
        "Estado: Eliminado\n\n"
        "El historial fue conservado."
    )


def process_whatsapp_admin_command(
    db: Session,
    *,
    source_jid: str,
    sender_jid: str | None,
    sender_name: str | None,
    text: str,
    from_me: bool = False,
) -> WhatsAppAdminResult:
    command_text = str(
        text or ""
    ).strip()

    if not command_text.startswith("/"):
        return WhatsAppAdminResult(
            handled=False
        )

    parts = command_text.split()

    if not parts:
        return WhatsAppAdminResult(
            handled=False
        )

    command = parts[0].lower()

    supported = {
        "/autorizargrupo",
        "/desautorizargrupo",
        "/autorizarprivado",
        "/desautorizarprivado",
        "/autorizarprov",
        "/desautorizarprov",
        "/eliminarprov",
        "/ayudaprov",
        "/ayudaadmin",
    }

    if command not in supported:
        return WhatsAppAdminResult(
            handled=False
        )

    if (
        not from_me
        and not is_whatsapp_admin(
            source_jid=source_jid,
            sender_jid=sender_jid,
        )
    ):
        # Se procesa el comando, pero jamás
        # permite que siga como solicitud.
        return WhatsAppAdminResult(
            handled=True,
            response_text=(
                "⛔ Comando administrativo "
                "no autorizado."
            ),
        )

    actor = actor_jid(
        source_jid=source_jid,
        sender_jid=sender_jid,
    )

    if command == "/ayudaprov":
        return WhatsAppAdminResult(
            handled=True,
            response_text=(
                "🛠️ *Comandos proveedor*\n\n"
                "/autorizarprov\n"
                "/desautorizarprov\n"
                "/eliminarprov"
            ),
        )

    if command == "/ayudaadmin":
        return WhatsAppAdminResult(
            handled=True,
            response_text=(
                "🛠️ *Comandos administrativos*\n\n"
                "*Grupos*\n"
                "/autorizargrupo\n"
                "/desautorizargrupo\n\n"
                "*Chats privados*\n"
                "/autorizarprivado\n"
                "/desautorizarprivado\n\n"
                "*Proveedores*\n"
                "/autorizarprov\n"
                "/desautorizarprov\n"
                "/eliminarprov"
            ),
        )

    if command == "/autorizarprov":
        target_jid = canonical_jid(
            source_jid
        )

        if (
            not target_jid
            or not target_jid.endswith("@g.us")
        ):
            return WhatsAppAdminResult(
                handled=True,
                response_text=(
                    "⚠️ /autorizarprov debe "
                    "usarse dentro del grupo "
                    "proveedor."
                ),
            )

        return WhatsAppAdminResult(
            handled=True,
            response_text=authorize_provider(
                db,
                target_jid=target_jid,
                actor=actor,
                priority=100,
                timeout_minutes=60,
                name="Proveedor WhatsApp",
            ),
        )

    if command == "/desautorizarprov":
        return WhatsAppAdminResult(
            handled=True,
            response_text=deactivate_provider(
                db,
                target_jid=source_jid,
                actor=actor,
            ),
        )

    if command == "/eliminarprov":
        return WhatsAppAdminResult(
            handled=True,
            response_text=
                delete_provider_whatsapp(
                    db,
                    target_jid=source_jid,
                    actor=actor,
                ),
        )

    if command in {
        "/autorizargrupo",
        "/desautorizargrupo",
    }:
        target_jid = canonical_jid(
            source_jid
        )

        if (
            not target_jid
            or not target_jid.endswith("@g.us")
        ):
            return WhatsAppAdminResult(
                handled=True,
                response_text=(
                    "⚠️ Este comando debe "
                    "usarse dentro del grupo "
                    "cliente."
                ),
            )

        if command == "/desautorizargrupo":
            return WhatsAppAdminResult(
                handled=True,
                response_text=deactivate_client(
                    db,
                    target_jid=target_jid,
                    actor=actor,
                ),
            )

        return WhatsAppAdminResult(
            handled=True,
            response_text=authorize_client(
                db,
                target_jid=target_jid,
                source_type="group",
                actor=actor,
                sender_name=None,
                price=None,
                name=None,
            ),
        )

    if command == "/autorizarprivado":
        target_jid = canonical_jid(
            source_jid
        )

        if (
            not target_jid
            or target_jid.endswith("@g.us")
        ):
            return WhatsAppAdminResult(
                handled=True,
                response_text=(
                    "⚠️ /autorizarprivado "
                    "debe usarse dentro del "
                    "chat privado del cliente."
                ),
            )

        return WhatsAppAdminResult(
            handled=True,
            response_text=authorize_client(
                db,
                target_jid=target_jid,
                source_type="private",
                actor=actor,
                sender_name=sender_name,
                price=None,
                name=None,
            ),
        )

    if command == "/desautorizarprivado":
        target_jid = canonical_jid(
            source_jid
        )

        if (
            not target_jid
            or target_jid.endswith("@g.us")
        ):
            return WhatsAppAdminResult(
                handled=True,
                response_text=(
                    "⚠️ /desautorizarprivado "
                    "debe usarse dentro del "
                    "chat privado del cliente."
                ),
            )

        return WhatsAppAdminResult(
            handled=True,
            response_text=deactivate_client(
                db,
                target_jid=target_jid,
                actor=actor,
            ),
        )

    return WhatsAppAdminResult(
        handled=False
    )
