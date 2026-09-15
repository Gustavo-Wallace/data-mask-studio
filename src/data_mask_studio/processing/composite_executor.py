"""Execução pura de composites sobre uma linha original, sem binding ou I/O."""
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from data_mask_studio.anonymization.anonymizer import _normalize_with_fallback
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.processing.models import BoundCompositeColumn, ProcessingPlan
from data_mask_studio.vault.composite_models import CompositeMappingCandidate


class CompositeExecutionError(ValueError):
    """Falha fatal da linha, sem valores sensíveis na mensagem."""


@dataclass(frozen=True, slots=True)
class CompositeCellResult:
    composite_id: UUID
    output_index: int
    output_name: str
    value: str = field(repr=False)
    candidate: CompositeMappingCandidate | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class CompositeFallbackCount:
    rule: NormalizationRule
    count: int


@dataclass(frozen=True, slots=True)
class CompositeExecutionResult:
    cells: tuple[CompositeCellResult, ...]
    fallbacks: tuple[CompositeFallbackCount, ...]


def execute_composite_row(
    original_row: Sequence[str], plan: ProcessingPlan, key: bytes,
) -> CompositeExecutionResult:
    """Retorna a linha composta inteira ou falha; caller persiste os candidatos.

    Reutiliza o helper escalar de fallback, inclusive sua regra efetiva EXACT.
    Índices são físicos 0-based; output_index é a posição na projeção do plano.
    """
    if (isinstance(original_row, (str, bytes))
            or not isinstance(original_row, Sequence)
            or len(original_row) != len(plan.physical_columns)
            or not all(isinstance(value, str) for value in original_row)):
        raise CompositeExecutionError("Estrutura da linha incompatível com o plano.")
    cells: list[CompositeCellResult] = []
    counts: Counter[NormalizationRule] = Counter()
    try:
        for output_index, composite in enumerate(plan.outputs):
            if not isinstance(composite, BoundCompositeColumn):
                continue
            original = tuple(original_row[part.input_index] for part in composite.components)
            if all(value == "" or value.isspace() for value in original):
                cells.append(CompositeCellResult(composite.identifier, output_index,
                                                 composite.output_name, "", None))
                continue
            canonical: list[str] = []
            effective_rules: list[NormalizationRule] = []
            for value, part in zip(original, composite.components, strict=True):
                if value == "" or value.isspace():
                    normalized, rule, fallback = "", part.normalization_rule, False
                else:
                    normalized, rule, fallback = _normalize_with_fallback(value, part.normalization_rule)
                canonical.append(normalized)
                effective_rules.append(rule)
                if fallback:
                    counts[part.normalization_rule] += 1
            values = tuple(canonical)
            token = generate_composite_token(key, composite.prefix, values)
            candidate = CompositeMappingCandidate(
                code=token, prefix=composite.prefix, canonical_values=values,
                original_values=original, normalization_rules=tuple(effective_rules),
            )
            cells.append(CompositeCellResult(composite.identifier, output_index,
                                             composite.output_name, token, candidate))
    except Exception:
        # Erros inesperados não viram fallback e não expõem a linha via traceback.
        raise CompositeExecutionError("Não foi possível executar as composites da linha.") from None
    return CompositeExecutionResult(tuple(cells), tuple(
        CompositeFallbackCount(rule, count) for rule, count in counts.items()
    ))
