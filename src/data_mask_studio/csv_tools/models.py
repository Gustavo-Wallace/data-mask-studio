from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CSVInspectionResult:
    """Informações identificadas no cabeçalho de um arquivo CSV."""

    path: Path
    encoding: str
    delimiter: str
    headers: list[str]

