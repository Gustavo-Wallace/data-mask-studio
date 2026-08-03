import csv
from collections.abc import Callable, Sequence

from data_mask_studio.anonymization import normalize_prefix
from data_mask_studio.csv_tools import CSVInspectionResult
from data_mask_studio.detection.exceptions import DetectionCancelled, DetectionError
from data_mask_studio.detection.header_rules import detect_header_type
from data_mask_studio.detection.models import (
    ColumnSuggestion,
    ConfidenceLevel,
    DetectionResult,
    SuggestedType,
)
from data_mask_studio.detection.value_detectors import matches_type
from data_mask_studio.normalization import NormalizationRule

DEFAULT_ROW_LIMIT = 100
_VALUE_TYPES = (
    SuggestedType.CPF,
    SuggestedType.CNPJ,
    SuggestedType.EMAIL,
    SuggestedType.IP_ADDRESS,
    SuggestedType.PHONE,
    SuggestedType.GENERIC_ID,
)
_PREFIXES = {
    SuggestedType.CPF: "CPF",
    SuggestedType.CNPJ: "CNPJ",
    SuggestedType.EMAIL: "EMAIL",
    SuggestedType.PHONE: "TELEFONE",
    SuggestedType.IP_ADDRESS: "IP",
    SuggestedType.NAME: "NOME",
    SuggestedType.GENERIC_ID: "ID",
}
_NORMALIZATIONS = {
    SuggestedType.CPF: NormalizationRule.CPF,
    SuggestedType.CNPJ: NormalizationRule.CNPJ,
    SuggestedType.EMAIL: NormalizationRule.EMAIL,
    SuggestedType.PHONE: NormalizationRule.PHONE,
    SuggestedType.IP_ADDRESS: NormalizationRule.IP_ADDRESS,
    SuggestedType.NAME: NormalizationRule.COLLAPSE_WHITESPACE,
    SuggestedType.GENERIC_ID: NormalizationRule.EXACT,
}


def analyze_csv_columns(
    inspection: CSVInspectionResult,
    *,
    row_limit: int = DEFAULT_ROW_LIMIT,
    should_cancel: Callable[[], bool] | None = None,
) -> DetectionResult:
    """Analisa uma amostra limitada sem persistir ou retornar valores do CSV."""
    if row_limit < 1:
        raise ValueError("O limite de linhas deve ser positivo.")
    cancel_requested = should_cancel or (lambda: False)
    samples: list[list[str]] = [[] for _ in inspection.headers]
    rows_analyzed = 0
    python_encoding = (
        "cp1252" if inspection.encoding == "windows-1252" else inspection.encoding
    )
    try:
        with inspection.path.open(
            "r", encoding=python_encoding, newline=""
        ) as csv_file:
            reader = csv.reader(csv_file, delimiter=inspection.delimiter, strict=True)
            next(reader)
            while rows_analyzed < row_limit:
                _raise_if_cancelled(cancel_requested)
                try:
                    row = next(reader)
                except StopIteration:
                    break
                rows_analyzed += 1
                for index in range(min(len(row), len(samples))):
                    if row[index] and not row[index].isspace():
                        samples[index].append(row[index])
    except DetectionCancelled:
        raise
    except (PermissionError, UnicodeDecodeError, csv.Error, OSError, StopIteration) as error:
        raise DetectionError(
            "Não foi possível analisar a amostra do CSV selecionado."
        ) from error

    _raise_if_cancelled(cancel_requested)
    suggestions = tuple(
        _suggest_column(header, values)
        for header, values in zip(inspection.headers, samples, strict=True)
    )
    return DetectionResult(suggestions, rows_analyzed, row_limit)


def _raise_if_cancelled(should_cancel: Callable[[], bool]) -> None:
    if should_cancel():
        raise DetectionCancelled("A análise das colunas foi cancelada.")


