class BackupError(RuntimeError):
    """Erro seguro e compreensível relacionado a backup ou restauração."""


class BackupValidationError(BackupError):
    """O arquivo não pôde ser autenticado e validado."""


class BackupCompatibilityError(BackupError):
    """O formato ou conteúdo é incompatível com esta aplicação."""


class BackupCancelled(BackupError):
    """A operação foi cancelada cooperativamente."""


VALIDATION_FAILURE_MESSAGE = (
    "Não foi possível validar o backup. Verifique a senha e a integridade do arquivo."
)
