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
        "Verificações:",
    ]
    for check in result.audit.checks:
        lines.append(
            f"- {check.check_type}: {check.status.value}; "
            f"examinados={check.examined}; falhas={check.failures}; {check.message}"
        )
    return "\n".join(lines)
