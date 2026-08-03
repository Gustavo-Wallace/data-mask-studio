from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_type: str
    status: IntegrityStatus
    examined: int
    failures: int
    message: str
    identifiers: tuple[str, ...] = ()


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
