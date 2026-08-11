import asyncio

from app.services import (
    delayed_client_message_worker
    as worker,
)


def test_send_group_message_success(
    monkeypatch,
) -> None:
    class Result:
        ok = True

    async def fake_send_text_message(
        **kwargs,
    ):
        return Result()

    monkeypatch.setattr(
        worker,
        "send_text_message",
        fake_send_text_message,
    )

    result = asyncio.run(
        worker.send_group_message(
            destination_jid=
                "5218990000000@s.whatsapp.net",
            instance="test",
            text="hola",
            log_label="test",
        )
    )

    assert result is True


def test_send_group_message_returns_false_on_error(
    monkeypatch,
) -> None:
    async def fake_send_text_message(
        **kwargs,
    ):
        raise ValueError(
            "TEST_ERROR"
        )

    monkeypatch.setattr(
        worker,
        "send_text_message",
        fake_send_text_message,
    )

    result = asyncio.run(
        worker.send_group_message(
            destination_jid=
                "5218990000000@s.whatsapp.net",
            instance="test",
            text="hola",
            log_label="test",
        )
    )

    assert result is False


def test_ack_retry_is_removed_after_success(
    monkeypatch,
) -> None:
    class Retry:
        retry_id = "retry-1"
        instance = "test"
        source_jid = (
            "5218990000000"
            "@s.whatsapp.net"
        )
        text = "ACK"
        attempt = 0

    removed: list[str] = []

    monkeypatch.setattr(
        worker,
        "get_due_ack_retry_keys",
        lambda **kwargs: [
            "client_ack_retry:retry-1"
        ],
    )

    monkeypatch.setattr(
        worker,
        "get_pending_ack_retry",
        lambda key: Retry(),
    )

    async def fake_send_group_message(
        **kwargs,
    ):
        return True

    monkeypatch.setattr(
        worker,
        "send_group_message",
        fake_send_group_message,
    )

    monkeypatch.setattr(
        worker,
        "remove_ack_retry_key",
        lambda key: removed.append(
            key
        ),
    )

    processed = asyncio.run(
        worker.process_due_ack_retries()
    )

    assert processed == 1

    assert removed == [
        "client_ack_retry:retry-1"
    ]


def test_ack_retry_is_rescheduled_after_failure(
    monkeypatch,
) -> None:
    class Retry:
        retry_id = "retry-2"
        instance = "test"
        source_jid = (
            "5218990000000"
            "@s.whatsapp.net"
        )
        text = "ACK"
        attempt = 0

    removed: list[str] = []
    enqueued: list[object] = []

    monkeypatch.setattr(
        worker,
        "get_due_ack_retry_keys",
        lambda **kwargs: [
            "client_ack_retry:retry-2"
        ],
    )

    monkeypatch.setattr(
        worker,
        "get_pending_ack_retry",
        lambda key: Retry(),
    )

    async def fake_send_group_message(
        **kwargs,
    ):
        return False

    monkeypatch.setattr(
        worker,
        "send_group_message",
        fake_send_group_message,
    )

    monkeypatch.setattr(
        worker,
        "remove_ack_retry_key",
        lambda key: removed.append(
            key
        ),
    )

    def fake_enqueue_ack_retry(
        retry,
        **kwargs,
    ):
        enqueued.append(
            retry
        )
        return 123456.0

    monkeypatch.setattr(
        worker,
        "enqueue_ack_retry",
        fake_enqueue_ack_retry,
    )

    processed = asyncio.run(
        worker.process_due_ack_retries()
    )

    assert processed == 0

    assert removed == [
        "client_ack_retry:retry-2"
    ]

    assert len(enqueued) == 1
    assert enqueued[0].retry_id == "retry-2"
    assert enqueued[0].attempt == 1


def test_ack_retry_is_removed_after_max_attempts(
    monkeypatch,
) -> None:
    class Retry:
        retry_id = "retry-max"
        instance = "test"
        source_jid = (
            "5218990000000"
            "@s.whatsapp.net"
        )
        text = "ACK"
        attempt = (
            len(worker.ACK_RETRY_DELAYS)
            - 1
        )

    removed: list[str] = []
    enqueued: list[object] = []

    monkeypatch.setattr(
        worker,
        "get_due_ack_retry_keys",
        lambda **kwargs: [
            "client_ack_retry:retry-max"
        ],
    )

    monkeypatch.setattr(
        worker,
        "get_pending_ack_retry",
        lambda key: Retry(),
    )

    async def fake_send_group_message(
        **kwargs,
    ):
        return False

    monkeypatch.setattr(
        worker,
        "send_group_message",
        fake_send_group_message,
    )

    monkeypatch.setattr(
        worker,
        "remove_ack_retry_key",
        lambda key: removed.append(
            key
        ),
    )

    monkeypatch.setattr(
        worker,
        "enqueue_ack_retry",
        lambda retry, **kwargs:
            enqueued.append(retry),
    )

    processed = asyncio.run(
        worker.process_due_ack_retries()
    )

    assert processed == 0

    assert removed == [
        "client_ack_retry:retry-max"
    ]

    assert enqueued == []


