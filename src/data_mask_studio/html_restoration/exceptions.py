class HTMLRestorationError(RuntimeError):
    """Falha esperada sem exposição de conteúdo sensível."""


class HTMLRestorationSecurityError(HTMLRestorationError):
    """Falha de integridade ou descriptografia do cofre."""


class HTMLRestorationCancelled(HTMLRestorationError):
    """Operação interrompida cooperativamente."""


class HTMLMissingCodeError(HTMLRestorationError):
    """Código ausente sob política de interrupção."""
