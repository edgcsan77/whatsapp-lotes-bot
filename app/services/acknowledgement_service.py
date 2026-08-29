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


def _build_request_acknowledgement_legacy(
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


def build_request_acknowledgement(
    identifiers,
) -> str:
    values = list(
        identifiers
    )

    regular_identifiers: list[str] = []
    generic_identifiers: list[str] = []

    seen: set[str] = set()

    for raw_value in values:
        identifier = str(
            raw_value or ""
        ).strip().upper()

        if (
            not identifier
            or identifier in seen
        ):
            continue

        seen.add(identifier)

        if identifier.endswith("-G"):
            generic_identifiers.append(
                identifier
            )

        else:
            regular_identifiers.append(
                identifier
            )

    if not generic_identifiers:
        return (
            _build_request_acknowledgement_legacy(
                regular_identifiers
            )
        )

    generic_count = len(
        generic_identifiers
    )

    generic_label = (
        "RFC genérico"
        if generic_count == 1
        else "RFC genéricos"
    )

    generic_summary = (
        f"🧾 {generic_count} "
        f"{generic_label}\n"
        "Estado: pendiente de generación"
    )

    if regular_identifiers:
        regular_text = (
            _build_request_acknowledgement_legacy(
                regular_identifiers
            )
        )

        return (
            f"{regular_text}\n\n"
            f"{generic_summary}"
        )

    return (
        "✅ Solicitud recibida\n\n"
        f"{generic_summary}"
    )
