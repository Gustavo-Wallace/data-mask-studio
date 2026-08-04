from dataclasses import dataclass
from enum import StrEnum

from data_mask_studio.normalization import NormalizationRule


class SuggestedType(StrEnum):
    DATETIME = "datetime"
    CPF = "cpf"
    CNPJ = "cnpj"
    EMAIL = "email"
    PHONE = "phone"
    IP_ADDRESS = "ip_address"
    NAME = "name"
    GENERIC_ID = "generic_id"
    COMMON_TEXT = "common_text"
    UNKNOWN = "unknown"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNDEFINED = "undefined"


TYPE_LABELS: dict[SuggestedType, str] = {
    SuggestedType.DATETIME: "Data/Hora",
    SuggestedType.CPF: "CPF",
    SuggestedType.CNPJ: "CNPJ",
    SuggestedType.EMAIL: "E-mail",
    SuggestedType.PHONE: "Telefone",
    SuggestedType.IP_ADDRESS: "Endereço IP",
    SuggestedType.NAME: "Nome",
    SuggestedType.GENERIC_ID: "Identificador genérico",
    SuggestedType.COMMON_TEXT: "Texto comum",
    SuggestedType.UNKNOWN: "Desconhecido",
}

CONFIDENCE_LABELS: dict[ConfidenceLevel, str] = {
    ConfidenceLevel.HIGH: "Alta",
    ConfidenceLevel.MEDIUM: "Média",
    ConfidenceLevel.LOW: "Baixa",
    ConfidenceLevel.UNDEFINED: "Indefinida",
}


@dataclass(frozen=True, slots=True)
class ColumnSuggestion:
    header: str
    suggested_type: SuggestedType
    anonymize: bool
    prefix: str
    normalization_rule: NormalizationRule
    confidence: ConfidenceLevel
    reason: str
    sampled_values: int
    compatible_values: int


@dataclass(frozen=True, slots=True)
class DetectionResult:
    suggestions: tuple[ColumnSuggestion, ...]
    rows_analyzed: int
    row_limit: int
