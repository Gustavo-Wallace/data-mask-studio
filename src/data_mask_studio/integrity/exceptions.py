class IntegrityError(Exception):
    """Falha segura durante a auditoria local."""


class IntegrityCancelled(IntegrityError):
    """Cancelamento cooperativo da auditoria."""
