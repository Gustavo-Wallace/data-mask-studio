import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from data_mask_studio.anonymization import ColumnAction, ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import VaultCipher, VaultRepository


@pytest.fixture
def editor(tmp_path):
    app = create_application([])
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    widget = AnonymizationWidget(profile_service=service)
    widget.resize(1200, 700)
    widget.show()
    yield app, widget, service
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_rename_editor_validation_exclusion_profile_and_repopulation(editor, tmp_path):
    app, widget, service = editor
    source = tmp_path / "source.csv"
    source.write_text("NOME,CPF\nAna,123\n", encoding="utf-8")
    widget.load_csv(str(source))
    app.processEvents()
    field = widget._output_name_fields[0]
    assert field.placeholderText() == "Manter original"
    field.setText(" CPF ")
    widget.validate_current_configuration()
    assert not widget.generate_button.isEnabled()
    assert "NOME" in widget.status_label.text() and "CPF" in widget.status_label.text()
    assert field.styleSheet() and widget._output_name_fields[1].styleSheet()
    field.setText("PESSOA")
    widget._normalization_fields[0].setCurrentIndex(
        widget._normalization_fields[0].findData(NormalizationRule.PERSON_NAME.value)
    )
    widget.validate_current_configuration()
    assert widget.generate_button.isEnabled()
    assert not field.styleSheet()
    action = widget._action_fields[0]
    action.setCurrentIndex(action.findData(ColumnAction.EXCLUDE.value))
    assert not field.isEnabled() and field.text() == "PESSOA"
    action.setCurrentIndex(action.findData(ColumnAction.PRESERVE.value))
    assert field.isEnabled() and field.text() == "PESSOA"
    service.create("Renomeação GUI", widget._column_configs)
    widget.load_csv(str(source))
    assert all(not f.text() for f in widget._output_name_fields)
    widget._apply_profile_configurations(service.list_profiles()[0].columns)
    app.processEvents()
    assert widget._output_name_fields[0].text() == "PESSOA"
    assert widget._column_configs[0].header == "NOME"
    assert widget._column_configs[0].normalization_rule is NormalizationRule.PERSON_NAME
    for index, fields in ((0, widget._action_fields), (4, widget._normalization_fields)):
        assert widget.config_table.columnWidth(index) >= max(f.sizeHint().width() for f in fields)


@pytest.mark.parametrize("column", [0, 4])
def test_closed_combo_wheel_scrolls_table_and_keyboard_still_selects(editor, tmp_path, column):
    app, widget, _service = editor
    source = tmp_path / "many.csv"
    source.write_text(",".join(f"Column{i}" for i in range(100)) + "\n" + ",".join("x" for _ in range(100)) + "\n", encoding="utf-8")
    widget.load_csv(str(source))
    app.processEvents()
    table = widget.config_table
    combo = table.cellWidget(0, column)
    table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum() if column == 4 else 0)
    app.processEvents()
    combo.setFocus()
    initial = combo.currentIndex()
    scroll = table.verticalScrollBar()
    assert scroll.maximum() > 0
    position = combo.mapTo(widget, combo.rect().center())
    QTest.wheelEvent(widget.windowHandle(), position, QPoint(0, -120))
    app.processEvents()
    assert combo.currentIndex() == initial
    assert scroll.value() > 0
    scroll.setValue(0)
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentIndex() == initial + 1
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert combo.view().isVisible()
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentIndex() == initial + 2


def test_worker_writes_edited_header(editor, tmp_path):
    app, widget, _service = editor

    class KeyProvider:
        def get_key(self):
            return b"K" * 32

    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    widget._key_provider = KeyProvider()
    widget._vault_repository_factory = lambda: repository
    source = tmp_path / "source.csv"
    source.write_text("NOME\nAna\n", encoding="utf-8")
    widget.load_csv(str(source))
    widget._output_name_fields[0].setText(" PESSOA ")
    widget.validate_current_configuration()
    output = tmp_path / "output.csv"
    widget._start_processing(output, overwrite=False)
    assert widget._worker.wait(5000)
    app.processEvents()
    with output.open(encoding="utf-8-sig", newline="") as file:
        assert list(csv.reader(file)) == [["PESSOA"], ["Ana"]]
    assert repository.count() == 0


def test_profile_collision_remains_visible_in_gui(editor, tmp_path):
    app, widget, service = editor
    service.create("Conflito com extra", [ColumnConfig("NOME", output_name=" CPF ")])
    widget.refresh_profiles()
    source = tmp_path / "extra.csv"
    source.write_text("NOME,CPF\nAna,123\n", encoding="utf-8")
    widget.load_csv(str(source))
    widget.apply_profile_button.click()
    app.processEvents()
    assert not widget.generate_button.isEnabled()
    assert "Cabeçalho de saída repetido" in widget.status_label.text()
    assert widget._output_name_fields[0].styleSheet()
