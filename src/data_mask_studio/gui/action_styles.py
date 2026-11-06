"""Indicadores compartilhados pelas tabelas de configuração."""
from PySide6.QtWidgets import QComboBox, QLineEdit, QWidget

from data_mask_studio.anonymization.models import ColumnAction


def _set_indicator(control: QWidget, name: str, value: str | bool) -> None:
    if control.property(name) == value:
        return
    control.setProperty(name, value)
    control.style().unpolish(control)
    control.style().polish(control)
    control.update()


def set_action_indicator(control: QComboBox, action: ColumnAction) -> None:
    """Semantic identity only; colors belong to the shared palette stylesheet."""
    _set_indicator(control, "columnAction", action.value)


def set_validation_indicator(control: QLineEdit, invalid: bool) -> None:
    """Present the existing validation decision, without performing validation."""
    _set_indicator(control, "validationError", invalid)
