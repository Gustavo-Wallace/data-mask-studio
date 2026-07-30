import re
import unicodedata

PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,23}$")
MAX_PREFIX_LENGTH = 24


def normalize_prefix(header: str) -> str:
    """Gera uma sugestão de prefixo a partir de um cabeçalho."""
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", header)
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^A-Z0-9]+", "_", without_accents.upper())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized[:MAX_PREFIX_LENGTH].rstrip("_")


def validate_prefix(prefix: str) -> str | None:
    """Retorna uma mensagem de erro ou ``None`` para um prefixo válido."""
    if not prefix:
        return "Informe um prefixo."
    if len(prefix) < 2:
        return "O prefixo deve ter ao menos 2 caracteres."
    if len(prefix) > MAX_PREFIX_LENGTH:
        return "O prefixo deve ter no máximo 24 caracteres."
    if not prefix[0].isalpha() or not prefix[0].isascii():
        return "O prefixo deve começar por uma letra de A a Z."
    if PREFIX_PATTERN.fullmatch(prefix) is None:
        return "Use somente letras maiúsculas, números e _."
    return None

