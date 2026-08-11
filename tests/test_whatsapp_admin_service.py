from app.config import settings
from app.services.whatsapp_admin_service import (
    is_whatsapp_admin,
)


def test_configured_admin_is_recognized(
    monkeypatch,
) -> None:
    jid = (
        "5218991112233"
        "@s.whatsapp.net"
    )

    monkeypatch.setattr(
        settings,
        "whatsapp_admin_jids",
        jid,
    )

    assert is_whatsapp_admin(
        source_jid=jid,
        sender_jid=None,
    ) is True
