"""Contrato compartilhado de ação/prefixo das composites."""
from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.anonymization.prefix_rules import validate_prefix


def composite_action_error(action: ColumnAction, prefix: str) -> str | None:
    if not isinstance(action, ColumnAction) or action not in (ColumnAction.PRESERVE, ColumnAction.MASK):
        return "Ação composta inválida: use Preservar ou Mascarar."
    if not isinstance(prefix, str):
        return "Prefixo de composite inválido."
    if action is ColumnAction.PRESERVE:
        return "Uma composite Preservar deve ter prefixo vazio." if prefix != "" else None
    return validate_prefix(prefix)
