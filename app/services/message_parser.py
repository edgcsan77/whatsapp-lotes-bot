import re
import unicodedata
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Literal


RFC_PATTERN = re.compile(
    r"(?<![A-Z0-9Ñ&])"
    r"([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})"
    r"(?![A-Z0-9Ñ&])",
    re.IGNORECASE,
)

CURP_PATTERN = re.compile(
    r"(?<![A-Z0-9])"
    r"([A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d)"
    r"(?![A-Z0-9])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedClientRequest:
    identifier_type: Literal["RFC", "CURP"]
    identifier: str
    rfc: str | None
    curp: str | None
    detected_name: str | None
    original_text: str
    ignored_curps: tuple[str, ...]


def normalize_text(value: str) -> str:
    value = str(value or "")
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u00a0", " ")
    value = value.upper()
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def unique_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []

    for value in values:
        normalized = value.strip().upper()

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        output.append(normalized)

    return output


def extract_rfcs(text: str) -> list[str]:
    normalized = normalize_text(text)
    return unique_preserving_order(
        match.group(1).upper()
        for match in RFC_PATTERN.finditer(normalized)
    )


def extract_curps(text: str) -> list[str]:
    normalized = normalize_text(text)
    return unique_preserving_order(
        match.group(1).upper()
        for match in CURP_PATTERN.finditer(normalized)
    )


def looks_like_name(line: str) -> bool:
    candidate = normalize_text(line)
    candidate = re.sub(r"^[*#\-:•]+\s*", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip()

    if not candidate:
        return False

    if RFC_PATTERN.search(candidate) or CURP_PATTERN.search(candidate):
        return False

    if any(character.isdigit() for character in candidate):
        return False

    ignored_phrases = (
        "BUEN DIA",
        "BUENOS DIAS",
        "EXCELENTE DIA",
        "GRACIAS",
        "POR FA",
        "POR FAVOR",
        "ME APOYAS",
        "CONSTANCIA",
        "TEAM",
        "XIO",
    )

    if any(phrase in candidate for phrase in ignored_phrases):
        return False

    words = candidate.split()

    if not 2 <= len(words) <= 7:
        return False

    return all(
        re.fullmatch(r"[A-ZÁÉÍÓÚÜÑ.'-]+", word)
        for word in words
    )


def extract_name(text: str) -> str | None:
    candidates: list[str] = []

    for raw_line in str(text or "").splitlines():
        line = normalize_text(raw_line)
        line = re.sub(r"^[*#\-:•]+\s*", "", line).strip()

        if looks_like_name(line):
            candidates.append(line)

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda value: (
            len(value.split()),
            len(value),
        ),
    )


def parse_client_message(text: str) -> list[ParsedClientRequest]:
    original_text = str(text or "")
    rfcs = extract_rfcs(original_text)
    curps = extract_curps(original_text)
    detected_name = extract_name(original_text)

    results: list[ParsedClientRequest] = []

    # RFC y CURP pueden convivir en el mismo
    # mensaje. Se procesan TODOS.
    for rfc in rfcs:
        results.append(
            ParsedClientRequest(
                identifier_type="RFC",
                identifier=rfc,
                rfc=rfc,
                curp=None,
                detected_name=detected_name,
                original_text=original_text,
                ignored_curps=(),
            )
        )

    for curp in curps:
        results.append(
            ParsedClientRequest(
                identifier_type="CURP",
                identifier=curp,
                rfc=None,
                curp=curp,
                detected_name=detected_name,
                original_text=original_text,
                ignored_curps=(),
            )
        )

    return results
