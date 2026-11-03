"""Translate typed audit presentation only; canonical report fields stay literal."""

from data_mask_studio.i18n import Translator
from data_mask_studio.integrity import CheckResult, IntegrityCheckKind, IntegrityExplanation, IntegrityReason
from data_mask_studio.maintenance.models import EnvironmentStatistics, StatisticsReason


def integrity_check_presentation(check: CheckResult, translator: Translator) -> tuple[str, str]:
    name, message = check.check_type, check.message
    explanation = check.explanation
    if not isinstance(explanation, IntegrityExplanation):
        return name, message
    if isinstance(explanation.kind, IntegrityCheckKind):
        name = translator.text("maintenance.integrity.kind." + explanation.kind.value)
    if not isinstance(explanation.reason, IntegrityReason):
        return name, message
    identifier = "maintenance.integrity.reason." + explanation.reason.value
    if explanation.reason is IntegrityReason.PROFILES_VALID:
        count = explanation.count
        if type(count) is not int or count < 0 or count != check.examined:
            return name, message
        return name, translator.plural(identifier, count)
    if explanation.reason is IntegrityReason.SCHEMA_VALID:
        version = explanation.schema_version
        if type(version) is not int or version < 0:
            return name, message
        return name, translator.text(identifier, version=version)
    return name, translator.text(identifier)


def statistics_error_messages(stats: EnvironmentStatistics, translator: Translator) -> tuple[str, ...]:
    reasons = stats.error_reasons if isinstance(stats.error_reasons, tuple) else ()
    return tuple(
        translator.text("maintenance.overview.statistics." + reasons[index].value)
        if index < len(reasons) and isinstance(reasons[index], StatisticsReason) else message
        for index, message in enumerate(stats.errors)
    )
