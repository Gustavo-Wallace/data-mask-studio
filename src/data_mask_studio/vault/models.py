from dataclasses import dataclass, field


@dataclass(slots=True)
class MappingCandidate:
    """Mapeamento pendente, mantido somente durante um pequeno lote."""

    code: str
    prefix: str
    original_value: str = field(repr=False)
    source_header: str
    occurrences: int = 1


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


@dataclass(frozen=True, slots=True)
class DecryptedVaultMapping:
    code: str
    prefix: str
    source_header: str
    original_value: str = field(repr=False)
    first_seen: str = ""
    last_seen: str = ""
    occurrence_count: int = 0


@dataclass(frozen=True, slots=True)
class VaultUpdateSummary:
    new_mappings: int = 0
    updated_mappings: int = 0
