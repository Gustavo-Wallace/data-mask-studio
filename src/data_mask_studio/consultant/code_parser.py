import re

CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,23}-[A-Z2-7]{12}$")
CODE_SEPARATOR_PATTERN = re.compile(r"[\r\n,;]+")


def parse_codes(raw_text: str) -> list[str]:
    """Normaliza, separa e remove códigos repetidos preservando a ordem."""
    codes: list[str] = []
    seen: set[str] = set()
    for part in CODE_SEPARATOR_PATTERN.split(raw_text):
        code = part.strip().upper()
        if code and code not in seen:
            codes.append(code)
            seen.add(code)
    return codes


def is_valid_code(code: str) -> bool:
    """Valida um código completo, já normalizado."""
    return CODE_PATTERN.fullmatch(code) is not None

