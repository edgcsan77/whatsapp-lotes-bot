from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.daily_cutoff import DailyCutoff
from app.models.request import Request


DELIVERED_REQUEST_STATUSES = {
    "DELIVERED",
}

FAILED_REQUEST_STATUSES = {
    "CURP_LOOKUP_FAILED",
}


class DailyCutoffError(Exception):
    """Error al calcular o registrar un corte diario."""


@dataclass(frozen=True)
class CutoffPeriod:
    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime
    timezone_name: str


@dataclass(frozen=True)
class CutoffTotals:
    total_requests: int
    idcif_count: int
    delivered_count: int
    pending_count: int
    failed_count: int
    rfc_count: int
    curp_count: int
    total_amount: Decimal


def parse_cutoff_time(value: str) -> time:
    raw = str(value or "").strip()

    try:
        parsed = datetime.strptime(
            raw,
            "%H:%M",
        )
    except ValueError as exc:
        raise DailyCutoffError(
            f"INVALID_CUTOFF_TIME:{raw}"
        ) from exc

    return time(
        hour=parsed.hour,
        minute=parsed.minute,
    )


def get_client_timezone(
    timezone_name: str,
) -> ZoneInfo:
    raw = str(
        timezone_name or ""
    ).strip()

    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError as exc:
        raise DailyCutoffError(
            f"INVALID_TIMEZONE:{raw}"
        ) from exc


def local_cutoff_datetime(
    local_date: date,
    cutoff_time: time,
    timezone: ZoneInfo,
) -> datetime:
    return datetime.combine(
        local_date,
        cutoff_time,
        tzinfo=timezone,
    )


def calculate_latest_cutoff_period(
    *,
    now_utc: datetime,
    cutoff_time_value: str,
    timezone_name: str,
) -> CutoffPeriod:
    if now_utc.tzinfo is None:
        raise DailyCutoffError(
            "NOW_MUST_BE_TIMEZONE_AWARE"
        )

    timezone = get_client_timezone(
        timezone_name
    )

    cutoff_time = parse_cutoff_time(
        cutoff_time_value
    )

    normalized_now_utc = (
        now_utc.astimezone(UTC)
    )

    now_local = (
        normalized_now_utc.astimezone(
            timezone
        )
    )

    today_cutoff_local = (
        local_cutoff_datetime(
            now_local.date(),
            cutoff_time,
            timezone,
        )
    )

    if now_local >= today_cutoff_local:
        end_local = today_cutoff_local
    else:
        end_local = local_cutoff_datetime(
            now_local.date()
            - timedelta(days=1),
            cutoff_time,
            timezone,
        )

    start_local = local_cutoff_datetime(
        end_local.date()
        - timedelta(days=1),
        cutoff_time,
        timezone,
    )

    return CutoffPeriod(
        start_utc=start_local.astimezone(
            UTC
        ),
        end_utc=end_local.astimezone(
            UTC
        ),
        start_local=start_local,
        end_local=end_local,
        timezone_name=timezone_name,
    )


def get_requests_for_period(
    db: Session,
    *,
    client_id: int,
    period: CutoffPeriod,
) -> list[Request]:
    return list(
        db.scalars(
            select(Request)
            .where(
                Request.client_id
                == client_id,
                Request.received_at
                >= period.start_utc,
                Request.received_at
                < period.end_utc,
            )
            .order_by(
                Request.received_at.asc(),
                Request.id.asc(),
            )
        )
    )


def calculate_cutoff_totals(
    requests: list[Request],
) -> CutoffTotals:
    delivered_count = 0
    idcif_count = 0
    failed_count = 0
    pending_count = 0
    rfc_count = 0
    curp_count = 0
    total_amount = Decimal("0.00")

    for request in requests:
        status = str(
            request.status or ""
        ).strip().upper()

        if status in DELIVERED_REQUEST_STATUSES:
            delivered_count += 1

            if (
                str(request.result_code or "")
                .strip()
                .upper()
                == "OK"
                and str(request.idcif or "").strip()
            ):
                idcif_count += 1
        elif status in FAILED_REQUEST_STATUSES:
            failed_count += 1
        else:
            pending_count += 1

        input_type = str(
            request.input_type or ""
        ).strip().upper()

        if input_type == "CURP":
            curp_count += 1
        else:
            rfc_count += 1

        total_amount += Decimal(
            request.sale_price
            or Decimal("0.00")
        )

    return CutoffTotals(
        total_requests=len(requests),
        idcif_count=idcif_count,
        delivered_count=delivered_count,
        pending_count=pending_count,
        failed_count=failed_count,
        rfc_count=rfc_count,
        curp_count=curp_count,
        total_amount=total_amount.quantize(
            Decimal("0.01")
        ),
    )


