"""Consulta exata de códigos armazenados no cofre local."""

from data_mask_studio.consultant.code_parser import is_valid_code, parse_codes
from data_mask_studio.consultant.models import (
    ConsultationResult,
    ConsultationStatus,
)
from data_mask_studio.consultant.service import ConsultantService

__all__ = [
    "ConsultantService",
    "ConsultationResult",
    "ConsultationStatus",
    "is_valid_code",
    "parse_codes",
]

