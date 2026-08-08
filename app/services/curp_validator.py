import re
from datetime import datetime


CURP_STATE_CODES = {
    "AS",  # Aguascalientes
    "BC",  # Baja California
    "BS",  # Baja California Sur
    "CC",  # Campeche
    "CL",  # Coahuila
    "CM",  # Colima
    "CS",  # Chiapas
    "CH",  # Chihuahua
    "DF",  # Ciudad de México / histórico
    "DG",  # Durango
    "GT",  # Guanajuato
    "GR",  # Guerrero
    "HG",  # Hidalgo
    "JC",  # Jalisco
    "MC",  # México
    "MN",  # Michoacán
    "MS",  # Morelos
    "NT",  # Nayarit
    "NL",  # Nuevo León
    "OC",  # Oaxaca
    "PL",  # Puebla
    "QT",  # Querétaro
    "QR",  # Quintana Roo
    "SP",  # San Luis Potosí
    "SL",  # Sinaloa
    "SR",  # Sonora
    "TC",  # Tabasco
    "TS",  # Tamaulipas
    "TL",  # Tlaxcala
    "VZ",  # Veracruz
    "YN",  # Yucatán
    "ZS",  # Zacatecas
    "NE",  # Nacido en el extranjero
}


CURP_CHECK_DICTIONARY = (
    "0123456789"
    "ABCDEFGHIJKLMNÑ"
    "OPQRSTUVWXYZ"
)


def normalize_curp_candidate(
    value: str,
) -> str:
    return re.sub(
        r"[^A-Z0-9Ñ]",
        "",
        str(value or "").strip().upper(),
    )


def calculate_curp_check_digit(
    curp17: str,
) -> int | None:
    value = str(
        curp17 or ""
    ).strip().upper()

    if len(value) != 17:
        return None

    total = 0

    for index, char in enumerate(value):
        position = (
            CURP_CHECK_DICTIONARY.find(char)
        )

        if position < 0:
            return None

        total += (
            position
            * (18 - index)
        )

    return (
        10 - (total % 10)
    ) % 10


def validate_curp_format(
    value: str,
) -> tuple[bool, str]:
    curp = normalize_curp_candidate(
        value
    )

    if len(curp) != 18:
        return (
            False,
            "La CURP debe tener exactamente "
            "18 caracteres.",
        )

    if not re.fullmatch(
        r"[A-ZÑ][AEIOUX][A-ZÑ]{2}"
        r"\d{6}"
        r"[HM]"
        r"[A-Z]{2}"
        r"[B-DF-HJ-NP-TV-ZÑ]{3}"
        r"[A-Z0-9]"
        r"\d",
        curp,
    ):
        return (
            False,
            "La CURP contiene letras o números "
            "en posiciones incorrectas.",
        )

    date_text = curp[4:10]

    try:
        datetime.strptime(
            date_text,
            "%y%m%d",
        )
    except ValueError:
        return (
            False,
            "La fecha contenida en la CURP "
            "no es válida.",
        )

    sex = curp[10]

    if sex not in {"H", "M"}:
        return (
            False,
            "El sexo de la CURP debe ser "
            "H o M.",
        )

    state_code = curp[11:13]

    if state_code not in CURP_STATE_CODES:
        return (
            False,
            "La clave de entidad federativa "
            "de la CURP no es válida.",
        )

    expected_digit = (
        calculate_curp_check_digit(
            curp[:17]
        )
    )

    if (
        expected_digit is None
        or str(expected_digit)
        != curp[17]
    ):
        return (
            False,
            "El dígito verificador de la CURP "
            "no es válido.",
        )

    return True, ""


def extract_curp_like_candidates(
    text: str,
) -> list[str]:
    raw = str(
        text or ""
    ).upper()

    # Buscamos tokens que parecen intención
    # de CURP aunque estén mal escritos.
    tokens = re.findall(
        r"(?<![A-Z0-9Ñ])"
        r"[A-Z0-9Ñ]{16,20}"
        r"(?![A-Z0-9Ñ])",
        raw,
    )

    result: list[str] = []
    seen: set[str] = set()

    for token in tokens:
        candidate = (
            normalize_curp_candidate(
                token
            )
        )

        # Debe parecer una CURP:
        # bastante larga y contener dígitos.
        if not (
            16 <= len(candidate) <= 20
            and any(
                char.isdigit()
                for char in candidate
            )
        ):
            continue

        if candidate in seen:
            continue

        seen.add(candidate)
        result.append(candidate)

    return result
