from dataclasses import dataclass, field

from data_mask_studio.normalization import NormalizationRule


@dataclass(frozen=True, slots=True)
class CompositeMappingCandidate:
    code: str
    prefix: str
    canonical_values: tuple[str, ...] = field(repr=False)
    original_values: tuple[str, ...] = field(repr=False)
    normalization_rules: tuple[NormalizationRule, ...]
    occurrences: int = 1
    identity_version: int = 1
    payload_version: int = 1


@dataclass(frozen=True, slots=True)
class CompositeVariation:
    identifier: str
    code: str
    original_values: tuple[str, ...] = field(repr=False)
    first_seen: str
    last_seen: str
    occurrence_count: int


@dataclass(frozen=True, slots=True)
class CompositeMapping:
    code: str
    prefix: str
    identity_version: int
    payload_version: int
    component_count: int
    normalization_rules: tuple[NormalizationRule, ...]
    canonical_values: tuple[str, ...] = field(repr=False)
    variations: tuple[CompositeVariation, ...] = field(repr=False)
    first_seen: str
    last_seen: str
    occurrence_count: int
