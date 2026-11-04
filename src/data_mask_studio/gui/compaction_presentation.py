"""Render only explicit compaction metadata; preserve unclassified safe text."""

from data_mask_studio.i18n import Translator
from data_mask_studio.maintenance import CompactionExplanation, CompactionReason, MaintenanceError


def compaction_error_text(error: MaintenanceError, translator: Translator) -> str:
    return compaction_message_text(str(error), getattr(error, "explanation", None), translator)


def compaction_message_text(message: str, explanation: CompactionExplanation | None, translator: Translator) -> str:
    if not isinstance(explanation, CompactionExplanation):
        return message
    reason = explanation.reason
    if isinstance(reason, CompactionReason):
        if reason is CompactionReason.VAULT_MISSING:
            message = translator.text("maintenance.compaction.error.no_vault")
        elif reason is CompactionReason.CANCELLED:
            message = translator.text("restoration.csv.status.cancelled")
        elif reason is CompactionReason.ROLLBACK_FAILED:
            name = explanation.recovery_directory
            prefix = "dms-vault-compaction-"
            # Presentation safety only, never ownership or removal authority.
            if (isinstance(name, str) and name.startswith(prefix) and name[len(prefix):]
                    and all(char in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in name[len(prefix):])):
                message = translator.text("maintenance.compaction.error.rollback_failed", directory=name)
        else:
            message = translator.text("maintenance.compaction.error." + reason.value)
    if explanation.rollback_completed is True:
        message += "\n" + translator.text("maintenance.compaction.recovery.rolled_back")
    return message
