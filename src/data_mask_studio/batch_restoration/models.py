from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from data_mask_studio.restoration import RepresentationPolicy


class BatchRestorationFileType(StrEnum):
    CSV = "csv"
    HTML = "html"


class BatchRestorationStatus(StrEnum):
    PENDING = "pending"
    COMPATIBLE = "compatible"
    REVIEW_REQUIRED = "review_required"
    INCOMPATIBLE = "incompatible"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class BatchMissingCodePolicy(StrEnum):
    KEEP = "keep"
    ABORT_FILE = "abort_file"
    ABORT_BATCH = "abort_batch"


STATUS_LABELS = {
    BatchRestorationStatus.PENDING: "Aguardando análise",
    BatchRestorationStatus.COMPATIBLE: "Compatível",
    BatchRestorationStatus.REVIEW_REQUIRED: "Requer revisão",
    BatchRestorationStatus.INCOMPATIBLE: "Incompatível",
    BatchRestorationStatus.PROCESSING: "Processando",
    BatchRestorationStatus.COMPLETED: "Concluído",
    BatchRestorationStatus.ERROR: "Erro",
    BatchRestorationStatus.CANCELLED: "Cancelado",
    BatchRestorationStatus.SKIPPED: "Ignorado",
}


@dataclass(slots=True)
class BatchCSVColumn:
    index: int
    header: str
    valid_codes: int
    found_codes: int
    missing_codes: int
    selected: bool = False

    @property
    def is_candidate(self) -> bool:
        return self.valid_codes > 0


@dataclass(slots=True)
class BatchRestorationFile:
    path: Path
    file_type: BatchRestorationFileType
    status: BatchRestorationStatus = BatchRestorationStatus.PENDING
    encoding: str = ""
    delimiter: str | None = None
    headers: tuple[str, ...] = ()
    columns: list[BatchCSVColumn] = field(default_factory=list)
    codes_found: int = 0
    codes_in_vault: int = 0
    missing_codes: int = 0
    result_message: str = ""
    output_path: Path | None = None


@dataclass(frozen=True, slots=True)
class BatchRestorationOptions:
    representation_policy: RepresentationPolicy = RepresentationPolicy.FIRST_ORIGINAL
    missing_code_policy: BatchMissingCodePolicy = BatchMissingCodePolicy.KEEP


@dataclass(frozen=True, slots=True)
class BatchRestorationProgress:
    current_file: int
    total_files: int
    completed_files: int
    error_files: int
    file_name: str
    current_value: int = 0
    current_total: int = 0


@dataclass(frozen=True, slots=True)
class BatchRestorationSummary:
    selected_files: int
    completed_files: int
    error_files: int
    skipped_files: int
    cancelled_files: int
    restored_occurrences: int
    missing_occurrences: int
    output_directory: Path
    duration_seconds: float
    cancelled: bool = False