def test_multiple_messages_generate_one_grouped_ack(
    monkeypatch,
) -> None:
    import asyncio
    from types import SimpleNamespace

    from app.services.delayed_client_message_service import (
        DelayedClientMessage,
    )

    class DummyDB:
        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: DummyDB(),
    )

    def fake_register(
        db,
        message,
    ):
        return SimpleNamespace(
            created_identifiers=[
                message.text
            ],
            invalid_curps=[],
            recent_in_progress_identifiers=[],
            recent_processed_identifiers=[],
            created_count=1,
        )

    monkeypatch.setattr(
        worker,
        "register_client_message",
        fake_register,
    )

    sent_messages: list[dict] = []

    async def fake_send_group_message(
        **kwargs,
    ):
        sent_messages.append(
            kwargs
        )
        return True

    monkeypatch.setattr(
        worker,
        "send_group_message",
        fake_send_group_message,
    )

    removed: list[str] = []

    monkeypatch.setattr(
        worker,
        "remove_pending_key",
        lambda key: removed.append(
            key
        ),
    )

    entries = [
        (
            "key-1",
            DelayedClientMessage(
                instance="test",
                message_id="MSG-1",
                source_jid=
                    "CLIENT@g.us",
                sender_jid=None,
                sender_name=None,
                text=
                    "VALA830403RA8",
                group_due_at=100.0,
            ),
        ),
        (
            "key-2",
            DelayedClientMessage(
                instance="test",
                message_id="MSG-2",
                source_jid=
                    "CLIENT@g.us",
                sender_jid=None,
                sender_name=None,
                text=
                    "RAHC850707NW3",
                group_due_at=100.0,
            ),
        ),
        (
            "key-3",
            DelayedClientMessage(
                instance="test",
                message_id="MSG-3",
                source_jid=
                    "CLIENT@g.us",
                sender_jid=None,
                sender_name=None,
                text=
                    "MECA7305107Y3",
                group_due_at=100.0,
            ),
        ),
    ]

    processed = asyncio.run(
        worker.process_pending_group(
            entries
        )
    )

    assert processed == 3

    # Un solo ACK para los 3 mensajes.
    assert len(sent_messages) == 1

    assert sent_messages[0][
        "destination_jid"
    ] == "CLIENT@g.us"

    assert (
        "3 RFC"
        in sent_messages[0]["text"]
    )

    assert set(removed) == {
        "key-1",
        "key-2",
        "key-3",
    }


def test_failed_grouped_ack_is_queued_without_recreating_requests(
    monkeypatch,
) -> None:
    import asyncio
    from types import SimpleNamespace

    from app.services.delayed_client_message_service import (
        DelayedClientMessage,
    )

    class DummyDB:
        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: DummyDB(),
    )

    register_calls: list[str] = []

    def fake_register(
        db,
        message,
    ):
        register_calls.append(
            message.message_id
        )

        return SimpleNamespace(
            created_identifiers=[
                message.text
            ],
            invalid_curps=[],
            recent_in_progress_identifiers=[],
            recent_processed_identifiers=[],
            created_count=1,
        )

    monkeypatch.setattr(
        worker,
        "register_client_message",
        fake_register,
    )

    async def failed_send(
        **kwargs,
    ):
        return False

    monkeypatch.setattr(
        worker,
        "send_group_message",
        failed_send,
    )

    queued: list[object] = []

    def fake_enqueue(
        retry,
        **kwargs,
    ):
        queued.append(
            retry
        )
        return 123456.0

    monkeypatch.setattr(
        worker,
        "enqueue_ack_retry",
        fake_enqueue,
    )

    removed: list[str] = []

    monkeypatch.setattr(
        worker,
        "remove_pending_key",
        lambda key: removed.append(
            key
        ),
    )

    entries = [
        (
            "key-A",
            DelayedClientMessage(
                instance="test",
                message_id="MSG-A",
                source_jid=
                    "CLIENT@g.us",
                sender_jid=None,
                sender_name=None,
                text=
                    "VALA830403RA8",
                group_due_at=100.0,
            ),
        ),
        (
            "key-B",
            DelayedClientMessage(
                instance="test",
                message_id="MSG-B",
                source_jid=
                    "CLIENT@g.us",
                sender_jid=None,
                sender_name=None,
                text=
                    "RAHC850707NW3",
                group_due_at=100.0,
            ),
        ),
    ]

    processed = asyncio.run(
        worker.process_pending_group(
            entries
        )
    )

    assert processed == 2

    # Cada solicitud se registró una sola vez.
    assert register_calls == [
        "MSG-A",
        "MSG-B",
    ]

    # Solo el ACK se manda a retry.
    assert len(queued) == 1

    assert "2 RFC" in queued[0].text
    assert queued[0].attempt == 0

    # Las keys originales sí desaparecen:
    # el retry NO vuelve a crear las Requests.
    assert set(removed) == {
        "key-A",
        "key-B",
    }
