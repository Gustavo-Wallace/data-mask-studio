class VaultError(RuntimeError):
    """Falha segura e esperada ao acessar o cofre local."""


class VaultEncryptionError(VaultError):
    """Falha de autenticação ou descriptografia de um mapeamento."""


class VaultCollisionError(VaultError):
    """Um código já está associado a outro valor original."""

