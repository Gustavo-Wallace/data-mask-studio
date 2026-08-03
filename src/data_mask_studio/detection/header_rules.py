import re
import unicodedata

from data_mask_studio.detection.models import SuggestedType


_ALIASES: tuple[tuple[SuggestedType, frozenset[str]], ...] = (
    (SuggestedType.CPF, frozenset({"cpf", "documento_cpf", "numero_cpf"})),
    (SuggestedType.CNPJ, frozenset({"cnpj", "documento_cnpj", "numero_cnpj"})),
    (
        SuggestedType.EMAIL,
        frozenset({"email", "e_mail", "correio_eletronico", "email_usuario"}),
    ),
    (
        SuggestedType.PHONE,
        frozenset({"telefone", "fone", "celular", "mobile", "whatsapp"}),
    ),
    (
        SuggestedType.IP_ADDRESS,
        frozenset({"ip", "endereco_ip", "ip_address", "endereco_ipv4", "endereco_ipv6"}),
    ),
    (
        SuggestedType.NAME,
        frozenset({"nome", "nome_completo", "usuario", "responsavel", "titular"}),
    ),
    (
        SuggestedType.GENERIC_ID,
        frozenset({"id", "identificador", "codigo", "matricula", "registro", "chave"}),
    ),
    (
        SuggestedType.COMMON_TEXT,
        frozenset(
            {
                "descricao",
                "observacao",
                "mensagem",
                "comentario",
                "texto",
                "cidade",
                "estado",
                "produto",
            }
        ),
    ),
)


def normalize_header(header: str) -> str:
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", header)
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", "_", without_accents.casefold()).strip("_")


def detect_header_type(header: str) -> SuggestedType | None:
    normalized = normalize_header(header)
    tokens = frozenset(normalized.split("_"))
    for suggested_type, aliases in _ALIASES:
        if normalized in aliases or aliases.intersection(tokens):
            return suggested_type
    return None
