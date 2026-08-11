from app.services.message_parser import (
    extract_curps,
    extract_name,
    extract_rfcs,
    parse_client_message,
)


def test_single_curp() -> None:
    text = "BEEJ760109HSLRSL03"

    parsed = parse_client_message(text)

    assert len(parsed) == 1
    assert parsed[0].identifier_type == "CURP"
    assert parsed[0].curp == "BEEJ760109HSLRSL03"
    assert parsed[0].rfc is None


def test_curp_and_name() -> None:
    text = """
    Excelente día team 👋🏼, Xio, me apoyas con la siguiente constancia por fa:

    *LOPV040803HCSPRCA2
    *VICTOR LIZANDRO LOPEZ PEREZ

    Gracias
    """

    parsed = parse_client_message(text)

    assert len(parsed) == 1
    assert parsed[0].curp == "LOPV040803HCSPRCA2"
    assert parsed[0].detected_name == "VICTOR LIZANDRO LOPEZ PEREZ"


def test_name_before_curp() -> None:
    text = """
    LIZBETH JAQUELINE TOVAR FLORES
    TOFL980825MJCVLZ04
    """

    parsed = parse_client_message(text)

    assert len(parsed) == 1
    assert parsed[0].curp == "TOFL980825MJCVLZ04"
    assert parsed[0].detected_name == "LIZBETH JAQUELINE TOVAR FLORES"


def test_rfc_and_curp_are_both_parsed() -> None:
    text = """
    LIZBETH JAQUELINE TOVAR FLORES
    TOFL980825MJCVLZ04
    TOFL980825ABC
    """

    parsed = parse_client_message(text)

    assert len(parsed) == 2

    assert parsed[0].identifier_type == "RFC"
    assert parsed[0].identifier == "TOFL980825ABC"
    assert parsed[0].rfc == "TOFL980825ABC"
    assert parsed[0].curp is None

    assert parsed[1].identifier_type == "CURP"
    assert parsed[1].identifier == "TOFL980825MJCVLZ04"
    assert parsed[1].rfc is None
    assert parsed[1].curp == "TOFL980825MJCVLZ04"

    assert parsed[0].ignored_curps == ()
    assert parsed[1].ignored_curps == ()

def test_multiple_rfcs() -> None:
    text = """
    Vala830403ra8
    RAHC850707NW3
    """

    assert extract_rfcs(text) == [
        "VALA830403RA8",
        "RAHC850707NW3",
    ]


def test_duplicate_identifiers_are_deduplicated() -> None:
    text = """
    BEEJ760109HSLRSL03
    BEEJ760109HSLRSL03
    """

    assert extract_curps(text) == [
        "BEEJ760109HSLRSL03",
    ]


def test_no_identifier() -> None:
    assert parse_client_message("Hola, muchas gracias") == []


def test_extract_name_ignores_greetings() -> None:
    text = """
    Excelente día team
    Xio, me apoyas con una constancia por fa
    CELINA ISABEL FUENTES DOMINGUEZ
    FUDC961114MTCNML08
    Gracias
    """

    assert extract_name(text) == "CELINA ISABEL FUENTES DOMINGUEZ"
