from collections.abc import Iterable


def unique_preserving_order(
    values: Iterable[str],
) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []

    for value in values:
        normalized = str(
            value or ""
        ).strip().upper()

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

    curp_count = sum(
        1
        for identifier in normalized
        if len(identifier) == 18
    )

    rfc_count = sum(
        1
        for identifier in normalized
        if len(identifier) != 18
    )

    parts: list[str] = []

    if curp_count:
        parts.append(
            f"{curp_count} CURP"
        )

    if rfc_count:
        parts.append(
            f"{rfc_count} RFC"
        )

    summary = " y ".join(parts)

    return (
        "✅ Solicitud recibida\n\n"
        f"{summary}\n"
        "Estado: pendiente de envío"
    )
