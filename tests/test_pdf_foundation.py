from types import SimpleNamespace

import pytest

from app.services.pdf_backend_client import (
    PdfBackendError,
    normalize_pdf_backend_endpoint,
)
from app.services.pdf_processing_service import (
    build_pdf_backend_payload,
    build_pdf_query,
)


CURP = "CASE020722HTSRNDA8"
RFC = "VALA830403RA8"
IDCIF = "25030288082"


def make_request(
    *,
    route: str,
):
    return SimpleNamespace(
        lookup_route=route,
        original_curp=(
            CURP
            if route
            == "CURP_NL_SEPOMEX_NO_CHECKID"
            else None
        ),
        rfc=(
            RFC
            if route
            in {
                "RFC_CHECKID",
                "DIRECT_RFC_IDCIF",
            }
            else None
        ),
        idcif=(
            IDCIF
            if route
            == "DIRECT_RFC_IDCIF"
            else None
        ),
        source_jid=
            "120363000000000001@g.us",
        original_text="SOLICITUD",
    )


def make_client():
    return SimpleNamespace(
        name="Cliente prueba",
        whatsapp_jid=
            "120363000000000001@g.us",
    )


def test_backend_endpoint_normalization() -> None:
    assert (
        normalize_pdf_backend_endpoint(
            "https://backend.example.com"
        )
        == (
            "https://backend.example.com"
            "/internal/generate-pdf"
        )
    )

    assert (
        normalize_pdf_backend_endpoint(
            "https://backend.example.com/"
            "internal/generate-pdf"
        )
        == (
            "https://backend.example.com/"
            "internal/generate-pdf"
        )
    )


def test_empty_backend_url_is_rejected() -> None:
    with pytest.raises(
        PdfBackendError
    ):
        normalize_pdf_backend_endpoint(
            ""
        )


def test_curp_route_never_contains_checkid_data() -> None:
    request = make_request(
        route=(
            "CURP_NL_SEPOMEX_NO_CHECKID"
        )
    )

    assert build_pdf_query(
        request
    ) == CURP

    payload = build_pdf_backend_payload(
        request=request,
        client=make_client(),
    )

    assert payload["lookup_route"] == (
        "CURP_NL_SEPOMEX_NO_CHECKID"
    )

    assert payload[
        "skip_internal_stats"
    ] is True


def test_rfc_route_requires_checkid() -> None:
    request = make_request(
        route="RFC_CHECKID"
    )

    assert build_pdf_query(
        request
    ) == RFC

    payload = build_pdf_backend_payload(
        request=request,
        client=make_client(),
    )

    assert payload["lookup_route"] == (
        "RFC_CHECKID"
    )


def test_direct_idcif_query() -> None:
    request = make_request(
        route="DIRECT_RFC_IDCIF"
    )

    assert build_pdf_query(
        request
    ) == (
        f"RFC: {RFC}\n"
        f"IDCIF: {IDCIF}"
    )


def test_invalid_route_is_rejected() -> None:
    request = make_request(
        route="CHECKID_FOR_CURP"
    )

    with pytest.raises(
        PdfBackendError
    ):
        build_pdf_query(
            request
        )
