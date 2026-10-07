from PySide6.QtCore import QEvent, QModelIndex, Qt
from PySide6.QtGui import QHelpEvent, Qt as GuiQt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTextEdit,
    QToolTip,
)


class TruncatedTextToolTipDelegate(QStyledItemDelegate):
    """Opt-in para colunas de texto: revela só o valor já exibido quando cortado."""

    def helpEvent(
        self,
        event: QHelpEvent,
        view: QAbstractItemView,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        if event.type() != QEvent.Type.ToolTip:
            return super().helpEvent(event, view, option, index)
        if not index.isValid():
            return False
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        rect = view.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, styled, view,
        )
        rect = rect.intersected(view.viewport().rect())
        if styled.features & QStyleOptionViewItem.ViewItemFeature.WrapText:
            bounds = styled.fontMetrics.boundingRect(
                rect, Qt.TextFlag.TextWordWrap.value, styled.text,
            )
            fits = bounds.width() <= rect.width() and bounds.height() <= rect.height()
        else:
            fits = styled.fontMetrics.horizontalAdvance(styled.text) <= rect.width()
        if not styled.text or fits:
            QToolTip.hideText()
        else:
            QToolTip.showText(
                event.globalPos(),
                GuiQt.convertFromPlainText(styled.text),
                view.viewport(),
                option.rect,
            )
        return True


def set_button_role(button: QPushButton, role: str) -> None:
    button.setProperty("role", role)
    button.style().unpolish(button)
    button.style().polish(button)


def configure_path_field(field: QLineEdit, accessible_name: str) -> None:
    field.setAccessibleName(accessible_name)
    field.setMaximumWidth(760)
    field.setClearButtonEnabled(False)
    field.textChanged.connect(field.setToolTip)
    field.setToolTip(field.text())


def configure_table(table: QTableWidget) -> None:
    table.setAlternatingRowColors(True)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    table.verticalHeader().setDefaultSectionSize(30)
    table.horizontalHeader().setMinimumSectionSize(72)


def configure_result_area(
    editor: QPlainTextEdit | QTextEdit, empty_height: int = 280
) -> None:
    """Mantém relatórios vazios compactos e libera expansão quando preenchidos."""

    def update_height() -> None:
        editor.setMaximumHeight(
            16_777_215 if editor.toPlainText().strip() else empty_height
        )

    editor.textChanged.connect(update_height)
    update_height()
