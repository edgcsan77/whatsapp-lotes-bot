import re
import unicodedata
from dataclasses import dataclass


RFC_VALUE_PATTERN = (
    r"[A-ZÑ&]{3,4}"
    r"\d{6}"
    r"[A-Z0-9]{3}"
)

IDCIF_VALUE_PATTERN = r"\d{8,20}"

# Formatos aceptados:
#   RFC IDCIF
#   RFC | IDCIF
#   RFC: XXX IDCIF: 123
#   XXX, 123
#   XXX; 123
# También acepta salto de línea entre ambos valores.
DIRECT_REQUEST_PATTERN = re.compile(
    r"(?<![A-Z0-9Ñ&])"
    r"(?:RFC\s*[:=-]?\s*)?"
    rf"(?P<rfc>{RFC_VALUE_PATTERN})"
    r"\s*(?:\||,|;|\s+)\s*"
    r"(?:ID\s*CIF|IDCIF)?"
    r"\s*[:=-]?\s*"
    rf"(?P<idcif>{IDCIF_VALUE_PATTERN})"
    r"(?!\d)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedDirectRequest:
    rfc: str
    idcif: str
    display_identifier: str


def normalize_direct_text(value: str) -> str:
    text = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )
    text = text.replace("\u00a0", " ")
    return text.upper()


def extract_direct_requests(
    text: str,
) -> list[ParsedDirectRequest]:
    normalized = normalize_direct_text(text)
    output: list[ParsedDirectRequest] = []
    seen: set[tuple[str, str]] = set()

    for match in DIRECT_REQUEST_PATTERN.finditer(normalized):
        rfc = match.group("rfc").strip().upper()
        idcif = match.group("idcif").strip()
        key = (rfc, idcif)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            ParsedDirectRequest(
                rfc=rfc,
                idcif=idcif,
                display_identifier=f"{rfc} {idcif}",
            )
        )

    return output


def strip_direct_requests(text: str) -> str:
    normalized = normalize_direct_text(text)
    return DIRECT_REQUEST_PATTERN.sub(" ", normalized)
