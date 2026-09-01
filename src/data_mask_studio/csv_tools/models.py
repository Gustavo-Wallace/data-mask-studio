from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CSVHeaderReplacement:
    position: int
    synthetic_name: str


@dataclass(slots=True)
class CSVInspectionResult:
    """Informações identificadas no cabeçalho de um arquivo CSV."""

    path: Path
    encoding: str
    delimiter: str
    headers: list[str]
    header_replacements: tuple[CSVHeaderReplacement, ...] = ()
