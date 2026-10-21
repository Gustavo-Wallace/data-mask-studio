from data_mask_studio.integrity.models import AuditReport, STATUS_LABELS as INTEGRITY_LABELS
from data_mask_studio.maintenance.models import DiagnosticResult, STATUS_LABELS
from data_mask_studio.metadata import application_version


def safe_diagnostic_report(result: DiagnosticResult) -> str:
    stats = result.statistics
    distribution = ", ".join(
        f"{rule}={count}" for rule, count in stats.normalization_distribution
    ) or "nenhuma"
    lines = [
        "Relatório técnico seguro do Data Mask Studio",
        f"Versão da aplicação: {application_version()}",
        f"Estado geral: {STATUS_LABELS[result.status]}",
        f"Versão do esquema: {stats.schema_version if stats.schema_version is not None else 'indisponível'}",
        f"Mapeamentos: {stats.mapping_count}",
        f"Variações: {stats.variation_count}",
        f"Ocorrências: {stats.total_occurrences}",
        f"Perfis: {stats.profile_count}",
        f"Prefixos: {stats.prefix_count}",
        f"Regras de normalização: {distribution}",
        f"Tamanho do cofre: {stats.vault_size} bytes",
        f"Tamanho do ambiente: {stats.environment_size} bytes",
        f"Espaço livre: {stats.free_space} bytes",
        f"Auxiliares SQLite: WAL={'sim' if stats.wal_present else 'não'}, "
        f"SHM={'sim' if stats.shm_present else 'não'}, "
        f"journal={'sim' if stats.journal_present else 'não'}",
        "",
        *result.statistics.errors,
        safe_integrity_details(result.audit),
    ]
    return "\n".join(lines)


def safe_integrity_details(report: AuditReport) -> str:
    """Present the auditor's safe results without local filesystem paths."""
    lines = [
        f"Integridade: {INTEGRITY_LABELS[report.status]}",
        f"Início da auditoria: {report.started_at.astimezone().isoformat(timespec='seconds')}",
        f"Fim da auditoria: {report.finished_at.astimezone().isoformat(timespec='seconds')}",
        "Verificações (contagens por verificação, não registros únicos):",
    ]
    for check in report.checks:
        line = (
            f"[{INTEGRITY_LABELS[check.status]}] {check.check_type}: "
            f"examinados={check.examined}; falhas={check.failures}; {check.message}"
        )
        if check.identifiers:
            line += f"; referências={', '.join(check.identifiers)}"
        lines.append(line)
    return "\n".join(lines)
