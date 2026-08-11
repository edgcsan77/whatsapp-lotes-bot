from app.services.provider_parser import (
    parse_provider_message,
)
from app.services.provider_response_service import (
    build_client_result_message,
    render_provider_result_line,
)


def test_result_order_is_preserved_from_provider() -> None:
    parsed = parse_provider_message(
        "RAHC850707NW3 22010123989\n"
        "VALA830403RA8 19060153257"
    )

    assert [result.rfc for result in parsed] == [
        "RAHC850707NW3",
        "VALA830403RA8",
    ]


def test_ok_result_line() -> None:
    parsed = parse_provider_message(
        "VALA830403RA8 19060153257"
    )

    assert render_provider_result_line(
        parsed[0]
    ) == "VALA830403RA8 19060153257"


def test_rfc_only_result_line() -> None:
    parsed = parse_provider_message(
        "VALA830403RA8"
    )

    assert render_provider_result_line(
        parsed[0]
    ) == "NO ID VALA830403RA8"


def test_sin_id_result_line() -> None:
    parsed = parse_provider_message(
        "VALA830403RA8 SIN ID"
    )

    assert render_provider_result_line(
        parsed[0]
    ) == "NO ID VALA830403RA8"


def test_multiple_client_result_message() -> None:
    parsed = parse_provider_message(
        "RAHC850707NW3 22010123989\n"
        "VALA830403RA8 19060153257"
    )

    text = build_client_result_message(parsed)

    assert "✅ Resultados recibidos" in text
    assert "RAHC850707NW3 22010123989" in text
    assert "VALA830403RA8 19060153257" in text
