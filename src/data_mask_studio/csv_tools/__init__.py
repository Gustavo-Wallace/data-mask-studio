"""Ferramentas para inspeção de arquivos CSV."""

from data_mask_studio.csv_tools.csv_inspector import CSVInspectionError, inspect_csv
from data_mask_studio.csv_tools.header_resolver import (
    format_header_replacement_warning,
    resolve_empty_headers,
)
from data_mask_studio.csv_tools.models import CSVHeaderReplacement, CSVInspectionResult


def __getattr__(name: str):
    # Source metadata is used by processing.models. Loading that metadata must
    # not eagerly import the executor, which depends on those processing types.
    # Keep the existing package API, loading only its execution exports on demand.
    if name in {"CSVAnonymizationError", "ProcessingCancelled", "anonymize_csv"}:
        from data_mask_studio.csv_tools import csv_anonymizer

        return getattr(csv_anonymizer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CSVAnonymizationError",
    "CSVInspectionError",
    "CSVHeaderReplacement",
    "CSVInspectionResult",
    "ProcessingCancelled",
    "anonymize_csv",
    "format_header_replacement_warning",
    "inspect_csv",
    "resolve_empty_headers",
]
