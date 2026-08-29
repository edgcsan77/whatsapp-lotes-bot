import re
from datetime import datetime


RFC_EXACT_PATTERN = re.compile(
    r"(?:"
    r"[A-ZÑ&]{3}\d{6}[A-Z0-9]{3}"
    r"|"
    r"[A-ZÑ&]{4}\d{6}[A-Z0-9]{3}"
    r")"
)


def normalize_rfc_candidate(
    value: str,
) -> str:
    return re.sub(
        r"[^A-Z0-9Ñ&]",
        "",
        str(value or "")
        .strip()
        .upper(),
    )


def validate_rfc_format(
    value: str,
) -> tuple[bool, str]:
    rfc = normalize_rfc_candidate(
        value
    )

    if len(rfc) not in {12, 13}:
        return (
            False,
            "El RFC debe tener 12 o 13 "
            "caracteres.",
        )

    if not RFC_EXACT_PATTERN.fullmatch(
        rfc
    ):
        return (
            False,
            "El RFC contiene letras o "
            "números en posiciones "
            "incorrectas.",
        )

    date_start = (
        3
        if len(rfc) == 12
        else 4
    )

    date_text = rfc[
        date_start:
        date_start + 6
    ]

    try:
        datetime.strptime(
            date_text,
            "%y%m%d",
        )

    except ValueError:
        return (
            False,
            "La fecha contenida en el RFC "
            "no es válida.",
        )

    return True, ""


def extract_rfc_like_candidates(
    text: str,
) -> list[str]:
    raw = str(
        text or ""
    ).upper()

    # Detecta tokens que claramente parecen
    # intentos de RFC aunque les falte o sobre
    # algún carácter.
    tokens = re.findall(
        r"(?<![A-Z0-9Ñ&])"
        r"[A-Z0-9Ñ&]{10,15}"
        r"(?![A-Z0-9Ñ&])",
        raw,
    )

    output: list[str] = []
    seen: set[str] = set()

    for token in tokens:
        candidate = (
            normalize_rfc_candidate(
                token
            )
        )

        first_digit = next(
            (
                index
                for index, char
                in enumerate(candidate)
                if char.isdigit()
            ),
            -1,
        )

        # Un RFC comienza normalmente con
        # 3 o 4 letras. Permitimos 2–5 para
        # detectar errores de una posición.
        if not 2 <= first_digit <= 5:
            continue

        prefix = candidate[
            :first_digit
        ]

        if not re.fullmatch(
            r"[A-ZÑ&]{2,5}",
            prefix,
        ):
            continue

        # Debe contener algo parecido a la
        # fecha YYMMDD.
        if not re.search(
            r"\d{5,7}",
            candidate,
        ):
            continue

        if candidate in seen:
            continue

        seen.add(candidate)
        output.append(candidate)

    return output
