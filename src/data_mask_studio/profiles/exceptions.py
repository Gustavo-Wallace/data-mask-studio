class ProfileError(Exception):
    """Erro compreensível relacionado aos perfis de configuração."""


class ProfileValidationError(ProfileError):
    """Um perfil ou nome não atende às regras do formato."""


class ProfileStorageError(ProfileError):
    """O arquivo de perfis não pôde ser lido ou gravado com segurança."""


class ProfileFormatError(ProfileStorageError):
    """O arquivo existe, mas não contém um documento válido e suportado."""
