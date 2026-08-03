class BatchRestorationError(RuntimeError):
    """Erro seguro e compreensível da restauração em lote."""


class BatchRestorationStructuralError(BatchRestorationError):
    """Falha estrutural que impede continuar usando o ambiente local."""
