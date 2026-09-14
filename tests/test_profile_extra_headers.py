import pytest

from data_mask_studio.anonymization import ColumnAction, ColumnConfig
from data_mask_studio.batch import BatchError, BatchFile, BatchFileStatus, BatchService, validate_file
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService


@pytest.mark.parametrize("headers,extra,missing", [
    (("NOME", "CPF"), (), ()), (("CPF", "NOME"), (), ()),
    (("NOME", "CPF", "IDADE"), ("IDADE",), ()),
    (("EMAIL", "CPF", "IDADE", "NOME"), ("EMAIL", "IDADE"), ()),
    (("NOME", "EMAIL"), ("EMAIL",), ("CPF",)),
])
def test_application_reports_extras_without_changing_profile(tmp_path, headers, extra, missing):
    repository = ProfileRepository(tmp_path / "profiles.json")
    service = ProfileService(repository)
    profile = service.create("Perfil existente", [
        ColumnConfig("NOME", True, "NM", NormalizationRule.PERSON_NAME, output_name="PESSOA"),
        ColumnConfig("CPF", action=ColumnAction.EXCLUDE),
    ])
    before = repository.path.read_bytes()
    loaded = ProfileRepository(repository.path).load()[0]
    result = service.apply(loaded, headers)
    assert result.extra_headers == extra
    assert result.missing_headers == missing
    assert result.is_complete == (not extra and not missing)
    known = {column.header: column for column in profile.columns}
    for config in result.configurations:
        if config.header in known:
            assert config == known[config.header]
        else:
            assert config.action is ColumnAction.PRESERVE
            assert config.normalization_rule is NormalizationRule.EXACT
    assert repository.path.read_bytes() == before


def test_batch_rejects_extras_before_loading_keys_or_writing(tmp_path):
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create("Nome", [ColumnConfig("NOME", True, "NM")])
    source = tmp_path / "extra.csv"
    source.write_text("NOME,CPF\nprivate-value,private-cpf\n", encoding="utf-8")
    item = BatchFile(source)
    validate_file(item, profile, service)
    assert item.status is BatchFileStatus.INCOMPATIBLE
    assert "adicionais" in item.result_message and "CPF" in item.result_message
    assert "private" not in item.result_message

    class ForbiddenKey:
        def get_key(self):
            pytest.fail("Não deve carregar chaves")

    def forbidden_vault():
        pytest.fail("Não deve abrir cofre")

    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(BatchError, match="compatíveis"):
        BatchService(service).process([item], profile, output, ForbiddenKey(), forbidden_vault)
    assert list(output.iterdir()) == []


def test_gui_keeps_extra_column_for_explicit_review(tmp_path):
    from data_mask_studio.app import create_application
    from data_mask_studio.gui.anonymization_widget import AnonymizationWidget

    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    service.create("Nome", [ColumnConfig("NOME", True, "NM", NormalizationRule.PERSON_NAME, output_name="PESSOA")])
    source = tmp_path / "extra.csv"
    source.write_text("NOME,CPF\nprivate-value,private-cpf\n", encoding="utf-8")
    app = create_application([])
    widget = AnonymizationWidget(profile_service=service)
    try:
        widget.load_csv(str(source))
        widget.apply_profile_button.click()
        app.processEvents()
        assert not widget.generate_button.isEnabled()
        assert not widget._configuration_validated
        assert "parcialmente" in widget.status_label.text()
        assert "CPF" in widget.status_label.text() and "adicionais" in widget.status_label.text()
        assert "private" not in widget.status_label.text()
        assert widget.config_table.rowCount() == 2
        known, extra = widget._column_configs
        assert known.action is ColumnAction.MASK and known.prefix == "NM"
        assert known.normalization_rule is NormalizationRule.PERSON_NAME
        assert known.output_name == "PESSOA"
        assert extra.header == "CPF" and extra.action is ColumnAction.PRESERVE
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()
