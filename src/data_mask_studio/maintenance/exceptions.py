class MaintenanceError(RuntimeError):
    """Falha segura durante diagnóstico ou manutenção."""


class MaintenanceCancelled(MaintenanceError):
    """Operação cancelada antes da fase irreversível."""
