from data_mask_studio.maintenance.compaction_messages import CompactionExplanation


class MaintenanceError(RuntimeError):
    """Falha segura durante diagnóstico ou manutenção."""

    def __init__(self, *args: object, explanation: CompactionExplanation | None = None) -> None:
        super().__init__(*args)
        self.explanation = explanation


class MaintenanceCancelled(MaintenanceError):
    """Operação cancelada antes da fase irreversível."""
