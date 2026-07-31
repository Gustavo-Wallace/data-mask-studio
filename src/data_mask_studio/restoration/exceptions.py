class RestorationError(RuntimeError):
    """Falha esperada apresentada sem dados sensiveis."""


class RestorationSecurityError(RestorationError):
    """Falha de integridade ou recuperacao segura do cofre."""


class RestorationCancelled(RestorationError):
    """Operacao interrompida cooperativamente."""


class MissingCodeError(RestorationError):
    """Codigo ausente com politica configurada para interrupcao."""
