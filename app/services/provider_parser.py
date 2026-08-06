import re
import unicodedata
from dataclasses import dataclass


RFC_LINE_PATTERN = re.compile(
    r"^\s*"
    r"([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})"
    r"(?:\s+|\t+)?"
    r"([0-9]{8,20}|SR|S/R|SIN\s+RESULTADO|NO\s+ID|SIN\s+ID)?"
    r"\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedProviderResult:
    rfc: str
    raw_value: str | None
    idcif: str | None
    result_code: str
    raw_line: str


def normalize_line(value: str) -> str:
    value = str(value or "")
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u00a0", " ")
    value = value.upper()
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def parse_provider_message(text: str) -> list[ParsedProviderResult]:
    results: list[ParsedProviderResult] = []
    seen_rfcs: set[str] = set()

    for raw_line in str(text or "").splitlines():
        normalized = normalize_line(raw_line)

        if not normalized:
            continue

        match = RFC_LINE_PATTERN.fullmatch(normalized)

        # Encabezados como ISAI se ignoran automáticamente
        if not match:
            continue

        rfc = match.group(1).upper()
        value = normalize_line(match.group(2) or "") or None

        if rfc in seen_rfcs:
            continue

        seen_rfcs.add(rfc)

        if value and value.isdigit():
            idcif = value
            result_code = "OK"
        elif value in {"SR", "S/R", "SIN RESULTADO"}:
            idcif = None
            result_code = "SIN_RESULTADO"
        elif value in {"NO ID", "SIN ID"}:
            idcif = None
            result_code = "SIN_ID"
        else:
            idcif = None
            result_code = "RFC_ONLY"

        results.append(
            ParsedProviderResult(
                rfc=rfc,
                raw_value=value,
                idcif=idcif,
                result_code=result_code,
                raw_line=raw_line,
            )
        )

    return results
