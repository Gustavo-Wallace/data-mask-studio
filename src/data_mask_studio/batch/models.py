from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Event

from data_mask_studio.anonymization.models import NormalizationFallback


class BatchFileStatus(StrEnum):
    PENDING = "pending"
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    PROCESSING = "processing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    ERROR = "error"


STATUS_LABELS: dict[BatchFileStatus, str] = {
    BatchFileStatus.PENDING: "Aguardando validação",
    BatchFileStatus.COMPATIBLE: "Compatível",
    BatchFileStatus.INCOMPATIBLE: "Incompatível",
    BatchFileStatus.PROCESSING: "Processando",
    BatchFileStatus.COMPLETED: "Concluído",
    BatchFileStatus.SKIPPED: "Ignorado",
    BatchFileStatus.CANCELLED: "Cancelado",
    BatchFileStatus.ERROR: "Erro",
}


class BatchErrorType(StrEnum):
    FILE = "file"
    STRUCTURAL = "structural"
    CANCELLATION = "cancellation"


@dataclass(slots=True)
class BatchFile:
    path: Path
    status: BatchFileStatus = BatchFileStatus.PENDING
    column_count: int | None = None
    encoding: str | None = None
    delimiter: str | None = None
    headers: tuple[str, ...] = ()
    missing_headers: tuple[str, ...] = ()
    result_message: str = ""
    output_path: Path | None = None
    records_processed: int = 0
    new_mappings: int = 0
    updated_mappings: int = 0
    normalization_fallbacks: tuple[NormalizationFallback, ...] = ()
    error_type: BatchErrorType | None = None


@dataclass(frozen=True, slots=True)
class BatchProgress:
    current_file: int
    compatible_files: int
    file_name: str
    records_processed: int
    completed_files: int
    error_files: int


@dataclass(frozen=True, slots=True)
class BatchFileResult:
    path: Path
    status: BatchFileStatus
    output_path: Path | None = None
    message: str = ""
    records_processed: int = 0


@dataclass(frozen=True, slots=True)
class BatchSummary:
    selected_files: int
    compatible_files: int
    completed_files: int
    incompatible_files: int
    error_files: int
    cancelled_or_skipped_files: int
    records_processed: int
    new_mappings: int
    updated_mappings: int
    duration_seconds: float
    output_directory: Path
    normalization_fallbacks: tuple[NormalizationFallback, ...] = ()
    results: tuple[BatchFileResult, ...] = field(default_factory=tuple, repr=False)


class CancellationRequest:
    def __init__(self) -> None:
        self._event = Event()

    def request(self) -> None:
        self._event.set()

    def is_requested(self) -> bool:
        return self._event.is_set()
