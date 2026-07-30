class BatchError(RuntimeError):
    """Falha segura relacionada à preparação ou execução de um lote."""


class BatchStructuralError(BatchError):
    """Falha que impede o uso seguro dos recursos compartilhados do lote."""
