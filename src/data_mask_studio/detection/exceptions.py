class DetectionError(Exception):
    """Erro seguro e esperado durante a análise assistida."""


class DetectionCancelled(DetectionError):
    """Interrupção cooperativa solicitada pelo usuário."""
