import base64
import hashlib
import hmac

TOKEN_CODE_LENGTH = 12


class TokenGenerator:
    """Gerador determinístico de tokens baseado em HMAC-SHA256."""

    def __init__(self, secret_key: bytes) -> None:
        if not secret_key:
            raise ValueError("A chave secreta não pode estar vazia.")
        self._secret_key = secret_key

    def generate(self, prefix: str, original_value: str) -> str:
        if original_value == "" or original_value.isspace():
            return original_value

        message = f"{prefix}\0{original_value}".encode("utf-8")
        digest = hmac.new(self._secret_key, message, hashlib.sha256).digest()
        code = base64.b32encode(digest).decode("ascii").rstrip("=")
        return f"{prefix}-{code[:TOKEN_CODE_LENGTH].upper()}"


def generate_token(secret_key: bytes, prefix: str, original_value: str) -> str:
    """Gera um token determinístico sem expor o valor original."""
    return TokenGenerator(secret_key).generate(prefix, original_value)
