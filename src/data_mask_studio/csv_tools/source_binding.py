"""Referências de origem, sem valores de células ou nomes de saída.

Índices físicos e ocorrências são 0-based. Somente a adaptação dos metadados
CSVHeaderReplacement converte suas posições 1-based. Duplicatas usam ordem
de ocorrência, não uma identidade semântica inferida a partir dos dados.
"""

from dataclasses import dataclass
import re

from data_mask_studio.csv_tools.models import CSVInspectionResult


class SourceBindingError(ValueError):
    """Referência não pode ser associada inequivocamente à estrutura atual."""


def may_be_synthetic_name(header: str) -> bool:
    """Legacy names in the generated namespace do not prove real provenance."""
    return re.fullmatch(r"column_[0-9]+(?:_[0-9]+)?", header) is not None


@dataclass(frozen=True, slots=True)
class SourceColumnRef:
    """Identidade de origem; a estrutura completa é exigida para sintéticos."""

    header: str
    original_index: int | None = None
    is_synthetic: bool = False
    occurrence: int | None = None
    structure: tuple[tuple[str, bool], ...] | None = None


def _structure(inspection: CSVInspectionResult) -> tuple[tuple[str, bool], ...]:
    synthetic_indexes: set[int] = set()
    for replacement in inspection.header_replacements:
        index = replacement.position - 1
        if (
            index < 0
            or index >= len(inspection.headers)
            or index in synthetic_indexes
            or inspection.headers[index] != replacement.synthetic_name
        ):
            raise SourceBindingError("Metadados de proveniência incompatíveis com os cabeçalhos.")
        synthetic_indexes.add(index)
    return tuple(
        (header, index in synthetic_indexes)
        for index, header in enumerate(inspection.headers)
    )


def source_ref_at(inspection: CSVInspectionResult, index: int) -> SourceColumnRef:
    """Constrói referência explícita a uma coluna física inspecionada (0-based)."""
    if type(index) is not int or not 0 <= index < len(inspection.headers):
        raise SourceBindingError("Índice físico de origem inválido.")
    structure = _structure(inspection)
    header, synthetic = structure[index]
    matches = [i for i, entry in enumerate(structure) if entry == (header, False)]
    return SourceColumnRef(
        header=header,
        original_index=index,
        is_synthetic=synthetic,
        occurrence=matches.index(index) if not synthetic and len(matches) > 1 else None,
        structure=structure if synthetic else None,
    )


def bind_source(reference: SourceColumnRef, inspection: CSVInspectionResult) -> int:
    """Retorna índice físico atual (0-based) ou SourceBindingError.

    Reais únicos seguem o nome exato, independentemente do índice original.
    Reais duplicados exigem ocorrência explícita. Sintéticos exigem posição,
    nome e estrutura/proveniência idênticos aos da inspeção de origem.
    """
    if reference.original_index is not None and (
        type(reference.original_index) is not int or reference.original_index < 0
    ):
        raise SourceBindingError("Índice físico de origem inválido.")
    if reference.occurrence is not None and (
        type(reference.occurrence) is not int or reference.occurrence < 0
    ):
        raise SourceBindingError("Ocorrência de origem inválida.")
    structure = _structure(inspection)
    if reference.is_synthetic:
        index = reference.original_index
        if index is None or index >= len(structure):
            raise SourceBindingError("Posição obrigatória da fonte sintética incompatível.")
        if reference.occurrence is not None:
            raise SourceBindingError("Fonte sintética não admite ocorrência de header real.")
        if structure[index] != (reference.header, True):
            raise SourceBindingError("Posição ou proveniência da fonte sintética incompatível.")
        if reference.structure != structure:
            raise SourceBindingError("Estrutura da fonte sintética incompatível.")
        return index

    matches = [
        index for index, entry in enumerate(structure)
        if entry == (reference.header, False)
    ]
    if not matches:
        if (reference.header, True) in structure:
            raise SourceBindingError("Proveniência incompatível: a fonte esperada é real.")
        raise SourceBindingError(f"Fonte inexistente: {reference.header}.")
    if reference.occurrence is None:
        if len(matches) != 1:
            raise SourceBindingError(f"Fonte ambígua: {reference.header}; informe a ocorrência.")
        return matches[0]
    if reference.occurrence >= len(matches):
        raise SourceBindingError(f"Ocorrência indisponível para a fonte: {reference.header}.")
    return matches[reference.occurrence]
