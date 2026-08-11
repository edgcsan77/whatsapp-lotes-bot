import re
import unicodedata
from dataclasses import dataclass


RFC_PATTERN = (
    r"[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}"
)

IDCIF_PATTERN = (
    r"[0-9]{8,20}"
)

SPECIAL_VALUE_PATTERN = (
    r"(?:SR|S/R|SIN\s+RESULTADO|NO\s+ID|SIN\s+ID)"
)


RFC_VALUE_LINE_PATTERN = re.compile(
    rf"^\s*"
    rf"({RFC_PATTERN})"
    rf"(?:\s+|\t+)"
    rf"({IDCIF_PATTERN}|{SPECIAL_VALUE_PATTERN})"
    rf"\s*$",
    re.IGNORECASE,
)


VALUE_RFC_LINE_PATTERN = re.compile(
    rf"^\s*"
    rf"({IDCIF_PATTERN}|{SPECIAL_VALUE_PATTERN})"
    rf"(?:\s+|\t+)"
    rf"({RFC_PATTERN})"
    rf"\s*$",
    re.IGNORECASE,
)


RFC_ONLY_PATTERN = re.compile(
    rf"^\s*({RFC_PATTERN})\s*$",
    re.IGNORECASE,
)


VALUE_ONLY_PATTERN = re.compile(
    rf"^\s*"
    rf"({IDCIF_PATTERN}|{SPECIAL_VALUE_PATTERN})"
    rf"\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedProviderResult:
    rfc: str
    raw_value: str | None
    idcif: str | None
    result_code: str
    raw_line: str


def normalize_line(
    value: str,
) -> str:
    value = str(
        value or ""
    )

    value = unicodedata.normalize(
        "NFKC",
        value,
    )

    value = value.replace(
        "\u00a0",
        " ",
    )

    value = value.upper()

    value = re.sub(
        r"\bRFC\s*:\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"\bIDCIF\s*:\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"[ \t]+",
        " ",
        value,
    )

    return value.strip()

def build_result(
    *,
    rfc: str,
    value: str | None,
    raw_line: str,
) -> ParsedProviderResult:
    rfc = normalize_line(
        rfc
    )

    normalized_value = (
        normalize_line(
            value or ""
        )
        or None
    )

    if (
        normalized_value
        and normalized_value.isdigit()
    ):
        idcif = normalized_value
        result_code = "OK"

    elif normalized_value in {
        "SR",
        "S/R",
        "SIN RESULTADO",
    }:
        idcif = None
        result_code = "SIN_RESULTADO"

    elif normalized_value in {
        "NO ID",
        "SIN ID",
    }:
        idcif = None
        result_code = "SIN_ID"

    else:
        idcif = None
        result_code = "RFC_ONLY"

    return ParsedProviderResult(
        rfc=rfc,
        raw_value=normalized_value,
        idcif=idcif,
        result_code=result_code,
        raw_line=raw_line,
    )


def parse_provider_message(
    text: str,
) -> list[ParsedProviderResult]:
    results: list[
        ParsedProviderResult
    ] = []

    seen_rfcs: set[str] = set()

    # Puede quedar pendiente:
    #
    # RFC
    # ...
    #
    # o:
    #
    # IDCIF
    # ...
    pending_rfc: str | None = None
    pending_value: str | None = None

    pending_raw_line: str | None = None

    def append_result(
        *,
        rfc: str,
        value: str | None,
        raw_line: str,
    ) -> None:
        normalized_rfc = (
            normalize_line(
                rfc
            )
        )

        if normalized_rfc in seen_rfcs:
            return

        seen_rfcs.add(
            normalized_rfc
        )

        results.append(
            build_result(
                rfc=normalized_rfc,
                value=value,
                raw_line=raw_line,
            )
        )

    def flush_pending_rfc() -> None:
        nonlocal pending_rfc
        nonlocal pending_raw_line

        if pending_rfc is None:
            return

        append_result(
            rfc=pending_rfc,
            value=None,
            raw_line=(
                pending_raw_line
                or pending_rfc
            ),
        )

        pending_rfc = None
        pending_raw_line = None

    def clear_pending_value() -> None:
        nonlocal pending_value
        nonlocal pending_raw_line

        pending_value = None
        pending_raw_line = None

    for raw_line in str(
        text or ""
    ).splitlines():
        normalized = normalize_line(
            raw_line
        )

        if not normalized:
            continue

        # -------------------------
        # RFC IDCIF
        # RFC NO ID
        # RFC SR
        # -------------------------
        match = (
            RFC_VALUE_LINE_PATTERN
            .fullmatch(
                normalized
            )
        )

        if match:
            flush_pending_rfc()
            clear_pending_value()

            append_result(
                rfc=match.group(1),
                value=match.group(2),
                raw_line=raw_line,
            )

            continue

        # -------------------------
        # IDCIF RFC
        # NO ID RFC
        # SR RFC
        # -------------------------
        match = (
            VALUE_RFC_LINE_PATTERN
            .fullmatch(
                normalized
            )
        )

        if match:
            flush_pending_rfc()
            clear_pending_value()

            append_result(
                rfc=match.group(2),
                value=match.group(1),
                raw_line=raw_line,
            )

            continue

        # -------------------------
        # RFC solamente
        # -------------------------
        match = (
            RFC_ONLY_PATTERN
            .fullmatch(
                normalized
            )
        )

        if match:
            rfc = normalize_line(
                match.group(1)
            )

            # Caso:
            #
            # IDCIF
            # RFC
            if pending_value is not None:
                append_result(
                    rfc=rfc,
                    value=pending_value,
                    raw_line=(
                        f"{pending_raw_line}\n"
                        f"{raw_line}"
                    ),
                )

                clear_pending_value()

                continue

            # Si ya había RFC pendiente,
            # significa que nunca llegó su valor.
            flush_pending_rfc()

            pending_rfc = rfc
            pending_raw_line = raw_line

            continue

        # -------------------------
        # IDCIF solamente
        # NO ID solamente
        # SR solamente
        # -------------------------
        match = (
            VALUE_ONLY_PATTERN
            .fullmatch(
                normalized
            )
        )

        if match:
            value = normalize_line(
                match.group(1)
            )

            # Caso:
            #
            # RFC
            # IDCIF
            if pending_rfc is not None:
                append_result(
                    rfc=pending_rfc,
                    value=value,
                    raw_line=(
                        f"{pending_raw_line}\n"
                        f"{raw_line}"
                    ),
                )

                pending_rfc = None
                pending_raw_line = None

                continue

            # Caso posible:
            #
            # IDCIF
            # RFC
            pending_value = value
            pending_raw_line = raw_line

            continue

        # Encabezados, nombres, comentarios,
        # emojis u otro texto se ignoran.

    flush_pending_rfc()

    # Un IDCIF aislado sin RFC no genera
    # ningún resultado porque no sabemos
    # a qué solicitud pertenece.

    return results
