import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from data_mask_studio.app import create_application
from data_mask_studio.gui.consultant_widget import ConsultantWidget
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

CODE = "NOME-ABCDEFGHI234"
ORIGINAL_VALUE = "sensitive-test-value"


def make_repository(tmp_path: Path) -> VaultRepository:
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"G" * 32))
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [MappingCandidate(CODE, "NOME", ORIGINAL_VALUE, "Nome", 2)]
        )
    return repository


def test_copy_is_disabled_without_results(tmp_path: Path) -> None:
    application = create_application([])
    widget = ConsultantWidget(lambda: make_repository(tmp_path))

    assert not widget.copy_button.isEnabled()
    assert widget.results_output.isReadOnly()

    widget.close()
    application.quit()


def test_consult_copy_and_clear_interface(tmp_path: Path) -> None:
    application = create_application([])
    repository = make_repository(tmp_path)
    widget = ConsultantWidget(lambda: repository)
    widget.show()
    application.processEvents()
    widget.codes_input.setPlainText(CODE.lower())

    widget.consult_button.click()

    displayed = widget.results_output.toPlainText()
    assert f"Código: {CODE}" in displayed
    assert f"Valor original principal: {ORIGINAL_VALUE}" in displayed
    assert "Ocorrências totais: 2" in displayed
    assert widget.copy_button.isEnabled()

    widget.copy_button.click()

    assert application.clipboard().text() == displayed
    assert "copiado" in widget.status_label.text()

    widget.clear_button.click()

    assert widget.codes_input.toPlainText() == ""
    assert widget.results_output.toPlainText() == ""
    assert widget.status_label.text() == ""
    assert not widget.copy_button.isEnabled()
    assert widget.codes_input.hasFocus()

    widget.close()
    application.quit()


def test_new_widget_has_no_persisted_query_history(tmp_path: Path) -> None:
    application = create_application([])
    repository = make_repository(tmp_path)
    first_widget = ConsultantWidget(lambda: repository)
    first_widget.codes_input.setPlainText(CODE)
    first_widget.consult()
    first_widget.close()

    second_widget = ConsultantWidget(lambda: repository)

    assert second_widget.codes_input.toPlainText() == ""
    assert second_widget.results_output.toPlainText() == ""
    assert not second_widget.copy_button.isEnabled()

    second_widget.close()
    application.quit()


def test_consultant_displays_normalization_and_multiple_variations(
    tmp_path: Path,
) -> None:
    application = create_application([])
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"G" * 32))
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    CODE,
                    "NOME",
                    "João  da Silva",
                    "Nome",
                    canonical_value="João da Silva",
                    normalization_rule=NormalizationRule.COLLAPSE_WHITESPACE,
                )
            ]
        )
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    CODE,
                    "NOME",
                    " João da Silva ",
                    "Nome",
                    occurrences=2,
                    canonical_value="João da Silva",
                    normalization_rule=NormalizationRule.COLLAPSE_WHITESPACE,
                )
            ]
        )
    widget = ConsultantWidget(lambda: repository)
    widget.codes_input.setPlainText(CODE)

    widget.consult()

    displayed = widget.results_output.toPlainText()
    assert "Regra de normalização: Texto sem espaços extras" in displayed
    assert "Valor original principal: João  da Silva" in displayed
    assert "Outra variação original 1:  João da Silva " in displayed
    assert "Ocorrências da variação: 2" in displayed
    assert "Ocorrências totais: 3" in displayed

    widget.close()
    application.quit()
