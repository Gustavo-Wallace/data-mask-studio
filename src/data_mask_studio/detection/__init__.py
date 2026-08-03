"""Detecção local e assistida de colunas potencialmente sensíveis."""

from data_mask_studio.detection.analyzer import analyze_csv_columns
from data_mask_studio.detection.exceptions import DetectionCancelled, DetectionError
from data_mask_studio.detection.models import (
    ColumnSuggestion,
    ConfidenceLevel,
    DetectionResult,
    SuggestedType,
)

__all__ = [
    "ColumnSuggestion",
    "ConfidenceLevel",
    "DetectionCancelled",
    "DetectionError",
    "DetectionResult",
    "SuggestedType",
    "analyze_csv_columns",
]
