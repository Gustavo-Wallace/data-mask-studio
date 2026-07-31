from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class CellClassification(StrEnum):
    EMPTY = "empty"
    FOUND = "found"
    NOT_FOUND = "not_found"
    INVALID_CODE_LIKE = "invalid_code_like"
    COMMON = "common"


class MissingCodePolicy(StrEnum):
    KEEP = "keep"
    EMPTY = "empty"
    ABORT = "abort"


class RepresentationPolicy(StrEnum):
    FIRST_ORIGINAL = "first_original"
    CANONICAL = "canonical"


class RestorationStage(StrEnum):
    ANALYZING = "analyzing"
    RESTORING = "restoring"


@dataclass(frozen=True, slots=True)
class SelectedColumn:
    index: int
    header: str


@dataclass(frozen=True, slots=True)
class RestorationConfiguration:
    source_path: Path
    encoding: str
    delimiter: str
    headers: tuple[str, ...]
    selected_columns: tuple[SelectedColumn, ...]
    missing_code_policy: MissingCodePolicy = MissingCodePolicy.KEEP
    representation_policy: RepresentationPolicy = RepresentationPolicy.FIRST_ORIGINAL


@dataclass(frozen=True, slots=True)
class ClassifiedCell:
    classification: CellClassification
    lookup_code: str | None = field(default=None, repr=False)
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    rows_processed: int
    cells_analyzed: int
    valid_codes: int
    found_codes: int
    missing_codes: int
    invalid_formats: int
    empty_cells: int
    common_values: int
    prefixes: tuple[str, ...]
    possible_incompatibilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RestorationProgress:
    stage: RestorationStage
    rows_processed: int
    restored_codes: int = 0
    missing_codes: int = 0
    preserved_values: int = 0
    errors: int = 0


@dataclass(frozen=True, slots=True)
class RestorationResult:
    output_path: Path
    rows_processed: int
    restored_codes: int
    missing_codes: int
    preserved_common_values: int
    empty_cells: int
    duration_seconds: float
    missing_code_policy: MissingCodePolicy
    representation_policy: RepresentationPolicy


@dataclass(frozen=True, slots=True)
class SafeCellError:
    header: str
    row_number: int
    problem_type: str


class CancellationRequest:
    def __init__(self) -> None:
        from threading import Event

        self._event = Event()

    def request(self) -> None:
        self._event.set()

    def is_requested(self) -> bool:
        return self._event.is_set()
