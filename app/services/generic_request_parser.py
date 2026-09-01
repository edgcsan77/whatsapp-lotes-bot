import re
import unicodedata
from dataclasses import dataclass
from typing import Literal


CURP_VALUE_PATTERN = (
    r"[A-Z]{4}"
    r"\d{6}"
    r"[HM]"
    r"[A-Z]{5}"
    r"[A-Z0-9]"
    r"\d"
)

RFC_VALUE_PATTERN = (
    r"[A-ZÑ&]{3,4}"
    r"\d{6}"
    r"[A-Z0-9]{3}"
)

GENERIC_CURP_PATTERN = re.compile(
    r"(?<![A-Z0-9Ñ&])"
    rf"(?P<identifier>{CURP_VALUE_PATTERN})"
    r"\s*"
    r"\#"
    r"(?![A-Z0-9Ñ&])",
    re.IGNORECASE,
)

GENERIC_RFC_PATTERN = re.compile(
    r"(?<![A-Z0-9Ñ&])"
    rf"(?P<identifier>{RFC_VALUE_PATTERN})"
    r"\s*"
    r"#"
    r"(?![A-Z0-9Ñ&])",
    re.IGNORECASE,
)

GENERIC_REQUEST_PATTERNS = (
    ("CURP", GENERIC_CURP_PATTERN),
    ("RFC", GENERIC_RFC_PATTERN),
)


@dataclass(frozen=True)
class ParsedGenericRequest:
    identifier_type: Literal[
        "CURP",
        "RFC",
    ]

    identifier: str
    rfc: str | None
    curp: str | None
    lookup_route: str
    display_identifier: str


def normalize_generic_text(
    value: str,
) -> str:
    text = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )

    text = text.replace(
        "\u00a0",
        " ",
    )

    return text.upper()


def extract_generic_requests(
    text: str,
) -> list[ParsedGenericRequest]:
    normalized = normalize_generic_text(
        text
    )

    output: list[
        ParsedGenericRequest
    ] = []

    seen: set[
        tuple[str, str]
    ] = set()

    matches: list[
        tuple[int, int, str, re.Match[str]]
    ] = []

    for identifier_type, pattern in (
        GENERIC_REQUEST_PATTERNS
    ):
        for match in pattern.finditer(normalized):
            matches.append(
                (
                    match.start(),
                    match.end(),
                    identifier_type,
                    match,
                )
            )

    matches.sort(
        key=lambda item: (
            item[0],
            item[1],
        )
    )

    for (
        _start,
        _end,
        identifier_type,
        match,
    ) in matches:
        identifier = (
            match.group("identifier")
            .strip()
            .upper()
        )

        deduplication_key = (
            identifier_type,
            identifier,
        )

        if deduplication_key in seen:
            continue

        seen.add(
            deduplication_key
        )

        if identifier_type == "CURP":
            lookup_route = (
                "CURP_NL_SEPOMEX_"
                "NO_CHECKID"
            )
            rfc = None
            curp = identifier
            display_identifier = (
                f"{identifier}#"
            )
        else:
            lookup_route = "RFC_CHECKID"
            rfc = identifier
            curp = None
            display_identifier = (
                f"{identifier}#"
            )

        output.append(
            ParsedGenericRequest(
                identifier_type=
                    identifier_type,
                identifier=identifier,
                rfc=rfc,
                curp=curp,
                lookup_route=lookup_route,
                display_identifier=
                    display_identifier,
            )
        )

    return output


def strip_generic_requests(
    text: str,
) -> str:
    normalized = normalize_generic_text(
        text
    )

    output = normalized

    for _identifier_type, pattern in (
        GENERIC_REQUEST_PATTERNS
    ):
        output = pattern.sub(
            " ",
            output,
        )

    return output
