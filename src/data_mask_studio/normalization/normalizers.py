import ipaddress
import re
import unicodedata

from data_mask_studio.normalization.exceptions import NormalizationError


def exact(value: str) -> str:
    return value


def digits_only(value: str) -> str:
    normalized = re.sub(r"[^0-9]", "", value)
    if not normalized:
        raise NormalizationError("O valor não contém dígitos.")
    return normalized


def cpf(value: str) -> str:
    normalized = re.sub(r"[^0-9]", "", value)
    if len(normalized) != 11:
        raise NormalizationError("O CPF deve conter 11 dígitos.")
    return normalized


def cnpj(value: str) -> str:
    normalized = re.sub(r"[^0-9]", "", value)
    if len(normalized) != 14:
        raise NormalizationError("O CNPJ deve conter 14 dígitos.")
    return normalized


def phone(value: str) -> str:
    normalized = re.sub(r"[^0-9]", "", value)
    if not 8 <= len(normalized) <= 15:
        raise NormalizationError("O telefone deve conter entre 8 e 15 dígitos.")
    return normalized


def email(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.strip()).lower()
    if normalized.count("@") != 1:
        raise NormalizationError("O e-mail deve conter exatamente um @.")
    local_part, domain = normalized.split("@")
    if not local_part or not domain:
        raise NormalizationError("O e-mail deve ter conteúdo antes e depois do @.")
    return normalized


def ip_address(value: str) -> str:
    try:
        return ipaddress.ip_address(value.strip()).compressed.lower()
    except ValueError:
        raise NormalizationError("O endereço IP é inválido.") from None


def collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())
