"""Localized presentation for the existing normalization choices only."""

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QComboBox

from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NormalizationRule


def set_normalization_help(
    combo: QComboBox, translator: Translator, *,
    context_id: str = "masking.column.normalization_tooltip",
) -> None:
    with QSignalBlocker(combo):
        for index in range(combo.count()):
            rule = NormalizationRule(combo.itemData(index))
            hint = translator.text(f"masking.normalization.help.{rule.value}")
            combo.setItemData(index, hint, Qt.ItemDataRole.ToolTipRole)
            combo.setItemData(index, hint, Qt.ItemDataRole.AccessibleDescriptionRole)
    context = translator.text(context_id)
    combo.setToolTip(context)
    combo.setAccessibleDescription(context)
