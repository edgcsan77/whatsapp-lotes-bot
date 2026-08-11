import json
import time
from dataclasses import asdict, dataclass

from app.redis_client import redis_client


PENDING_PREFIX = "client_message_delay:"
PENDING_ZSET = "client_message_delay:due"

GROUP_PREFIX = "client_message_delay:group:"

DELAY_SECONDS = 60
RETENTION_SECONDS = 300


@dataclass(frozen=True)
class DelayedClientMessage:
    instance: str
    message_id: str
    source_jid: str
    sender_jid: str | None
    sender_name: str | None
    text: str
    group_due_at: float | None = None


def pending_key(
    *,
    instance: str,
    source_jid: str,
    message_id: str,
) -> str:
    return (
        f"{PENDING_PREFIX}"
        f"{instance}:"
        f"{source_jid}:"
        f"{message_id}"
    )


def group_key(
    *,
    instance: str,
    source_jid: str,
) -> str:
    return (
        f"{GROUP_PREFIX}"
        f"{instance}:"
        f"{source_jid}"
    )


_GROUP_DUE_SCRIPT = """
local current = redis.call('GET', KEYS[1])

if current then
    local current_number = tonumber(current)

    if current_number
       and current_number > tonumber(ARGV[1]) then
        return current
    end
end

redis.call(
    'SET',
    KEYS[1],
    ARGV[2],
    'EX',
    ARGV[3]
)

return ARGV[2]
"""


def get_or_create_group_due_at(
    *,
    instance: str,
    source_jid: str,
    delay_seconds: int,
) -> float:
    now = time.time()

    delay_seconds = max(
        1,
        int(delay_seconds),
    )

    candidate_due_at = (
        now + delay_seconds
    )

    key = group_key(
        instance=instance,
        source_jid=source_jid,
    )

    ttl_seconds = max(
        RETENTION_SECONDS,
        delay_seconds + 120,
    )

    value = redis_client.eval(
        _GROUP_DUE_SCRIPT,
        1,
        key,
        str(now),
        str(candidate_due_at),
        str(ttl_seconds),
    )

    return float(value)


def enqueue_client_message(
    message: DelayedClientMessage,
    *,
    delay_seconds: int = DELAY_SECONDS,
) -> float:
    due_at = get_or_create_group_due_at(
        instance=message.instance,
        source_jid=message.source_jid,
        delay_seconds=delay_seconds,
    )

    key = pending_key(
        instance=message.instance,
        source_jid=message.source_jid,
        message_id=message.message_id,
    )

    grouped_message = DelayedClientMessage(
        instance=message.instance,
        message_id=message.message_id,
        source_jid=message.source_jid,
        sender_jid=message.sender_jid,
        sender_name=message.sender_name,
        text=message.text,
        group_due_at=due_at,
    )

    payload = json.dumps(
        asdict(grouped_message),
        ensure_ascii=False,
        separators=(",", ":"),
    )

    pipe = redis_client.pipeline()

    pipe.set(
        key,
        payload,
        ex=max(
            RETENTION_SECONDS,
            int(delay_seconds) + 120,
        ),
    )

    pipe.zadd(
        PENDING_ZSET,
        {
            key: due_at,
        },
    )

    pipe.execute()

    return due_at


def cancel_client_message(
    *,
    instance: str,
    source_jid: str,
    message_id: str,
) -> bool:
    key = pending_key(
        instance=instance,
        source_jid=source_jid,
        message_id=message_id,
    )

    existed = bool(
        redis_client.exists(key)
    )

    pipe = redis_client.pipeline()
    pipe.delete(key)
    pipe.zrem(PENDING_ZSET, key)
    pipe.execute()

    return existed


def get_due_keys(
    *,
    now: float | None = None,
    limit: int = 5000,
) -> list[str]:
    timestamp = (
        time.time()
        if now is None
        else float(now)
    )

    values = redis_client.zrangebyscore(
        PENDING_ZSET,
        min="-inf",
        max=timestamp,
        start=0,
        num=max(1, int(limit)),
    )

    output: list[str] = []

    for value in values:
        if isinstance(value, bytes):
            value = value.decode(
                "utf-8",
                errors="replace",
            )

        output.append(str(value))

    return output


