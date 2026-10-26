from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from data_mask_studio.gui import anonymization_widget as module
from test_composite_gui import gui


@pytest.mark.parametrize("chosen", ["default", "custom_anonimizado.csv"])
def test_individual_save_suggests_masked_but_preserves_explicit_path(
    gui, tmp_path, monkeypatch, chosen,
):
    _, widget, _ = gui
    widget.select_all_columns()
    widget.validate_current_configuration()
    expected = tmp_path / "source_masked.csv"
    destination = expected if chosen == "default" else tmp_path / chosen
    dialogs, started = [], []

    def choose(parent, title, suggestion, file_filter):
        dialogs.append((title, Path(suggestion), file_filter))
        return str(destination), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    monkeypatch.setattr(widget, "_start_processing", lambda path, **kw: started.append((path, kw)))
    widget._choose_output_file()

    assert widget.generate_button.text() == "Gerar CSV mascarado"
    assert dialogs == [("Salvar CSV mascarado", expected, "Arquivos CSV (*.csv)")]
    assert started == [(destination, {"overwrite": False})]
    assert not destination.exists()  # A suggestion never creates a file.


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
def test_new_default_collision_still_requires_explicit_overwrite(gui, tmp_path, monkeypatch, answer):
    _, widget, _ = gui
    widget.select_all_columns()
    widget.validate_current_configuration()
    destination = tmp_path / "source_masked.csv"
    destination.write_bytes(b"existing unrelated output")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(destination), ""))
    confirmations, started = [], []

    def confirm(*args, **kwargs):
        confirmations.append((args, kwargs))
        return answer

    monkeypatch.setattr(module, "confirm_destructive_action", confirm)
    monkeypatch.setattr(widget, "_start_processing", lambda path, **kw: started.append((path, kw)))
    widget._choose_output_file()

    assert len(confirmations) == 1
    assert confirmations[0][1] == {"translator": widget._translator}
    assert started == ([(destination, {"overwrite": True})] if answer == QMessageBox.StandardButton.Yes else [])
    assert destination.read_bytes() == b"existing unrelated output"


def test_source_alias_remains_rejected(gui, monkeypatch):
    _, widget, _ = gui
    widget.select_all_columns()
    widget.validate_current_configuration()
    source = widget._inspection_result.path
    original = source.read_bytes()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(source), ""))
    monkeypatch.setattr(widget, "_start_processing", lambda *a, **kw: pytest.fail("Input must not be overwritten"))
    widget._choose_output_file()
    assert "mesmo CSV original" in widget.status_label.text()
    assert source.read_bytes() == original


def test_masking_file_selection_remains_csv_only(gui, monkeypatch):
    _, widget, _ = gui
    dialogs = []

    def choose(parent, title, initial, file_filter):
        dialogs.append((title, file_filter))
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    widget._select_csv()
    assert dialogs == [("Selecionar arquivo CSV", "Arquivos CSV (*.csv)")]
