from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.provider import Provider
from app.models.request import Request


@dataclass
class ProviderTimeoutResult:
    checked: int = 0

    timed_out_request_ids: list[int] = field(
        default_factory=list
    )


def ensure_utc(
    value: datetime,
) -> datetime:
    if value.tzinfo is None:
        return value.replace(
            tzinfo=UTC
        )

    return value.astimezone(
        UTC
    )


def is_provider_timeout_due(
    *,
    sent_at: datetime,
    timeout_minutes: int,
    now: datetime | None = None,
) -> bool:
    current = ensure_utc(
        now or datetime.now(UTC)
    )

    sent = ensure_utc(
        sent_at
    )

    timeout = max(
        1,
        int(timeout_minutes),
    )

    return current >= (
        sent
        + timedelta(
            minutes=timeout
        )
    )


def process_provider_timeouts(
    db: Session,
    *,
    now: datetime | None = None,
) -> ProviderTimeoutResult:
    current = ensure_utc(
        now or datetime.now(UTC)
    )

    result = ProviderTimeoutResult()

    rows = db.execute(
        select(
            Request,
            Provider,
        )
        .join(
            Provider,
            Provider.id
            == Request.provider_id,
        )
        .where(
            Request.status
            == "SENT_TO_PROVIDER",
            Request.sent_to_provider_at
            .is_not(None),
        )
        .order_by(
            Request.sent_to_provider_at.asc(),
            Request.id.asc(),
        )
        .with_for_update(
            skip_locked=True
        )
    ).all()

    for request, provider in rows:
        if request.sent_to_provider_at is None:
            continue

        result.checked += 1

        if not is_provider_timeout_due(
            sent_at=
                request.sent_to_provider_at,
            timeout_minutes=
                provider.timeout_minutes,
            now=current,
        ):
            continue

        request.status = (
            "PROVIDER_TIMEOUT"
        )

        result.timed_out_request_ids.append(
            request.id
        )

    if result.timed_out_request_ids:
        db.commit()

    return result
