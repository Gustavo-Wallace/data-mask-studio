"""Indicadores compartilhados pelas tabelas de configuração."""
from data_mask_studio.anonymization.models import ColumnAction

ACTION_INDICATOR_STYLES = {
    ColumnAction.PRESERVE: "QComboBox { background-color: #18202a; border-color: #465569; }",
    ColumnAction.MASK: "QComboBox { background-color: #1d3449; border-color: #347db8; }",
    ColumnAction.EXCLUDE: "QComboBox { background-color: #352322; border-color: #854842; }",
}