def _suggest_column(header: str, values: Sequence[str]) -> ColumnSuggestion:
    header_type = detect_header_type(header)
    ratios = {
        candidate: _match_ratio(values, candidate) for candidate in _VALUE_TYPES
    }
    best_type = max(_VALUE_TYPES, key=lambda candidate: ratios[candidate])
    best_ratio = ratios[best_type]

    if header_type is SuggestedType.COMMON_TEXT:
        if best_ratio >= 0.8 and best_type is not SuggestedType.GENERIC_ID:
            return _build(
                header,
                best_type,
                ConfidenceLevel.LOW,
                values,
                ratios[best_type],
                "O cabeçalho parece comum, mas a amostra possui outro padrão.",
            )
        return _build(
            header,
            SuggestedType.COMMON_TEXT,
            ConfidenceLevel.HIGH,
            values,
            0.0,
            "O cabeçalho indica texto comum sem padrão sensível consistente.",
        )

    if header_type is SuggestedType.NAME:
        confidence = ConfidenceLevel.MEDIUM if values else ConfidenceLevel.LOW
        return _build(
            header,
            SuggestedType.NAME,
            confidence,
            values,
            0.0,
            "O cabeçalho indica uma coluna de nome.",
        )

    if header_type is not None:
        own_ratio = ratios.get(header_type, 0.0)
        if not values:
            confidence = ConfidenceLevel.LOW
            reason = "O cabeçalho sugere o tipo, mas não há valores para confirmar."
        elif (
            best_type is not header_type
            and best_ratio >= 0.8
            and best_ratio > own_ratio
        ):
            confidence = ConfidenceLevel.LOW
            reason = "O cabeçalho e o padrão agregado da amostra são conflitantes."
        elif own_ratio >= 0.8:
            confidence = ConfidenceLevel.HIGH
            reason = "O cabeçalho e a maior parte da amostra indicam o mesmo tipo."
        elif own_ratio >= 0.5:
            confidence = ConfidenceLevel.MEDIUM
            reason = "O cabeçalho é compatível com parte relevante da amostra."
        else:
            confidence = ConfidenceLevel.LOW
            reason = "O cabeçalho sugere o tipo, mas a amostra é inconsistente."
        return _build(header, header_type, confidence, values, own_ratio, reason)

    if best_ratio >= 0.8 and best_type is not SuggestedType.GENERIC_ID:
        return _build(
            header,
            best_type,
            ConfidenceLevel.MEDIUM,
            values,
            best_ratio,
            "A maior parte da amostra possui um padrão estrutural consistente.",
        )
    if best_ratio >= 0.6:
        return _build(
            header,
            best_type,
            ConfidenceLevel.LOW,
            values,
            best_ratio,
            "Parte da amostra possui um possível padrão estrutural.",
        )
    return _build(
        header,
        SuggestedType.UNKNOWN,
        ConfidenceLevel.UNDEFINED,
        values,
        0.0,
        "Não foi identificado um padrão confiável no cabeçalho ou na amostra.",
    )


def _match_ratio(values: Sequence[str], suggested_type: SuggestedType) -> float:
    if not values:
        return 0.0
    return sum(matches_type(value, suggested_type) for value in values) / len(values)


def _build(
    header: str,
    suggested_type: SuggestedType,
    confidence: ConfidenceLevel,
    values: Sequence[str],
    ratio: float,
    reason: str,
) -> ColumnSuggestion:
    anonymize = suggested_type not in {
        SuggestedType.COMMON_TEXT,
        SuggestedType.UNKNOWN,
    }
    prefix = _PREFIXES.get(suggested_type, "")
    if anonymize and not prefix:
        prefix = normalize_prefix(header)
    normalization = _NORMALIZATIONS.get(suggested_type, NormalizationRule.EXACT)
    return ColumnSuggestion(
        header=header,
        suggested_type=suggested_type,
        anonymize=anonymize,
        prefix=prefix,
        normalization_rule=normalization,
        confidence=confidence,
        reason=reason,
        sampled_values=len(values),
        compatible_values=round(len(values) * ratio),
    )
