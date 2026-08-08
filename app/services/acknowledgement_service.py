from collections.abc import Iterable


def unique_preserving_order(
    values: Iterable[str],
) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []

    for value in values:
        normalized = str(value or "").strip().upper()

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        output.append(normalized)

    return output


def build_request_acknowledgement(
    identifiers: Iterable[str],
) -> str:
    normalized = unique_preserving_order(
        identifiers
    )

    if not normalized:
        return ""

    if len(normalized) == 1:
        identifier_block = normalized[0]
        label = (
            "CURP"
            if len(normalized[0]) == 18
            else "RFC"
        )

        return (
            "✅ Solicitud recibida\n\n"
            f"{label}: {identifier_block}\n"
            "Estado: pendiente de envío"
        )

    lines = "\n".join(
        f"• {identifier}"
        for identifier in normalized
    )

    return (
        "✅ Solicitudes recibidas\n\n"
        f"{lines}\n\n"
        f"Total: {len(normalized)}\n"
        "Estado: pendientes de envío"
    )
