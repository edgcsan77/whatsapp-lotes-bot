import os
import re
import shutil
import tempfile
from datetime import datetime
from typing import Any

import requests


NUEVO_LEON_CURP_URL = (
    "https://us-central1-os-gobierno-de-nuevo-leon."
    "cloudfunctions.net/nuevoLeon-checkCurp"
)

RFC_PATTERN = re.compile(
    r"^[A-ZÑ&]{4}\d{6}[A-Z0-9]{3}$"
)

CURP_PATTERN = re.compile(
    r"^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$"
)


class CurpRfcError(Exception):
    """Error consultando una CURP o calculando su RFC."""


def normalize_text(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip().upper()


def normalize_birthdate(value: Any) -> str:
    raw = str(value or "").strip()

    for date_format in (
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(
                raw[:10],
                date_format,
            ).strftime("%Y-%m-%d")
        except ValueError:
            continue

    raise CurpRfcError(
        f"CURP_BIRTHDATE_INVALID:{raw}"
    )


def consultar_curp_nuevo_leon(
    curp: str,
    timeout_seconds: int = 20,
) -> dict[str, str]:
    normalized_curp = normalize_text(curp)

    if not CURP_PATTERN.fullmatch(
        normalized_curp
    ):
        raise CurpRfcError("CURP_INVALIDA")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "Content-Type":
            "application/json; charset=utf-8",
        "Accept": "application/json",
    }

    try:
        response = requests.post(
            NUEVO_LEON_CURP_URL,
            json={
                "curp": normalized_curp,
            },
            headers=headers,
            timeout=timeout_seconds,
        )
    except requests.Timeout as error:
        raise CurpRfcError(
            "NL_CURP_TIMEOUT"
        ) from error
    except requests.RequestException as error:
        raise CurpRfcError(
            "NL_CURP_REQUEST_ERROR:"
            f"{type(error).__name__}"
        ) from error

    if response.status_code != 200:
        response_preview = (
            response.text or ""
        ).strip()[:300]

        raise CurpRfcError(
            "NL_CURP_HTTP_ERROR:"
            f"{response.status_code}:"
            f"{response_preview}"
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise CurpRfcError(
            "NL_CURP_INVALID_JSON"
        ) from error

    if not isinstance(payload, dict):
        raise CurpRfcError(
            "NL_CURP_INVALID_RESPONSE"
        )

    returned_curp = normalize_text(
        payload.get("curp")
    )

    same_curp = (
        returned_curp == normalized_curp
    )

    corrected_last_two = (
        len(returned_curp) == 18
        and len(normalized_curp) == 18
        and returned_curp[:16]
        == normalized_curp[:16]
    )

    if not (
        same_curp
        or corrected_last_two
    ):
        raise CurpRfcError(
            "NL_CURP_RESPONSE_MISMATCH:"
            f"{normalized_curp}:"
            f"{returned_curp}"
        )

    data = {
        "CURP": returned_curp,
        "NOMBRE": normalize_text(
            payload.get("nombres")
            or payload.get("nombre")
        ),
        "PRIMER_APELLIDO": normalize_text(
            payload.get("apePat")
            or payload.get("apellidoPaterno")
            or payload.get("primerApellido")
            or payload.get("apellido_paterno")
        ),
        "SEGUNDO_APELLIDO": normalize_text(
            payload.get("apeMat")
            or payload.get("apellidoMaterno")
            or payload.get("segundoApellido")
            or payload.get("apellido_materno")
        ),
        "FECHA_NACIMIENTO": normalize_text(
            payload.get("fechaNac")
            or payload.get("fechaNacimiento")
            or payload.get("fecha_nacimiento")
        ),
    }

    if (
        not data["NOMBRE"]
        or not (
            data["PRIMER_APELLIDO"]
            or data["SEGUNDO_APELLIDO"]
        )
        or not data["FECHA_NACIMIENTO"]
    ):
        raise CurpRfcError(
            "NL_CURP_DATA_INCOMPLETE"
        )

    if (
        not data["PRIMER_APELLIDO"]
        and data["SEGUNDO_APELLIDO"]
    ):
        data["PRIMER_APELLIDO"] = (
            data["SEGUNDO_APELLIDO"]
        )
        data["SEGUNDO_APELLIDO"] = ""

    return data


def calcular_rfc_moffin(
    nombre: str,
    apellido_paterno: str,
    apellido_materno: str,
    fecha_nacimiento: str,
    timeout_seconds: int = 30,
) -> str:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait

    normalized_name = normalize_text(nombre)
    normalized_lastname = normalize_text(
        apellido_paterno
    )
    normalized_second_lastname = normalize_text(
        apellido_materno
    )
    birthdate_iso = normalize_birthdate(
        fecha_nacimiento
    )

    if (
        not normalized_name
        or not normalized_lastname
    ):
        raise CurpRfcError(
            "MOFFIN_DATA_INCOMPLETE"
        )

    chrome_binary = os.environ.get(
        "CHROME_BIN",
        "",
    ).strip()

    chromedriver_binary = os.environ.get(
        "CHROMEDRIVER_BIN",
        "",
    ).strip()

    profile_base = os.environ.get(
        "CHROME_PROFILE_BASE",
        "/tmp",
    ).strip() or "/tmp"

    os.makedirs(
        profile_base,
        mode=0o700,
        exist_ok=True,
    )

    profile_dir = tempfile.mkdtemp(
        prefix="curp-rfc-",
        dir=profile_base,
    )

    options = webdriver.ChromeOptions()

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument(
        "--disable-dev-shm-usage"
    )
    options.add_argument("--disable-gpu")
    options.add_argument(
        "--disable-software-rasterizer"
    )
    options.add_argument(
        "--remote-debugging-port=0"
    )
    options.add_argument(
        f"--user-data-dir={profile_dir}"
    )
    options.add_argument(
        "--window-size=1440,1200"
    )
    options.add_argument("--lang=es-MX")

    if chrome_binary:
        options.binary_location = (
            chrome_binary
        )

    driver = None

    try:
        if chromedriver_binary:
            service = Service(
                executable_path=
                    chromedriver_binary
            )

            driver = webdriver.Chrome(
                service=service,
                options=options,
            )
        else:
            # Sin driver explícito:
            # Selenium Manager selecciona
            # el ChromeDriver compatible.
            driver = webdriver.Chrome(
                options=options,
            )

        driver.set_page_load_timeout(
            timeout_seconds
        )
        driver.set_script_timeout(
            timeout_seconds
        )

        wait = WebDriverWait(
            driver,
            timeout_seconds,
        )

        driver.get(
            "https://moffin.com/calcular_rfc"
        )

        wait.until(
            lambda browser:
                browser.execute_script(
                    "return document.readyState"
                ) == "complete"
        )

        # Cerrar el aviso de cookies si aparece.
        consent_buttons = driver.find_elements(
            By.CSS_SELECTOR,
            'button[data-cta="consent-accept"]',
        )

        for consent_button in consent_buttons:
            if consent_button.is_displayed():
                driver.execute_script(
                    "arguments[0].click();",
                    consent_button,
                )
                break

        def find_visible_input(
            selectors: list[str],
        ):
            for selector in selectors:
                elements = driver.find_elements(
                    By.CSS_SELECTOR,
                    selector,
                )

                for element in elements:
                    if element.is_displayed():
                        return element

            return None

        name_input = find_visible_input([
            "#nombre",
            'input[name="nombre"]',
        ])

        paternal_input = find_visible_input([
            "#apellidoPaterno",
            'input[name="apellidoPaterno"]',
        ])

        maternal_input = find_visible_input([
            "#apellidoMaterno",
            'input[name="apellidoMaterno"]',
        ])

        date_input = find_visible_input([
            "#fecha",
            'input[type="date"]',
        ])

        if (
            name_input is None
            or paternal_input is None
            or maternal_input is None
            or date_input is None
        ):
            raise CurpRfcError(
                "MOFFIN_INPUTS_NOT_FOUND"
            )

        def set_react_value(
            element,
            value: str,
        ) -> None:
            driver.execute_script(
                """
                const element = arguments[0];
                const value = arguments[1];

                const prototype =
                    element.tagName === 'TEXTAREA'
                    ? window.HTMLTextAreaElement.prototype
                    : window.HTMLInputElement.prototype;

                const descriptor =
                    Object.getOwnPropertyDescriptor(
                        prototype,
                        'value'
                    );

                if (!descriptor || !descriptor.set) {
                    throw new Error(
                        'NATIVE_VALUE_SETTER_NOT_FOUND'
                    );
                }

                descriptor.set.call(
                    element,
                    value
                );

                element.dispatchEvent(
                    new InputEvent(
                        'input',
                        {
                            bubbles: true,
                            inputType: 'insertText',
                            data: value
                        }
                    )
                );

                element.dispatchEvent(
                    new Event(
                        'change',
                        {bubbles: true}
                    )
                );

                element.dispatchEvent(
                    new FocusEvent(
                        'blur',
                        {bubbles: true}
                    )
                );
                """,
                element,
                value,
            )

        set_react_value(
            name_input,
            normalized_name,
        )

        set_react_value(
            paternal_input,
            normalized_lastname,
        )

        set_react_value(
            maternal_input,
            normalized_second_lastname,
        )

        set_react_value(
            date_input,
            birthdate_iso,
        )

        buttons = driver.find_elements(
            By.CSS_SELECTOR,
            'button[data-cta="Calcular RFC"]',
        )

        if not buttons:
            buttons = driver.find_elements(
                By.XPATH,
                (
                    "//button"
                    "[contains("
                    "translate("
                    "normalize-space(.),"
                    "'abcdefghijklmnopqrstuvwxyz',"
                    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'"
                    "),"
                    "'CALCULAR RFC'"
                    ")]"
                ),
            )

        button = next(
            (
                candidate
                for candidate in buttons
                if candidate.is_displayed()
                and candidate.is_enabled()
            ),
            None,
        )

        if button is None:
            raise CurpRfcError(
                "MOFFIN_BUTTON_NOT_FOUND"
            )

        driver.execute_script(
            "arguments[0].click();",
            button,
        )

        def read_rfc(browser):
            elements = browser.find_elements(
                By.XPATH,
                (
                    "//*[self::h1 or self::h2 "
                    "or self::h3 or self::h4 "
                    "or self::h5 or self::h6 "
                    "or self::p or self::div "
                    "or self::span]"
                ),
            )

            for element in elements:
                try:
                    if not element.is_displayed():
                        continue

                    matches = re.findall(
                        (
                            r"[A-ZÑ&]{4}"
                            r"\d{6}"
                            r"[A-Z0-9]{3}"
                        ),
                        (
                            element.text
                            or ""
                        ).upper(),
                    )

                    for match in matches:
                        if RFC_PATTERN.fullmatch(
                            match
                        ):
                            return match
                except Exception:
                    continue

            return False

        rfc = wait.until(read_rfc)

        if not RFC_PATTERN.fullmatch(
            str(rfc or "")
        ):
            raise CurpRfcError(
                f"MOFFIN_RFC_INVALID:{rfc}"
            )

        return str(rfc).upper()

    except CurpRfcError:
        raise
    except Exception as error:
        raise CurpRfcError(
            "MOFFIN_ERROR:"
            f"{type(error).__name__}:"
            f"{error}"
        ) from error
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

        shutil.rmtree(
            profile_dir,
            ignore_errors=True,
        )


def convert_curp_to_rfc(
    curp: str,
) -> tuple[str, dict[str, str]]:
    normalized_curp = normalize_text(curp)

    data = consultar_curp_nuevo_leon(
        normalized_curp
    )

    rfc = calcular_rfc_moffin(
        data["NOMBRE"],
        data["PRIMER_APELLIDO"],
        data["SEGUNDO_APELLIDO"],
        data["FECHA_NACIMIENTO"],
    )

    if rfc[4:10] != normalized_curp[4:10]:
        raise CurpRfcError(
            "RFC_CURP_DATE_MISMATCH:"
            f"{normalized_curp}:"
            f"{rfc}"
        )

    return rfc, data
