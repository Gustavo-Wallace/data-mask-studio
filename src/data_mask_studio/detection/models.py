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


class DetectionReason(StrEnum):
    """Presentation evidence only; never detection or processing decisions."""

    DATETIME_HEADER_NO_SAMPLE = "datetime_header_no_sample"
    DATETIME_HEADER_STRONG = "datetime_header_strong"
    DATETIME_HEADER_PARTIAL = "datetime_header_partial"
    DATETIME_HEADER_INCONSISTENT = "datetime_header_inconsistent"
    DATETIME_SAMPLE = "datetime_sample"
    COMMON_HEADER_CONFLICT = "common_header_conflict"
    COMMON_TEXT_HEADER = "common_text_header"
    NAME_HEADER = "name_header"
    HEADER_NO_SAMPLE = "header_no_sample"
    HEADER_CONFLICT = "header_conflict"
    HEADER_STRONG = "header_strong"
    HEADER_PARTIAL = "header_partial"
    HEADER_INCONSISTENT = "header_inconsistent"
    SAMPLE_STRONG = "sample_strong"
    SAMPLE_PARTIAL = "sample_partial"
    UNRECOGNIZED_PATTERN = "unrecognized_pattern"


@dataclass(frozen=True, slots=True)
class DetectionExplanation:
    reason: DetectionReason


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
    explanation: DetectionExplanation | None = None


@dataclass(frozen=True, slots=True)
class DetectionResult:
    suggestions: tuple[ColumnSuggestion, ...]
    rows_analyzed: int
    row_limit: int
