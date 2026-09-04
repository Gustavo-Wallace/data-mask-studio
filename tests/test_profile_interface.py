import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QInputDialog, QMessageBox

from data_mask_studio.anonymization import ColumnAction, ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService


def make_service(tmp_path: Path) -> ProfileService:
    return ProfileService(ProfileRepository(tmp_path / "profiles.json"))


def test_profile_controls_start_without_accessing_real_storage(tmp_path: Path) -> None:
    application = create_application([])
    window = MainWindow(profile_service=make_service(tmp_path))

    assert window.profile_combo.count() == 0
    assert not window.apply_profile_button.isEnabled()
    assert not window.save_profile_button.isEnabled()
    assert window.save_profile_button.text() == "Salvar como perfil"

    window.close()
    application.quit()


def test_corrupt_profile_file_does_not_prevent_csv_use(tmp_path: Path) -> None:
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text("invalid-json", encoding="utf-8")
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("Nome,Extra\nAna,x\n", encoding="utf-8")
    application = create_application([])
    window = MainWindow(
        profile_service=ProfileService(ProfileRepository(profile_path))
    )

    assert "inválido" in window.status_label.text()
    window.load_csv(str(csv_path))

    assert window.config_table.rowCount() == 2
    assert window.select_button.isEnabled()
    assert profile_path.read_text(encoding="utf-8") == "invalid-json"

    window.close()
    application.quit()


def test_complete_profile_is_applied_and_validated(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("Nome,CPF,Extra\nAna,123,x\n", encoding="utf-8")
    service = make_service(tmp_path)
    profile = service.create(
        "Dados pessoais",
        [
            ColumnConfig("Nome", True, "NOME", NormalizationRule.COLLAPSE_WHITESPACE),
            ColumnConfig("CPF", True, "CPF_ID", NormalizationRule.CPF),
        ],
    )
    application = create_application([])
    window = MainWindow(profile_service=service)
    window.load_csv(str(csv_path))

    window.apply_profile_button.click()

    assert window.profile_combo.currentData() == profile.identifier
    assert [item.anonymize for item in window._column_configs] == [True, True, False]
    assert window._column_configs[1].normalization_rule is NormalizationRule.CPF
    assert window.generate_button.isEnabled()
    assert "aplicado" in window.status_label.text()

    window.close()
    application.quit()


def test_profile_actions_are_restored_in_the_configuration_table(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "actions.csv"
    csv_path.write_text("Nome,Idade,Observacao\nAna,30,interno\n", encoding="utf-8")
    service = make_service(tmp_path)
    service.create(
        "Preparação de dados",
        [
            ColumnConfig("Nome", True, "NOME", NormalizationRule.PERSON_NAME),
            ColumnConfig("Idade", action=ColumnAction.PRESERVE),
            ColumnConfig("Observacao", action=ColumnAction.EXCLUDE),
        ],
    )
    application = create_application([])
    window = MainWindow(profile_service=service)
    window.load_csv(str(csv_path))

    window.apply_profile_button.click()

    assert [item.action for item in window._column_configs] == [
        ColumnAction.MASK,
        ColumnAction.PRESERVE,
        ColumnAction.EXCLUDE,
    ]
    assert window._prefix_fields[0].isEnabled()
    assert not window._prefix_fields[1].isEnabled()
    assert not window._prefix_fields[2].isEnabled()
    assert [field.property("columnAction") for field in window._action_fields] == [
        "mask",
        "preserve",
        "exclude",
    ]
    assert len({field.styleSheet() for field in window._action_fields}) == 3
    assert window.generate_button.isEnabled()

    window.close()
    application.quit()


def test_partial_profile_requires_review(tmp_path: Path) -> None:
    csv_path = tmp_path / "partial.csv"
    csv_path.write_text("Nome,Extra\nAna,x\n", encoding="utf-8")
    service = make_service(tmp_path)
    service.create(
        "Dados pessoais",
        [
            ColumnConfig("Nome", True, "NOME", NormalizationRule.EXACT),
            ColumnConfig("CPF", True, "CPF_ID", NormalizationRule.CPF),
        ],
    )
    application = create_application([])
    window = MainWindow(profile_service=service)
    window.load_csv(str(csv_path))

    window.apply_profile_button.click()

    assert window._column_configs[0].anonymize
    assert not window._column_configs[1].anonymize
    assert not window.generate_button.isEnabled()
    assert "Cabeçalhos não encontrados: CPF" in window.status_label.text()

    window.close()
    application.quit()


def test_incompatible_profile_does_not_change_current_configuration(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "other.csv"
    csv_path.write_text("Cidade,Estado\nRecife,PE\n", encoding="utf-8")
    service = make_service(tmp_path)
    service.create(
        "Dados pessoais",
        [ColumnConfig("CPF", True, "CPF_ID", NormalizationRule.CPF)],
    )
    application = create_application([])
    window = MainWindow(profile_service=service)
    window.load_csv(str(csv_path))

    window.apply_profile_button.click()

    assert all(not item.anonymize for item in window._column_configs)
    assert all(item.prefix == "" for item in window._column_configs)
    assert "não é compatível" in window.status_label.text()

    window.close()
    application.quit()


def test_manual_configuration_is_not_replaced_without_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("Nome,Extra\nAna,x\n", encoding="utf-8")
    service = make_service(tmp_path)
    service.create(
        "Perfil",
        [ColumnConfig("Nome", True, "PERFIL", NormalizationRule.EXACT)],
    )
    application = create_application([])
    window = MainWindow(profile_service=service)
    window.load_csv(str(csv_path))
    window._action_fields[0].setCurrentIndex(
        window._action_fields[0].findData(ColumnAction.MASK.value)
    )
    window._prefix_fields[0].setText("MANUAL")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    window.apply_profile_button.click()

    assert window._column_configs[0].prefix == "MANUAL"
    assert window._configuration_dirty

    window.close()
    application.quit()


def test_save_rename_update_and_delete_profile_from_interface(
    tmp_path: Path, monkeypatch
) -> None:
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("Nome,Extra\nAna,x\n", encoding="utf-8")
    service = make_service(tmp_path)
    application = create_application([])
    window = MainWindow(profile_service=service)
    window.load_csv(str(csv_path))
    window._action_fields[0].setCurrentIndex(
        window._action_fields[0].findData(ColumnAction.MASK.value)
    )
    window.validate_current_configuration()
    names = iter((("Perfil inicial", True), ("Perfil final", True)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: next(names))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    window.save_profile_button.click()
    identifier = window.profile_combo.currentData()
    window.rename_profile_button.click()
    window._prefix_fields[0].setText("NOVO")
    window.validate_current_configuration()
    window.update_profile_button.click()

    updated = service.list_profiles()[0]
    assert updated.identifier == identifier
    assert updated.name == "Perfil final"
    assert updated.columns[0].prefix == "NOVO"

    window.delete_profile_button.click()

    assert service.list_profiles() == []
    assert window.profile_combo.count() == 0

    window.close()
    application.quit()
