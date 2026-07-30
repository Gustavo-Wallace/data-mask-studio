import hashlib
import hmac

TOKEN_HEX_LENGTH = 24


def generate_token(secret_key: bytes, prefix: str, original_value: str) -> str:
    """Gera um token determinístico sem expor o valor original."""
    if original_value == "" or original_value.isspace():
        return original_value
    if not secret_key:
        raise ValueError("A chave secreta não pode estar vazia.")

    message = f"{prefix}\0{original_value}".encode("utf-8")
    code = hmac.new(secret_key, message, hashlib.sha256).hexdigest()
    return f"{prefix}-{code[:TOKEN_HEX_LENGTH].upper()}"

