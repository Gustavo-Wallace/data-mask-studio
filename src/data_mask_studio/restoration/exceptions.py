from dataclasses import dataclass
from enum import StrEnum


class RestorationReason(StrEnum):
    """Safe presentation metadata only; never restoration/security decisions."""

    SOURCE_MISSING = "source_missing"
    HEADERS_CHANGED = "headers_changed"
    ROW_COLUMNS = "row_columns"
    ANALYSIS_FAILED = "analysis_failed"
    MISSING_MAPPING = "missing_mapping"
    DESTINATION_ALIAS = "destination_alias"
    DESTINATION_FOLDER_MISSING = "destination_folder_missing"
    DESTINATION_EXISTS = "destination_exists"
    DISK_FULL = "disk_full"
    FILE_REMOVED = "file_removed"
    ACCESS_DENIED = "access_denied"
    IO_FAILED = "io_failed"
    CSV_GENERATION_FAILED = "csv_generation_failed"


@dataclass(frozen=True, slots=True)
class RestorationExplanation:
    reason: RestorationReason
    header: str | None = None
    row_number: int | None = None


class RestorationError(RuntimeError):
    """Falha esperada apresentada sem dados sensiveis."""

    def __init__(self, *args: object, explanation: RestorationExplanation | None = None) -> None:
        super().__init__(*args)
        self.explanation = explanation


class RestorationSecurityError(RestorationError):
    """Falha de integridade ou recuperacao segura do cofre."""


class RestorationCancelled(RestorationError):
    """Operacao interrompida cooperativamente."""


class MissingCodeError(RestorationError):
    """Codigo ausente com politica configurada para interrupcao."""
