import csv
import sqlite3

import pytest

from data_mask_studio.anonymization import ColumnAction, ColumnConfig, generate_token
from data_mask_studio.csv_tools import anonymize_csv
from data_mask_studio.normalization import NormalizationError, NormalizationRule, normalize_value
from data_mask_studio.normalization import registry
from data_mask_studio.restoration import RestorationConfiguration, SelectedColumn, restore_csv
from data_mask_studio.vault import VaultCipher, VaultRepository


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.reader(stream))


def test_nonempty_combining_mark_rejected_without_disclosure():
    with pytest.raises(NormalizationError) as error:
        normalize_value("\u0301", NormalizationRule.PERSON_NAME)
    assert "\u0301" not in str(error.value)
    assert normalize_value("João da Silva", NormalizationRule.PERSON_NAME) == "joao da silva"


def test_guard_is_general_and_preserves_internal_whitespace_contract(monkeypatch):
    monkeypatch.setitem(registry._NORMALIZERS, NormalizationRule.EMAIL, lambda value: "")
    with pytest.raises(NormalizationError):
        normalize_value("nonempty", NormalizationRule.EMAIL)
    for value in ("", "   ", "\t", "\u00a0"):
        assert normalize_value(value, NormalizationRule.EMAIL) == value
    monkeypatch.setitem(registry._NORMALIZERS, NormalizationRule.EMAIL, lambda value: " ")
    assert normalize_value("nonempty", NormalizationRule.EMAIL) == " "


@pytest.mark.parametrize("action", [ColumnAction.MASK, ColumnAction.PRESERVE])
def test_csv_empty_canonical_uses_exact_fallback_and_preserves_blanks(tmp_path, action):
    original_values = ["\u0301", "", "   ", "\t", "\u00a0", "\u0301"]
    source = tmp_path / "input.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([["NOME"], *[[value] for value in original_values]])
    original_bytes = source.read_bytes()
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    output = tmp_path / "output.csv"
    result = anonymize_csv(
        source, output, encoding="utf-8", delimiter=",", secret_key=b"H" * 32,
        configurations=[ColumnConfig("NOME", action=action, prefix="NM",
                                     normalization_rule=NormalizationRule.PERSON_NAME)],
        vault_repository=repository,
    )
    expected = list(original_values)
    if action is ColumnAction.MASK:
        token = generate_token(b"H" * 32, "NM", "\u0301")
        expected[0] = expected[-1] = token
        assert token.startswith("NM-") and len(token) == 15
        mapping = repository.get_decrypted_mapping(token)
        assert mapping.canonical_value == mapping.original_value == "\u0301"
        assert mapping.normalization_rule is NormalizationRule.EXACT
        assert mapping.occurrence_count == 2
        assert repository.count() == 1
        restored = tmp_path / "restored.csv"
        restore_csv(RestorationConfiguration(output, "utf-8-sig", ",", ("NOME",),
                                             (SelectedColumn(0, "NOME"),)), restored, repository)
        assert rows(restored) == [["NOME"], *[[value] for value in original_values]]
    else:
        with sqlite3.connect(repository.database_path) as connection:
            for table in ("vault_mappings", "vault_variations"):
                assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert rows(output) == [["NOME"], *[[value] for value in expected]]
    assert len(result.normalization_fallbacks) == 1
    assert result.normalization_fallbacks[0].header == "NOME"
    assert result.normalization_fallbacks[0].count == 2
    assert "\u0301" not in repr(result.normalization_fallbacks)
    assert source.read_bytes() == original_bytes
