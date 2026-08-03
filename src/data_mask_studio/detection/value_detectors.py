import ipaddress
import re

from data_mask_studio.detection.models import SuggestedType

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_GENERIC_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{1,63}$")


def digits(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)


def matches_type(value: str, suggested_type: SuggestedType) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    numeric = digits(stripped)
    if suggested_type is SuggestedType.CPF:
        return len(numeric) == 11
    if suggested_type is SuggestedType.CNPJ:
        return len(numeric) == 14
    if suggested_type is SuggestedType.EMAIL:
        return _EMAIL_PATTERN.fullmatch(stripped) is not None
    if suggested_type is SuggestedType.PHONE:
        return 8 <= len(numeric) <= 15 and not re.search(r"[A-Za-z]", stripped)
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
