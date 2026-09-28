from dataclasses import dataclass, field

from data_mask_studio.vault.models import DecryptedVaultMapping
from data_mask_studio.vault.composite_models import CompositeMapping

FORMAT_NAME = "Data Mask Studio Transfer Package"
FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class MaskedFileBinding:
    sha256: str = field(repr=False)
    size: int


@dataclass(frozen=True, slots=True)
class ScalarRestorationMapping:
    code: str
    original_value: str = field(repr=False)
    canonical_value: str = field(repr=False)

    @classmethod
    def from_vault(cls, mapping: DecryptedVaultMapping) -> "ScalarRestorationMapping":
        return cls(mapping.code, mapping.original_value, mapping.canonical_value)


@dataclass(frozen=True, slots=True)
class CompositeRestorationMapping:
    code: str
    identity_version: int
    canonical_values: tuple[str, ...] = field(repr=False)
    # None means multiple original tuples: current restoration uses canonical.
    original_values: tuple[str, ...] | None = field(repr=False)

    @classmethod
    def from_vault(cls, mapping: CompositeMapping) -> "CompositeRestorationMapping":
        if not mapping.variations:
            raise PackageError("Mapeamento composto sem representações originais.")
        return cls(mapping.code, mapping.identity_version, mapping.canonical_values,
                   mapping.variations[0].original_values if len(mapping.variations) == 1 else None)


@dataclass(frozen=True, slots=True)
class TransferPayload:
    application_version: str
    masked_file: MaskedFileBinding = field(repr=False)
    scalar_mappings: tuple[ScalarRestorationMapping, ...] = field(default=(), repr=False)
    composite_mappings: tuple[CompositeRestorationMapping, ...] = field(default=(), repr=False)
    format_version: int = FORMAT_VERSION
    format_name: str = FORMAT_NAME


class PackageError(RuntimeError):
    """Safe message; never includes passwords, paths or decrypted mappings."""
