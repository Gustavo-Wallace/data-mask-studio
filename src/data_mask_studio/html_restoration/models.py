from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from data_mask_studio.restoration import RepresentationPolicy


class HTMLMissingCodePolicy(StrEnum):
    KEEP = "keep"
    ABORT = "abort"


class HTMLRestorationStage(StrEnum):
    ANALYZING = "analyzing"
    RESTORING = "restoring"


@dataclass(frozen=True, slots=True)
class HTMLInspectionResult:
    path: Path
    encoding: str


@dataclass(frozen=True, slots=True)
class HTMLAnalysisResult:
    unique_codes: int
    total_occurrences: int
    found_codes: int
    missing_codes: int
    invalid_similar_codes: int
    prefixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HTMLRestorationProgress:
    stage: HTMLRestorationStage
    processed_bytes: int
    total_bytes: int
    occurrences_processed: int = 0
    restored_occurrences: int = 0
    missing_occurrences: int = 0


@dataclass(frozen=True, slots=True)
class HTMLRestorationResult:
    output_path: Path
    encoding: str
    total_occurrences: int
    restored_occurrences: int
    missing_occurrences: int
    duration_seconds: float
    missing_code_policy: HTMLMissingCodePolicy
    representation_policy: RepresentationPolicy
