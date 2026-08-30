import re
import unicodedata
from dataclasses import dataclass


RFC_VALUE_PATTERN = (
    r"[A-ZÑ&]{3,4}"
    r"\d{6}"
    r"[A-Z0-9]{3}"
)

# Mantiene compatibilidad con la intención existente.
# La validación definitiva de LOTES exige 11 dígitos
# en constancia-backend.
IDCIF_VALUE_PATTERN = r"\d{8,20}"

SEPARATOR_PATTERN = (
    r"(?:"
    r"\s*(?:\||,|;)\s*"
    r"|"
    r"\s+"
    r")"
)

RFC_LABEL_PATTERN = (
    r"(?:RFC\s*[:=-]?\s*)?"
)

IDCIF_LABEL_PATTERN = (
    r"(?:(?:ID\s*CIF|IDCIF)\s*[:=-]?\s*)?"
)


# ------------------------------------------------------------
# RFC primero
#
# EUPA031107864 24070438891
# EUPA031107864
# 24070438891
# RFC: EUPA031107864 IDCIF: 24070438891
# ------------------------------------------------------------
RFC_FIRST_PATTERN = re.compile(
    r"(?<![A-Z0-9Ñ&])"
    rf"{RFC_LABEL_PATTERN}"
    rf"(?P<rfc>{RFC_VALUE_PATTERN})"
    rf"{SEPARATOR_PATTERN}"
    rf"{IDCIF_LABEL_PATTERN}"
    rf"(?P<idcif>{IDCIF_VALUE_PATTERN})"
    r"(?!\d)",
    re.IGNORECASE,
)


# ------------------------------------------------------------
# IDCIF primero
#
# 24070438891 EUPA031107864
# 24070438891
# EUPA031107864
# IDCIF: 24070438891 RFC: EUPA031107864
# ------------------------------------------------------------
IDCIF_FIRST_PATTERN = re.compile(
    r"(?<![A-Z0-9Ñ&])"
    rf"{IDCIF_LABEL_PATTERN}"
    rf"(?P<idcif>{IDCIF_VALUE_PATTERN})"
    rf"{SEPARATOR_PATTERN}"
    rf"{RFC_LABEL_PATTERN}"
    rf"(?P<rfc>{RFC_VALUE_PATTERN})"
    r"(?![A-Z0-9Ñ&])",
    re.IGNORECASE,
)


DIRECT_REQUEST_PATTERNS = (
    RFC_FIRST_PATTERN,
    IDCIF_FIRST_PATTERN,
)

# Compatibilidad con cualquier import histórico.
DIRECT_REQUEST_PATTERN = RFC_FIRST_PATTERN


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


def _find_direct_matches(
    normalized: str,
) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []

    for pattern in DIRECT_REQUEST_PATTERNS:
        matches.extend(
            pattern.finditer(normalized)
        )

    matches.sort(
        key=lambda item: (
            item.start(),
            item.end(),
        )
    )

    return matches


def extract_direct_requests(
    text: str,
) -> list[ParsedDirectRequest]:
    normalized = normalize_direct_text(text)
    output: list[ParsedDirectRequest] = []
    seen: set[tuple[str, str]] = set()

    for match in _find_direct_matches(
        normalized
    ):
        rfc = (
            match.group("rfc")
            .strip()
            .upper()
        )
        idcif = (
            match.group("idcif")
            .strip()
        )

        key = (rfc, idcif)

        if key in seen:
            continue

        seen.add(key)

        output.append(
            ParsedDirectRequest(
                rfc=rfc,
                idcif=idcif,
                display_identifier=(
                    f"{rfc} {idcif}"
                ),
            )
        )

    return output


def strip_direct_requests(
    text: str,
) -> str:
    normalized = normalize_direct_text(text)

    # Sustituimos en ambos sentidos para que el
    # RFC de una constancia directa nunca caiga
    # al parser de localización.
    output = normalized

    for pattern in DIRECT_REQUEST_PATTERNS:
        output = pattern.sub(
            " ",
            output,
        )

    return output
