from __future__ import annotations

import re

TERMINAL_CODES = {
    "IDCIF_INVALID",
    "RFC_DADO_DE_BAJA",
    "RFC_CANCELADO",
    "RFC_INACTIVO",
    "RFC_SIN_ESTATUS",
}

REASON_LABELS = {
    "IDCIF_INVALID": "ID INCORRECTO",
    "RFC_DADO_DE_BAJA": "RFC DADO DE BAJA",
    "RFC_CANCELADO": "RFC CANCELADO",
    "RFC_INACTIVO": "RFC INACTIVO",
    "RFC_SIN_ESTATUS": "RFC SIN ESTATUS",
}


def normalize_code(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def is_terminal_code(value: str | None) -> bool:
    return normalize_code(value) in TERMINAL_CODES


def terminal_code_from_error_text(value: str | None) -> str | None:
    text = normalize_code(value)
    for code in TERMINAL_CODES:
        if code in text:
            return code
    return None


def reason_label(value: str | None) -> str:
    return REASON_LABELS.get(normalize_code(value), "NO FUE POSIBLE VALIDAR")


def build_idcif_failure_message(*, rfc: str, idcif: str, code: str) -> str:
    return (
        "❌❌\n"
        f"{str(rfc or '').strip().upper()} "
        f"{str(idcif or '').strip()}\n\n"
        "NO SE PUEDE GENERAR CONSTANCIA\n"
        f"MOTIVO: {reason_label(code)}"
    )


def build_temporary_failure_message(*, rfc: str, idcif: str) -> str:
    return (
        "⚠️\n"
        f"{str(rfc or '').strip().upper()} "
        f"{str(idcif or '').strip()}\n\n"
        "NO FUE POSIBLE VALIDAR EN SAT EN ESTE MOMENTO.\n"
        "INTENTA NUEVAMENTE MÁS TARDE."
    )
