"""Scoped table styling and native interactions, without pixel/screenshot assertions."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView, QLineEdit, QListWidget, QTableView, QTableWidget,
    QTableWidgetItem, QTreeWidget, QVBoxLayout, QWidget,
)

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.app import create_application
from data_mask_studio.gui.action_styles import ACTION_INDICATOR_STYLES
from data_mask_studio.gui.detection_dialog import DetectionDialog
from data_mask_studio.gui.visual_tokens import COLORS, METRICS
from test_main_navigation import build_window
from test_visual_foundation import rule


def test_table_rules_use_scoped_tokens_and_keep_grid_and_compact_headers():
    body = rule("QTableView, QTableWidget")
    assert f"background: {COLORS['base']}" in body
    assert f"alternate-background-color: {COLORS['table_alternate']}" in body
    assert f"gridline-color: {COLORS['table_grid']}" in body
    assert f"border: 1px solid {COLORS['panel_border']}" in body
    assert f"selection-background-color: {COLORS['selection']}" in body
    assert f"selection-color: {COLORS['white']}" in body
    header = rule("QTableView QHeaderView::section")
    assert f"background: {COLORS['raised_surface']}" in header
    assert f"color: {COLORS['text']}" in header
    assert f"border-right: 1px solid {COLORS['table_grid']}" in header
    assert f"border-bottom: 1px solid {COLORS['panel_border']}" in header
    assert f"padding: {METRICS['table_header_padding']}px {METRICS['input_padding']}px" in header
    assert "font-weight: 600" in header and "gradient" not in header
    assert METRICS["table_header_padding"] <= METRICS["input_padding"]
    assert f"background: {COLORS['raised_surface']}" in rule("QTableView QTableCornerButton::section")
    # Non-table headers retain the previous rule; no broad list/tree item selector.
    assert "padding: 7px" in rule("QHeaderView::section")


def test_table_hover_selection_keyboard_focus_and_disabled_text_remain_distinct():
    assert f"background: {COLORS['table_hover']}" in rule("QTableView::item:hover:!selected")
    selected = rule("QTableView::item:selected")
    assert f"background: {COLORS['selection']}" in selected
    assert f"color: {COLORS['white']}" in selected
    assert f"border: 1px solid {COLORS['focus']}" in rule("QTableView::item:focus")
    assert f"border-color: {COLORS['input_focus']}" in rule("QTableView:focus, QTableWidget:focus")
    for selector in ("QTableView:disabled, QTableWidget:disabled",
                     "QTableView QHeaderView::section:disabled", "QTableView::item:selected:disabled"):
        assert f"color: {COLORS['disabled_text']}" in rule(selector)
        assert f"background: {COLORS['disabled_surface']}" in rule(selector)
    assert len({COLORS[name] for name in (
        "base", "table_alternate", "table_hover", "selection", "focus",
    )}) == 5


def test_all_eight_current_tables_receive_the_shared_style_without_changing_density_or_modes(tmp_path):
    app = create_application([])
    window = build_window(tmp_path)
    detection = DetectionDialog((), 0, window)
    individual = window.anonymization_widget
    batch_restore = window.batch_restoration_widget
    tables = (
        individual.config_table, individual.composite_section.table,
        window.batch_widget.file_table, window.restoration_widget.table,
        batch_restore.file_table, batch_restore.column_table,
        window.maintenance_widget.temporary_table, detection.table,
    )
    assert set(window.findChildren(QTableWidget)) == set(tables)
    expected_modes = ("NoSelection", "NoSelection", "ExtendedSelection", "NoSelection",
                      "ExtendedSelection", "ExtendedSelection", "ExtendedSelection", "NoSelection")
    for table, selection_mode in zip(tables, expected_modes):
        table.ensurePolished()
        assert table.showGrid() and table.alternatingRowColors()
        assert table.verticalHeader().defaultSectionSize() == 30
        assert table.selectionMode().name == selection_mode
        assert not table.isSortingEnabled()
        assert table.palette().color(QPalette.ColorRole.AlternateBase) == QColor(COLORS["table_alternate"])
        expected_base = "base" if table.isEnabled() else "disabled_surface"
        assert table.palette().color(QPalette.ColorRole.Base) == QColor(COLORS[expected_base])
    assert window.batch_widget.file_table.selectionBehavior() is QAbstractItemView.SelectionBehavior.SelectRows
    assert batch_restore.file_table.selectionBehavior() is QAbstractItemView.SelectionBehavior.SelectRows
    for table in (window.batch_widget.file_table, batch_restore.file_table, batch_restore.column_table,
                  individual.composite_section.table, window.maintenance_widget.temporary_table):
        assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert [individual.config_table.horizontalHeader().sectionResizeMode(i).name for i in range(5)] == [
        "ResizeToContents", "Interactive", "Interactive", "Interactive", "ResizeToContents",
    ]
    assert [individual.composite_section.table.horizontalHeader().sectionResizeMode(i).name for i in range(5)] == [
        "ResizeToContents", "Interactive", "Stretch", "Interactive", "ResizeToContents",
    ]
    assert app is not None


@pytest.mark.parametrize("kind", ["view", "widget"])
def test_native_table_selection_editing_sorting_and_resizing_still_work(kind):
    app = create_application([])
    host = QWidget()
    layout = QVBoxLayout(host)
    if kind == "view":
        table = QTableView()
        model = QStandardItemModel(2, 2, table)
        for row, name in enumerate(("Beta", "Alpha")):
            model.setItem(row, 0, QStandardItem(name))
            model.setItem(row, 1, QStandardItem("Synthetic value"))
        table.setModel(model)
    else:
        table = QTableWidget(2, 2)
        for row, name in enumerate(("Beta", "Alpha")):
            table.setItem(row, 0, QTableWidgetItem(name))
            table.setItem(row, 1, QTableWidgetItem("Synthetic value"))
        model = table.model()
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    outside = QLineEdit("Outside table")
    layout.addWidget(table)
    layout.addWidget(outside)
    host.resize(500, 240)
    host.show()
    app.processEvents()
    assert table.palette().color(QPalette.ColorRole.Base) == QColor(COLORS["base"])
    assert table.palette().color(QPalette.ColorRole.AlternateBase) == QColor(COLORS["table_alternate"])
    assert table.palette().color(QPalette.ColorRole.Highlight) == QColor(COLORS["selection"])
    table.setCurrentIndex(model.index(0, 0))
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_Down)
    assert table.currentIndex().row() == 1 and table.hasFocus()
    assert len(table.selectionModel().selectedIndexes()) == 2
    outside.setFocus()
    app.processEvents()
    assert not table.hasFocus() and len(table.selectionModel().selectedIndexes()) == 2
    changes = []
    model.dataChanged.connect(lambda *args: changes.append(args[0]))
    table.setCurrentIndex(model.index(0, 1))
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_F2)
    app.processEvents()
    editor = next(field for field in table.findChildren(QLineEdit) if field.isVisible())
    assert editor.isEnabled() and editor.hasFocus()
    assert editor.palette().color(QPalette.ColorRole.Base) == QColor(COLORS["base"])
    editor.selectAll()
    QTest.keyClicks(editor, "Updated value")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    app.processEvents()  # Qt queues the delegate's commit/close after Enter.
    assert model.index(0, 1).data() == "Updated value" and len(changes) == 1
    table.setSortingEnabled(True)
    table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    assert [model.index(row, 0).data() for row in range(2)] == ["Alpha", "Beta"]
    header = table.horizontalHeader()
    mode = header.sectionResizeMode(0)
    original_width = header.sectionSize(0)
    header.resizeSection(0, original_width + 20)
    assert header.sectionSize(0) > original_width and header.sectionResizeMode(0) == mode


def test_configuration_editors_action_colors_and_restoration_checkboxes_are_preserved(tmp_path):
    app = create_application([])
    window = build_window(tmp_path)
    source = tmp_path / "synthetic.csv"
    source.write_text("Nome Completo,CPF\nSynthetic Person,123\n", encoding="utf-8")
    window.show()
    widget = window.anonymization_widget
    widget.load_csv(str(source))
    app.processEvents()
    assert not widget.config_table.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable
    assert widget._output_name_fields[0].isEnabled() and not widget._prefix_fields[0].isEnabled()
    assert widget._prefix_fields[0].palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == QColor(COLORS["disabled_text"])
    action = widget._action_fields[0]
    action.setCurrentIndex(action.findData(ColumnAction.MASK.value))
    assert action.styleSheet() == ACTION_INDICATOR_STYLES[ColumnAction.MASK]
    assert widget._prefix_fields[0].isEnabled() and widget._prefix_fields[0].text() == "NOME_COMPLETO"
    action.setFocus()
    assert action.hasFocus()
    prefix = widget._prefix_fields[0]
    prefix.setFocus()
    prefix.selectAll()
    QTest.keyClicks(prefix, "PERSON")
    assert widget._column_configs[0].prefix == "PERSON"
    assert action.palette().color(QPalette.ColorRole.Base) == QColor(COLORS["action_mask"])
    restoration = window.restoration_widget
    restoration.load_csv(str(source))
    window.set_current_page(window.page_index(restoration))
    app.processEvents()
    checkbox = restoration._checkboxes[0]
    changed = []
    checkbox.stateChanged.connect(changed.append)
    QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton)
    assert checkbox.isChecked() and len(changed) == 1
    assert "1 de 2" in restoration.selected_count_label.text()
    assert not restoration.table.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable


def test_table_only_rules_do_not_change_external_forms_lists_trees_or_combo_popups():
    create_application([])
    from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox

    field = QLineEdit("Unchanged form")
    field.ensurePolished()
    assert field.palette().color(QPalette.ColorRole.Base) == QColor(COLORS["base"])
    for view in (QListWidget(), QTreeWidget()):
        view.ensurePolished()
        assert view.palette().color(QPalette.ColorRole.AlternateBase) == QColor(COLORS["surface"])
        assert view.palette().color(QPalette.ColorRole.Highlight) == QColor(COLORS["selection"])
    combo = ScrollSafeComboBox()
    combo.addItems(["First", "Second"])
    combo.ensurePolished()
    popup = combo.view()
    popup.ensurePolished()
    assert popup.palette().color(QPalette.ColorRole.AlternateBase) == QColor(COLORS["surface"])
    assert popup.palette().color(QPalette.ColorRole.Highlight) == QColor(COLORS["selection"])
