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
    candidates: list[re.Match[str]] = []

    for pattern in DIRECT_REQUEST_PATTERNS:
        candidates.extend(
            pattern.finditer(normalized)
        )

    # Un mismo bloque puede coincidir en ambos sentidos:
    #
    # RFC1 IDCIF1
    # RFC2 IDCIF2
    #
    # Antes, además de los dos pares correctos, el patrón
    # IDCIF_FIRST podía interpretar:
    #
    # IDCIF1 + RFC2
    #
    # porque \\s+ acepta también saltos de línea.
    #
    # Damos prioridad a parejas contenidas en la misma línea
    # y después aceptamos únicamente coincidencias que no se
    # superpongan con otra ya elegida. Esto conserva soporte
    # para RFC/IDCIF enviados en dos líneas, pero evita el
    # corrimiento entre solicitudes consecutivas.
    candidates.sort(
        key=lambda item: (
            "\n" in item.group(0),
            item.start(),
            item.end(),
        )
    )

    selected: list[re.Match[str]] = []

    for candidate in candidates:
        overlaps = any(
            candidate.start() < current.end()
            and current.start() < candidate.end()
            for current in selected
        )

        if overlaps:
            continue

        selected.append(candidate)

    # La prioridad anterior sirve solo para resolver conflictos.
    # La salida final debe conservar el orden original del mensaje.
    selected.sort(
        key=lambda item: (
            item.start(),
            item.end(),
        )
    )

    return selected


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

    # Usa exactamente las mismas coincidencias depuradas que
    # extract_direct_requests. Así un IDCIF de una solicitud
    # nunca se consume junto con el RFC de la siguiente.
    matches = _find_direct_matches(normalized)

    if not matches:
        return normalized

    output = list(normalized)

    for match in matches:
        output[
            match.start():match.end()
        ] = " " * (
            match.end() - match.start()
        )

    return "".join(output)
