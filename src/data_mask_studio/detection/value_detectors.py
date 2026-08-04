import ipaddress
import re
from datetime import datetime

from data_mask_studio.detection.models import SuggestedType

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_GENERIC_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{1,63}$")
_BRAZILIAN_DATETIME_PATTERN = re.compile(
    r"^\d{2}/\d{2}/\d{4}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?$"
)
_ISO_DATETIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?$"
)
_BRAZILIAN_DATETIME_FORMATS = (
    "%d/%m/%Y",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%YT%H:%M",
    "%d/%m/%YT%H:%M:%S",
)


def digits(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)


def matches_type(value: str, suggested_type: SuggestedType) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    numeric = digits(stripped)
    if suggested_type is SuggestedType.DATETIME:
        return matches_datetime(stripped)
    if suggested_type is SuggestedType.CPF:
        return len(numeric) == 11
    if suggested_type is SuggestedType.CNPJ:
        return len(numeric) == 14
    if suggested_type is SuggestedType.EMAIL:
        return _EMAIL_PATTERN.fullmatch(stripped) is not None
    if suggested_type is SuggestedType.PHONE:
        return (
            not matches_datetime(stripped)
            and not ("/" in stripped and ":" in stripped)
            and _ISO_DATETIME_PATTERN.fullmatch(stripped) is None
            and 8 <= len(numeric) <= 15
            and not re.search(r"[A-Za-z]", stripped)
        )
    if suggested_type is SuggestedType.IP_ADDRESS:
        try:
            ipaddress.ip_address(stripped)
        except ValueError:
            return False
        return True
    if suggested_type is SuggestedType.GENERIC_ID:
        return (
            _GENERIC_ID_PATTERN.fullmatch(stripped) is not None
            and any(character.isdigit() for character in stripped)
        )
    return False


def matches_datetime(value: str) -> bool:
    """Valida formatos deliberadamente limitados usando regras reais de calendário."""
    stripped = value.strip()
    if _BRAZILIAN_DATETIME_PATTERN.fullmatch(stripped):
        for date_format in _BRAZILIAN_DATETIME_FORMATS:
            try:
                datetime.strptime(stripped, date_format)
            except ValueError:
                continue
            return True
        return False
    if _ISO_DATETIME_PATTERN.fullmatch(stripped):
        try:
            datetime.fromisoformat(
                stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
            )
        except ValueError:
            return False
        return True
    return False
