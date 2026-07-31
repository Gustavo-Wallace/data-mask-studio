from dataclasses import dataclass, field

from data_mask_studio.normalization import NormalizationRule


@dataclass(slots=True)
class VariationCandidate:
    original_value: str = field(repr=False)
    normalization_rule: NormalizationRule = NormalizationRule.EXACT
    occurrences: int = 1


@dataclass(slots=True)
class MappingCandidate:
    """Mapeamento pendente, mantido somente durante um pequeno lote."""

    code: str
    prefix: str
    original_value: str = field(repr=False)
    source_header: str
    occurrences: int = 1
    canonical_value: str | None = field(default=None, repr=False)
    normalization_rule: NormalizationRule = NormalizationRule.EXACT
    variations: dict[str, VariationCandidate] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.canonical_value is None:
            self.canonical_value = self.original_value
        if not self.variations:
            self.variations[self.original_value] = VariationCandidate(
                self.original_value,
                self.normalization_rule,
                self.occurrences,
            )

    @property
    def total_occurrences(self) -> int:
        return sum(item.occurrences for item in self.variations.values())

    def add_variation(
        self,
        original_value: str,
        normalization_rule: NormalizationRule,
        occurrences: int = 1,
    ) -> None:
        existing = self.variations.get(original_value)
        if existing is None:
            self.variations[original_value] = VariationCandidate(
                original_value,
                normalization_rule,
                occurrences,
            )
        else:
            existing.occurrences += occurrences


@dataclass(frozen=True, slots=True)
class EncryptedValue:
    ciphertext: bytes
    nonce: bytes


@dataclass(frozen=True, slots=True)
class VaultRecord:
    code: str
    prefix: str
    encrypted_value: bytes
    nonce: bytes
    source_header: str
    first_seen: str
    last_seen: str
    occurrence_count: int
    normalization_rule: NormalizationRule = NormalizationRule.EXACT


@dataclass(frozen=True, slots=True)
class VaultVariationRecord:
    identifier: int
    code: str
    encrypted_value: bytes
    nonce: bytes
    first_seen: str
    last_seen: str
    occurrence_count: int
    normalization_rule: NormalizationRule = NormalizationRule.EXACT


@dataclass(frozen=True, slots=True)
class DecryptedVariation:
    original_value: str = field(repr=False)
    first_seen: str = ""
    last_seen: str = ""
    occurrence_count: int = 0
    normalization_rule: NormalizationRule = NormalizationRule.EXACT


@dataclass(frozen=True, slots=True)
class DecryptedVaultMapping:
    code: str
    prefix: str
    source_header: str
    original_value: str = field(repr=False)
    first_seen: str = ""
    last_seen: str = ""
    occurrence_count: int = 0
    normalization_rule: NormalizationRule = NormalizationRule.EXACT
    variations: tuple[DecryptedVariation, ...] = field(default_factory=tuple, repr=False)
    canonical_value: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class VaultUpdateSummary:
    new_mappings: int = 0
    updated_mappings: int = 0
