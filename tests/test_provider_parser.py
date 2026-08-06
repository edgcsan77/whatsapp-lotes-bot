from app.services.provider_parser import parse_provider_message


def test_provider_example_one() -> None:
    text = """
    VALA830403RA8
    RAHC850707NW3
    MECA7305107Y3 22110055618
    MOGR7808026RA 22070520180
    """

    parsed = parse_provider_message(text)

    assert [item.rfc for item in parsed] == [
        "VALA830403RA8",
        "RAHC850707NW3",
        "MECA7305107Y3",
        "MOGR7808026RA",
    ]

    assert parsed[0].result_code == "RFC_ONLY"
    assert parsed[2].result_code == "OK"
    assert parsed[2].idcif == "22110055618"


def test_provider_header_and_sr() -> None:
    text = """
    ISAI
    RIMJ690404M93 22030675007
    MARV640501KS4 SR
    AUAE860213EP6
    """

    parsed = parse_provider_message(text)

    assert len(parsed) == 3
    assert parsed[0].rfc == "RIMJ690404M93"
    assert parsed[0].idcif == "22030675007"
    assert parsed[1].result_code == "SIN_RESULTADO"
    assert parsed[2].result_code == "RFC_ONLY"


def test_provider_accepts_tabs() -> None:
    parsed = parse_provider_message(
        "GAFB730409AM1\t16030010154"
    )

    assert len(parsed) == 1
    assert parsed[0].rfc == "GAFB730409AM1"
    assert parsed[0].idcif == "16030010154"


def test_provider_deduplicates_rfc() -> None:
    text = """
    RIMJ690404M93 22030675007
    RIMJ690404M93 22030675007
    """

    parsed = parse_provider_message(text)

    assert len(parsed) == 1
