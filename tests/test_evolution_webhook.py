from app.services.evolution_webhook import (
    parse_evolution_payload,
    secrets_match,
)


def test_parse_group_message() -> None:
    payload = {
        "event": "messages.upsert",
        "instance": "lotesbot",
        "data": {
            "key": {
                "remoteJid":
                    "120363999999999999@g.us",
                "fromMe": False,
                "id": "MSG-EVOLUTION-001",
                "participant":
                    "5218990000000@s.whatsapp.net",
            },
            "pushName": "CLIENTE PRUEBA",
            "message": {
                "conversation":
                    "VALA830403RA8",
            },
        },
    }

    parsed = parse_evolution_payload(
        payload
    )

    assert parsed is not None
    assert parsed.instance == "lotesbot"
    assert parsed.message_id == "MSG-EVOLUTION-001"
    assert (
        parsed.source_jid
        == "120363999999999999@g.us"
    )
    assert (
        parsed.sender_jid
        == "5218990000000@s.whatsapp.net"
    )
    assert parsed.sender_name == "CLIENTE PRUEBA"
    assert parsed.text == "VALA830403RA8"
    assert parsed.from_me is False


def test_parse_private_message() -> None:
    payload = {
        "event": "MESSAGES_UPSERT",
        "instance": "lotesbot",
        "data": {
            "key": {
                "remoteJid":
                    "5218990000000@s.whatsapp.net",
                "fromMe": False,
                "id": "MSG-EVOLUTION-002",
            },
            "message": {
                "extendedTextMessage": {
                    "text":
                        "BEEJ760109HSLRSL03",
                }
            },
        },
    }

    parsed = parse_evolution_payload(
        payload
    )

    assert parsed is not None
    assert (
        parsed.source_jid
        == "5218990000000@s.whatsapp.net"
    )
    assert parsed.text == "BEEJ760109HSLRSL03"


def test_image_caption_is_supported() -> None:
    payload = {
        "event": "messages.upsert",
        "instance": "lotesbot",
        "data": {
            "key": {
                "remoteJid":
                    "120363999999999999@g.us",
                "fromMe": False,
                "id": "MSG-EVOLUTION-003",
            },
            "message": {
                "imageMessage": {
                    "caption":
                        "VALA830403RA8",
                }
            },
        },
    }

    parsed = parse_evolution_payload(
        payload
    )

    assert parsed is not None
    assert parsed.text == "VALA830403RA8"


def test_message_from_bot_is_detected() -> None:
    payload = {
        "event": "messages.upsert",
        "instance": "lotesbot",
        "data": {
            "key": {
                "remoteJid":
                    "120363999999999999@g.us",
                "fromMe": True,
                "id": "MSG-EVOLUTION-004",
            },
            "message": {
                "conversation":
                    "VALA830403RA8",
            },
        },
    }

    parsed = parse_evolution_payload(
        payload
    )

    assert parsed is not None
    assert parsed.from_me is True


def test_unsupported_event_is_ignored() -> None:
    payload = {
        "event": "connection.update",
        "instance": "lotesbot",
        "data": {},
    }

    assert parse_evolution_payload(payload) is None


def test_incomplete_payload_is_ignored() -> None:
    payload = {
        "event": "messages.upsert",
        "instance": "lotesbot",
        "data": {
            "message": {
                "conversation":
                    "VALA830403RA8",
            }
        },
    }

    assert parse_evolution_payload(payload) is None


def test_webhook_secret_comparison() -> None:
    assert secrets_match(
        "SECRET-123",
        "SECRET-123",
    )

    assert not secrets_match(
        "INCORRECTO",
        "SECRET-123",
    )

    assert not secrets_match(
        None,
        "SECRET-123",
    )
