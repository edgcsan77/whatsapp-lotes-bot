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


@dataclass(frozen=True)
class IdcifValidationResult:
    valid: bool
    terminal: bool
    code: str
    message: str
    raw_response: dict[str, Any]


def normalize_idcif_validation_endpoint(value: str) -> str:
    base_url = str(value or "").strip().rstrip("/")
    if not base_url:
        raise PdfBackendError("PDF_BACKEND_URL_EMPTY")

    for suffix in (
        "/internal/generate-pdf",
        "/internal/validate-rfc-idcif",
    ):
        if base_url.endswith(suffix):
            base_url = base_url[:-len(suffix)]
            break

    return f"{base_url}/internal/validate-rfc-idcif"


async def validate_rfc_idcif(*, rfc: str, idcif: str) -> IdcifValidationResult:
    import asyncio

    endpoint = normalize_idcif_validation_endpoint(settings.pdf_backend_url)
    token = str(settings.pdf_backend_token or "").strip()

    if not token:
        raise PdfBackendError("PDF_BACKEND_TOKEN_EMPTY")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "source_system": "LOTES",
        "rfc": str(rfc or "").strip().upper(),
        "idcif": str(idcif or "").strip(),
    }

    timeout = httpx.Timeout(
        connect=15.0,
        read=75.0,
        write=20.0,
        pool=20.0,
    )

    last_error: Exception | None = None

    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                )

            try:
                data = response.json()
            except ValueError as error:
                raise PdfBackendError(
                    f"IDCIF_VALIDATION_INVALID_JSON:{response.status_code}"
                ) from error

            if not isinstance(data, dict):
                raise PdfBackendError("IDCIF_VALIDATION_NOT_OBJECT")

            code = str(data.get("code") or data.get("error") or "").strip().upper()
            message = str(data.get("message") or "").strip()

            if response.status_code == 200:
                if not bool(data.get("valid")):
                    raise PdfBackendError("IDCIF_VALIDATION_200_NOT_VALID")

                return IdcifValidationResult(
                    valid=True,
                    terminal=False,
                    code=code or "OK",
                    message=message,
                    raw_response=data,
                )

            if response.status_code == 422:
                return IdcifValidationResult(
                    valid=False,
                    terminal=bool(data.get("terminal", True)),
                    code=code,
                    message=message,
                    raw_response=data,
                )

            if response.status_code < 500:
                raise PdfBackendError(
                    f"IDCIF_VALIDATION_HTTP_{response.status_code}:{code or message}"
                )

            last_error = PdfBackendError(
                f"IDCIF_VALIDATION_TEMPORARY:HTTP_{response.status_code}:{code or message}"
            )

        except httpx.HTTPError as error:
            last_error = PdfBackendError(
                f"IDCIF_VALIDATION_TEMPORARY:{type(error).__name__}"
            )

        except PdfBackendError as error:
            if "TEMPORARY" not in str(error) and "HTTP_5" not in str(error):
                raise
            last_error = error

        if attempt < 2:
            await asyncio.sleep(float(attempt))

    raise last_error or PdfBackendError("IDCIF_VALIDATION_TEMPORARY:UNKNOWN")
