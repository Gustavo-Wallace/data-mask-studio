from collections.abc import Callable

from data_mask_studio.normalization import normalizers
from data_mask_studio.normalization.models import NormalizationRule

Normalizer = Callable[[str], str]

NORMALIZATION_OPTIONS: tuple[tuple[NormalizationRule, str], ...] = (
    (NormalizationRule.EXACT, "Valor exato"),
    (NormalizationRule.DIGITS_ONLY, "Somente dígitos"),
    (NormalizationRule.CPF, "CPF"),
    (NormalizationRule.CNPJ, "CNPJ"),
    (NormalizationRule.PHONE, "Telefone"),
    (NormalizationRule.EMAIL, "E-mail"),
    (NormalizationRule.IP_ADDRESS, "Endereço IP"),
    (NormalizationRule.COLLAPSE_WHITESPACE, "Texto sem espaços extras"),
)

_NORMALIZERS: dict[NormalizationRule, Normalizer] = {
    NormalizationRule.EXACT: normalizers.exact,
    NormalizationRule.DIGITS_ONLY: normalizers.digits_only,
    NormalizationRule.CPF: normalizers.cpf,
    NormalizationRule.CNPJ: normalizers.cnpj,
    NormalizationRule.PHONE: normalizers.phone,
    NormalizationRule.EMAIL: normalizers.email,
    NormalizationRule.IP_ADDRESS: normalizers.ip_address,
    NormalizationRule.COLLAPSE_WHITESPACE: normalizers.collapse_whitespace,
}

_LABELS = dict(NORMALIZATION_OPTIONS)


def normalize_value(value: str, rule: NormalizationRule) -> str:
    """Aplica uma regra; vazios e espaços são sempre preservados."""
    if value == "" or value.isspace():
        return value
    return _NORMALIZERS[rule](value)


def normalization_label(rule: NormalizationRule) -> str:
    return _LABELS[rule]

