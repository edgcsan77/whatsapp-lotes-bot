from datetime import UTC, datetime
from decimal import Decimal

from app.models.request import Request
from app.services.daily_cutoff_service import (
    calculate_cutoff_totals,
)


def make_request(
    *,
    request_id: int,
    service_type: str,
    idcif: str,
    price: str,
) -> Request:
    return Request(
        id=request_id,
        client_id=1,
        provider_id=None,
        whatsapp_message_id=f"CUT-{request_id}",
        identifier_key=f"RFC00000{request_id:03d}"[-13:],
        source_jid="120363000000004001@g.us",
        sender_jid=None,
        sender_name=None,
        original_text="test",
        input_type="RFC",
        service_type=service_type,
        delivery_format="PDF",
        lookup_route="DIRECT_RFC_IDCIF",
        rfc="MATL941108QM3",
        original_curp=None,
        detected_name=None,
        status="DELIVERED",
        idcif=idcif,
        result_code="OK",
        sale_price=Decimal(price),
        received_at=datetime.now(UTC),
        delivered_at=datetime.now(UTC),
    )


def test_direct_never_increments_idcif_count() -> None:
    regular = make_request(
        request_id=1,
        service_type="RFC_IDCIF",
        idcif="23070521799",
        price="2.00",
    )
    direct = make_request(
        request_id=2,
        service_type="CONSTANCIA_DIRECTA",
        idcif="16275230322",
        price="3.25",
    )

    totals = calculate_cutoff_totals(
        [regular, direct]
    )

    assert totals.idcif_count == 1
    assert totals.direct_count == 1
    assert totals.generic_count == 0
    assert totals.delivered_count == 2
    assert totals.total_amount == Decimal("5.25")
