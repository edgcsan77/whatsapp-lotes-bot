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

GENERIC_REQUEST_PATTERN = re.compile(
    r"(?<![A-Z0-9Ñ&])"
    rf"(?P<identifier>"
    rf"(?:{CURP_VALUE_PATTERN})"
    rf"|(?:{RFC_VALUE_PATTERN})"
    rf")"
    r"\s*"
    r"[-–—]"
    r"\s*"
    r"G"
    r"(?![A-Z0-9Ñ&])",
    re.IGNORECASE,
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

    for match in (
        GENERIC_REQUEST_PATTERN
        .finditer(normalized)
    ):
        identifier = (
            match.group("identifier")
            .strip()
            .upper()
        )

        identifier_type: Literal[
            "CURP",
            "RFC",
        ] = (
            "CURP"
            if len(identifier) == 18
            else "RFC"
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

        else:
            lookup_route = (
                "RFC_CHECKID"
            )

            rfc = identifier
            curp = None

        output.append(
            ParsedGenericRequest(
                identifier_type=
                    identifier_type,
                identifier=identifier,
                rfc=rfc,
                curp=curp,
                lookup_route=
                    lookup_route,
                display_identifier=(
                    f"{identifier}-G"
                ),
            )
        )

    return output


def strip_generic_requests(
    text: str,
) -> str:
    normalized = normalize_generic_text(
        text
    )

    return (
        GENERIC_REQUEST_PATTERN
        .sub(
            " ",
            normalized,
        )
    )
