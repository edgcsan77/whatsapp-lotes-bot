import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings


logger = logging.getLogger(__name__)


class PdfBackendError(Exception):
    """Error comunicándose con constancia-backend."""


@dataclass(frozen=True)
class PdfBackendResult:
    pdf_url: str
    filename: str
    raw_response: dict[str, Any]


def normalize_pdf_backend_endpoint(
    value: str,
) -> str:
    base_url = str(
        value or ""
    ).strip().rstrip("/")

    if not base_url:
        raise PdfBackendError(
            "PDF_BACKEND_URL_EMPTY"
        )

    if base_url.endswith(
        "/internal/generate-pdf"
    ):
        return base_url

    return (
        f"{base_url}"
        "/internal/generate-pdf"
    )


async def generate_pdf_document(
    *,
    payload: dict[str, Any],
) -> PdfBackendResult:
    endpoint = (
        normalize_pdf_backend_endpoint(
            settings.pdf_backend_url
        )
    )

    token = str(
        settings.pdf_backend_token
        or ""
    ).strip()

    if not token:
        raise PdfBackendError(
            "PDF_BACKEND_TOKEN_EMPTY"
        )

    headers = {
        "Authorization":
            f"Bearer {token}",
        "Content-Type":
            "application/json",
        "Accept":
            "application/json",
    }

    timeout = httpx.Timeout(
        connect=20.0,
        read=300.0,
        write=30.0,
        pool=20.0,
    )

    try:
        async with httpx.AsyncClient(
            timeout=timeout
        ) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=payload,
            )

    except httpx.HTTPError as error:
        logger.exception(
            "Error de conexión con "
            "constancia-backend"
        )

        raise PdfBackendError(
            "PDF_BACKEND_CONNECTION_ERROR:"
            f"{type(error).__name__}"
        ) from error

    try:
        response_data = response.json()

    except ValueError as error:
        raise PdfBackendError(
            "PDF_BACKEND_INVALID_JSON:"
            f"{response.status_code}"
        ) from error

    if not isinstance(
        response_data,
        dict,
    ):
        raise PdfBackendError(
            "PDF_BACKEND_RESPONSE_NOT_OBJECT"
        )

    if response.status_code >= 400:
        backend_error = str(
            response_data.get("error")
            or response_data.get("message")
            or ""
        ).strip()

        raise PdfBackendError(
            "PDF_BACKEND_HTTP_"
            f"{response.status_code}:"
            f"{backend_error[:500]}"
        )

    if not bool(
        response_data.get("ok")
    ):
        backend_error = str(
            response_data.get("error")
            or response_data.get("message")
            or ""
        ).strip()

        raise PdfBackendError(
            "PDF_BACKEND_NOT_OK:"
            f"{backend_error[:500]}"
        )

    mode = str(
        response_data.get("mode")
        or "single"
    ).strip().lower()

    if mode != "single":
        raise PdfBackendError(
            "PDF_BACKEND_UNEXPECTED_MODE:"
            f"{mode}"
        )

    pdf_url = str(
        response_data.get("pdf_url")
        or ""
    ).strip()

    filename = str(
        response_data.get("filename")
        or "constancia.pdf"
    ).strip()

    if not pdf_url.startswith(
        (
            "http://",
            "https://",
        )
    ):
        raise PdfBackendError(
            "PDF_BACKEND_URL_INVALID"
        )

    if not filename:
        filename = "constancia.pdf"

    return PdfBackendResult(
        pdf_url=pdf_url,
        filename=filename,
        raw_response=response_data,
    )
