import re

from data_mask_studio.consultant.code_parser import is_valid_code
from data_mask_studio.restoration.models import (
    CellClassification,
    ClassifiedCell,
)

_CODE_LIKE_PATTERN = re.compile(r"^[^\s-]+-[^\s-]+$")


def classify_cell_format(value: str) -> ClassifiedCell:
    """Classifica o formato sem consultar nem expor o conteudo do cofre."""
    stripped = value.strip()
    if not stripped:
        return ClassifiedCell(CellClassification.EMPTY)

    lookup_code = stripped.upper()
    if is_valid_code(lookup_code):
        prefix = lookup_code.rsplit("-", 1)[0]
        return ClassifiedCell(
            CellClassification.NOT_FOUND,
            lookup_code=lookup_code,
            prefix=prefix,
        )
    if _CODE_LIKE_PATTERN.fullmatch(stripped):
        return ClassifiedCell(CellClassification.INVALID_CODE_LIKE)
    return ClassifiedCell(CellClassification.COMMON)