def get_pending_message(
    key: str,
) -> DelayedClientMessage | None:
    raw = redis_client.get(key)

    if not raw:
        return None

    if isinstance(raw, bytes):
        raw = raw.decode(
            "utf-8",
            errors="replace",
        )

    data = json.loads(raw)

    group_due_at = data.get(
        "group_due_at"
    )

    return DelayedClientMessage(
        instance=str(
            data.get("instance") or ""
        ),
        message_id=str(
            data.get("message_id") or ""
        ),
        source_jid=str(
            data.get("source_jid") or ""
        ),
        sender_jid=(
            str(data["sender_jid"])
            if data.get("sender_jid")
            else None
        ),
        sender_name=(
            str(data["sender_name"])
            if data.get("sender_name")
            else None
        ),
        text=str(
            data.get("text") or ""
        ),
        group_due_at=(
            float(group_due_at)
            if group_due_at is not None
            else None
        ),
    )


def remove_pending_key(
    key: str,
) -> None:
    pipe = redis_client.pipeline()
    pipe.delete(key)
    pipe.zrem(PENDING_ZSET, key)
    pipe.execute()


# ============================================================
# ACK RETRY QUEUE
# ============================================================

ACK_RETRY_PREFIX = "client_ack_retry:"
ACK_RETRY_ZSET = "client_ack_retry:due"

ACK_RETRY_RETENTION_SECONDS = 86400

ACK_RETRY_DELAYS = (
    60,
    120,
    300,
    600,
    1200,
    1800,
    3600,
    7200,
)


@dataclass(frozen=True)
class PendingAckRetry:
    retry_id: str
    instance: str
    source_jid: str
    text: str
    attempt: int = 0


def ack_retry_key(
    retry_id: str,
) -> str:
    return (
        f"{ACK_RETRY_PREFIX}"
        f"{retry_id}"
    )


def enqueue_ack_retry(
    retry: PendingAckRetry,
    *,
    delay_seconds: int | None = None,
) -> float:
    attempt = max(
        0,
        int(retry.attempt),
    )

    if delay_seconds is None:
        delay_index = min(
            attempt,
            len(ACK_RETRY_DELAYS) - 1,
        )

        delay_seconds = (
            ACK_RETRY_DELAYS[
                delay_index
            ]
        )

    delay_seconds = max(
        1,
        int(delay_seconds),
    )

    due_at = (
        time.time()
        + delay_seconds
    )

    key = ack_retry_key(
        retry.retry_id
    )

    payload = json.dumps(
        asdict(retry),
        ensure_ascii=False,
        separators=(",", ":"),
    )

    pipe = redis_client.pipeline()

    pipe.set(
        key,
        payload,
        ex=ACK_RETRY_RETENTION_SECONDS,
    )

    pipe.zadd(
        ACK_RETRY_ZSET,
        {
            key: due_at,
        },
    )

    pipe.execute()

    return due_at


def get_due_ack_retry_keys(
    *,
    now: float | None = None,
    limit: int = 500,
) -> list[str]:
    timestamp = (
        time.time()
        if now is None
        else float(now)
    )

    values = redis_client.zrangebyscore(
        ACK_RETRY_ZSET,
        min="-inf",
        max=timestamp,
        start=0,
        num=max(
            1,
            int(limit),
        ),
    )

    output: list[str] = []

    for value in values:
        if isinstance(
            value,
            bytes,
        ):
            value = value.decode(
                "utf-8",
                errors="replace",
            )

        output.append(
            str(value)
        )

    return output


def get_pending_ack_retry(
    key: str,
) -> PendingAckRetry | None:
    raw = redis_client.get(
        key
    )

    if not raw:
        return None

    if isinstance(
        raw,
        bytes,
    ):
        raw = raw.decode(
            "utf-8",
            errors="replace",
        )

    data = json.loads(
        raw
    )

    return PendingAckRetry(
        retry_id=str(
            data.get("retry_id")
            or ""
        ),
        instance=str(
            data.get("instance")
            or ""
        ),
        source_jid=str(
            data.get("source_jid")
            or ""
        ),
        text=str(
            data.get("text")
            or ""
        ),
        attempt=int(
            data.get("attempt")
            or 0
        ),
    )


def remove_ack_retry_key(
    key: str,
) -> None:
    pipe = redis_client.pipeline()

    pipe.delete(
        key
    )

    pipe.zrem(
        ACK_RETRY_ZSET,
        key,
    )

    pipe.execute()
