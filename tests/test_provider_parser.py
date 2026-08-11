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


def test_provider_accepts_idcif_on_next_line() -> None:
    text = """
MAAF901118T20
15040018668
"""

    parsed = parse_provider_message(text)

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAAF901118T20"
    assert parsed[0].idcif == "15040018668"
    assert parsed[0].result_code == "OK"


def test_provider_accepts_no_id_on_next_line() -> None:
    text = """
MAMB760905IN2
no id
"""

    parsed = parse_provider_message(text)

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAMB760905IN2"
    assert parsed[0].idcif is None
    assert parsed[0].result_code == "SIN_ID"


def test_provider_mixed_two_line_results() -> None:
    text = """
MAMB760905IN2
no id
MAAF901118T20
15040018668
MASJ9304174U4
no id
LOVH800510RY8
16110100840
"""

    parsed = parse_provider_message(text)

    assert len(parsed) == 4

    assert parsed[0].rfc == "MAMB760905IN2"
    assert parsed[0].result_code == "SIN_ID"

    assert parsed[1].rfc == "MAAF901118T20"
    assert parsed[1].idcif == "15040018668"
    assert parsed[1].result_code == "OK"

    assert parsed[2].rfc == "MASJ9304174U4"
    assert parsed[2].result_code == "SIN_ID"

    assert parsed[3].rfc == "LOVH800510RY8"
    assert parsed[3].idcif == "16110100840"
    assert parsed[3].result_code == "OK"


def test_provider_accepts_rfc_idcif_same_line() -> None:
    parsed = parse_provider_message(
        "MAAF901118T20 15040018668"
    )

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAAF901118T20"
    assert parsed[0].idcif == "15040018668"
    assert parsed[0].result_code == "OK"


def test_provider_accepts_idcif_rfc_same_line() -> None:
    parsed = parse_provider_message(
        "15040018668 MAAF901118T20"
    )

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAAF901118T20"
    assert parsed[0].idcif == "15040018668"
    assert parsed[0].result_code == "OK"


def test_provider_accepts_rfc_then_idcif() -> None:
    parsed = parse_provider_message(
        """
MAAF901118T20
15040018668
"""
    )

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAAF901118T20"
    assert parsed[0].idcif == "15040018668"
    assert parsed[0].result_code == "OK"


def test_provider_accepts_idcif_then_rfc() -> None:
    parsed = parse_provider_message(
        """
15040018668
MAAF901118T20
"""
    )

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAAF901118T20"
    assert parsed[0].idcif == "15040018668"
    assert parsed[0].result_code == "OK"


def test_provider_accepts_all_four_formats_together() -> None:
    text = """
AAAA800101AAA 11111111111
22222222222 BBBB800101BBB
CCCC800101CCC
33333333333
44444444444
DDDD800101DDD
"""

    parsed = parse_provider_message(
        text
    )

    assert len(parsed) == 4

    assert parsed[0].rfc == (
        "AAAA800101AAA"
    )
    assert parsed[0].idcif == (
        "11111111111"
    )

    assert parsed[1].rfc == (
        "BBBB800101BBB"
    )
    assert parsed[1].idcif == (
        "22222222222"
    )

    assert parsed[2].rfc == (
        "CCCC800101CCC"
    )
    assert parsed[2].idcif == (
        "33333333333"
    )

    assert parsed[3].rfc == (
        "DDDD800101DDD"
    )
    assert parsed[3].idcif == (
        "44444444444"
    )


def test_provider_accepts_labeled_rfc_idcif_same_line() -> None:
    parsed = parse_provider_message(
        "RFC: MAAF901118T20 IDCIF: 15040018668"
    )

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAAF901118T20"
    assert parsed[0].idcif == "15040018668"
    assert parsed[0].result_code == "OK"


def test_provider_accepts_labeled_idcif_rfc_same_line() -> None:
    parsed = parse_provider_message(
        "IDCIF: 15040018668 RFC: MAAF901118T20"
    )

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAAF901118T20"
    assert parsed[0].idcif == "15040018668"
    assert parsed[0].result_code == "OK"


def test_provider_accepts_labeled_rfc_then_idcif() -> None:
    parsed = parse_provider_message(
        """
RFC: MAAF901118T20
IDCIF: 15040018668
"""
    )

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAAF901118T20"
    assert parsed[0].idcif == "15040018668"
    assert parsed[0].result_code == "OK"


def test_provider_accepts_labeled_idcif_then_rfc() -> None:
    parsed = parse_provider_message(
        """
IDCIF: 15040018668
RFC: MAAF901118T20
"""
    )

    assert len(parsed) == 1
    assert parsed[0].rfc == "MAAF901118T20"
    assert parsed[0].idcif == "15040018668"
    assert parsed[0].result_code == "OK"
