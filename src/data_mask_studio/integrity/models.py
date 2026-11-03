from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class IntegrityStatus(StrEnum):
    INTACT = "intact"
    ATTENTION = "attention"
    FAILURE = "failure"


STATUS_LABELS = {
    IntegrityStatus.INTACT: "ÍNTEGRO",
    IntegrityStatus.ATTENTION: "ATENÇÃO",
    IntegrityStatus.FAILURE: "FALHA",
}


class IntegrityCheckKind(StrEnum):
    HMAC_KEY = "hmac_key"
    AES_KEY = "aes_key"
    SQLITE = "sqlite"
    SCHEMA = "schema"
    AES_AUTHENTICATION = "aes_authentication"
    VARIATION_COMPATIBILITY = "variation_compatibility"
    OCCURRENCE_COUNTERS = "occurrence_counters"
    TABLE_REFERENCES = "table_references"
    CODE_FORMAT = "code_format"
    TOKEN_RECOMPUTATION = "token_recomputation"
    PROFILES = "profiles"
    TEMPORARIES = "temporaries"
    COMPOSITE_MAPPINGS = "composite_mappings"
    COMPOSITE_VARIATIONS = "composite_variations"
    COMPOSITE_TOKENS = "composite_tokens"
    COMPOSITE_COUNTERS = "composite_counters"
    COMPOSITE_REFERENCES = "composite_references"
    COMPOSITE_UNIQUENESS = "composite_uniqueness"


class IntegrityReason(StrEnum):
    KEY_MISSING = "key_missing"
    KEY_INACCESSIBLE = "key_inaccessible"
    KEY_VALID = "key_valid"
    SQLITE_VALID = "sqlite_valid"
    SQLITE_INCONSISTENT = "sqlite_inconsistent"
    SCHEMA_INVALID = "schema_invalid"
    SCHEMA_VALID = "schema_valid"
    INCONSISTENCIES = "inconsistencies"
    NO_INCONSISTENCIES = "no_inconsistencies"
    COMPOSITE_INCONSISTENCIES = "composite_inconsistencies"
    PROFILES_INVALID = "profiles_invalid"
    PROFILES_VALID = "profiles_valid"
    TEMPORARIES_FOUND = "temporaries_found"
    NO_TEMPORARIES = "no_temporaries"
    DATABASE_MISSING = "database_missing"
    DATABASE_UNAVAILABLE = "database_unavailable"
    DATABASE_FAILED = "database_failed"
    MIGRATION_PENDING = "migration_pending"


@dataclass(frozen=True, slots=True)
class IntegrityExplanation:
    """Presentation only; legacy fields remain authoritative for reports/decisions."""

    reason: IntegrityReason
    kind: IntegrityCheckKind | None = None
    count: int | None = None
    schema_version: int | None = None


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_type: str
    status: IntegrityStatus
    examined: int
    failures: int
    message: str
    identifiers: tuple[str, ...] = ()
    explanation: IntegrityExplanation | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class AuditReport:
    started_at: datetime
    finished_at: datetime
    database_path: Path
    profiles_path: Path
    schema_version: int | None
    checks: tuple[CheckResult, ...]

    @property
    def status(self) -> IntegrityStatus:
        statuses = {check.status for check in self.checks}
        if IntegrityStatus.FAILURE in statuses:
            return IntegrityStatus.FAILURE
        if IntegrityStatus.ATTENTION in statuses:
            return IntegrityStatus.ATTENTION
        return IntegrityStatus.INTACT

    @property
    def records_examined(self) -> int:
        return sum(check.examined for check in self.checks)

    @property
    def failure_count(self) -> int:
        return sum(check.failures for check in self.checks)

    def to_safe_text(self) -> str:
        lines = [
            "Relatório de integridade do Data Mask Studio",
            f"Status geral: {STATUS_LABELS[self.status]}",
            f"Início: {self.started_at.astimezone().isoformat(timespec='seconds')}",
            f"Fim: {self.finished_at.astimezone().isoformat(timespec='seconds')}",
            f"Cofre: {self.database_path}",
            f"Perfis: {self.profiles_path}",
            f"Versão do esquema: {self.schema_version if self.schema_version is not None else 'indisponível'}",
            "",
        ]
        for check in self.checks:
            line = (
                f"[{STATUS_LABELS[check.status]}] {check.check_type}: "
                f"examinados={check.examined}; falhas={check.failures}; "
                f"{check.message}"
            )
            if check.identifiers:
                line += f"; referências={', '.join(check.identifiers)}"
            lines.append(line)
        return "\n".join(lines)