def find_existing_cutoff(
    db: Session,
    *,
    client_id: int,
    period: CutoffPeriod,
) -> DailyCutoff | None:
    return db.scalar(
        select(DailyCutoff).where(
            DailyCutoff.client_id
            == client_id,
            DailyCutoff.period_start
            == period.start_utc,
            DailyCutoff.period_end
            == period.end_utc,
        )
    )


def create_daily_cutoff(
    db: Session,
    *,
    client: Client,
    now_utc: datetime | None = None,
) -> tuple[DailyCutoff, bool]:
    if not client.active:
        raise DailyCutoffError(
            "CLIENT_INACTIVE"
        )

    if not client.daily_cutoff_enabled:
        raise DailyCutoffError(
            "DAILY_CUTOFF_DISABLED"
        )

    current_utc = (
        now_utc
        if now_utc is not None
        else datetime.now(UTC)
    )

    period = calculate_latest_cutoff_period(
        now_utc=current_utc,
        cutoff_time_value=(
            client.daily_cutoff_time
        ),
        timezone_name=client.timezone,
    )

    existing = find_existing_cutoff(
        db,
        client_id=client.id,
        period=period,
    )

    if existing is not None:
        return existing, False

    requests = get_requests_for_period(
        db,
        client_id=client.id,
        period=period,
    )

    totals = calculate_cutoff_totals(
        requests
    )

    cutoff = DailyCutoff(
        client_id=client.id,
        period_start=period.start_utc,
        period_end=period.end_utc,
        total_requests=(
            totals.total_requests
        ),
        delivered_count=(
            totals.delivered_count
        ),
        pending_count=(
            totals.pending_count
        ),
        failed_count=(
            totals.failed_count
        ),
        rfc_count=totals.rfc_count,
        curp_count=totals.curp_count,
        total_amount=(
            totals.total_amount
        ),
        status="CREATED",
    )

    db.add(cutoff)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

        existing = find_existing_cutoff(
            db,
            client_id=client.id,
            period=period,
        )

        if existing is None:
            raise

        return existing, False

    db.refresh(cutoff)

    return cutoff, True


def format_local_datetime(
    value: datetime,
) -> str:
    return value.strftime(
        "%d/%m/%Y %H:%M"
    )


def render_daily_cutoff_message(
    *,
    client: Client,
    period: CutoffPeriod,
    totals: CutoffTotals,
) -> str:
    months = {
        1: "ENERO",
        2: "FEBRERO",
        3: "MARZO",
        4: "ABRIL",
        5: "MAYO",
        6: "JUNIO",
        7: "JULIO",
        8: "AGOSTO",
        9: "SEPTIEMBRE",
        10: "OCTUBRE",
        11: "NOVIEMBRE",
        12: "DICIEMBRE",
    }

    cutoff_date = period.end_local

    date_text = (
        f"{cutoff_date.day} DE "
        f"{months[cutoff_date.month]} DEL "
        f"{cutoff_date.year}"
    )

    return (
        f"CORTE - {date_text}         "
        "🧾 Resumen del día\n\n\n"
        f"📄 *Total de IDCIF: "
        f"{totals.idcif_count}*\n\n\n"
        "Si algún rastreo se envió doble, "
        "favor de aclarar para que podamos "
        "ajustar el corte."
    )


async def send_daily_cutoff(
    db: Session,
    *,
    cutoff: DailyCutoff,
    client: Client,
    message: str,
) -> DailyCutoff:
    from app.integrations.evolution_client import (
        EvolutionAPIError,
        send_text_message,
    )

    if cutoff.status == "SENT":
        return cutoff

    try:
        send_result = await send_text_message(
            destination_jid=client.whatsapp_jid,
            text=message,
        )

    except (
        EvolutionAPIError,
        ValueError,
    ) as exc:
        cutoff.status = "SEND_FAILED"
        cutoff.error_message = str(exc)[:1000]
        db.commit()
        db.refresh(cutoff)
        return cutoff

    cutoff.status = "SENT"
    cutoff.whatsapp_message_id = (
        send_result.message_id
    )
    cutoff.error_message = None
    cutoff.sent_at = datetime.now(UTC)

    db.commit()
    db.refresh(cutoff)

    return cutoff
