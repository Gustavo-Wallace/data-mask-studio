"""Indicadores compartilhados pelas tabelas de configuração."""
from data_mask_studio.anonymization.models import ColumnAction
from data_mask_studio.gui.visual_tokens import COLORS

ACTION_INDICATOR_STYLES = {
    ColumnAction.PRESERVE: (
        f"QComboBox {{ background-color: {COLORS['action_preserve']}; "
        f"border-color: {COLORS['neutral_indicator']}; }}"
    ),
    ColumnAction.MASK: (
        f"QComboBox {{ background-color: {COLORS['action_mask']}; "
        f"border-color: {COLORS['accent']}; }}"
    ),
    ColumnAction.EXCLUDE: (
        f"QComboBox {{ background-color: {COLORS['danger_surface']}; "
        f"border-color: {COLORS['danger_border']}; }}"
    ),
}
