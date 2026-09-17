import csv
import sqlite3
from dataclasses import replace

import pytest

from data_mask_studio.consultant import ConsultantService, ConsultationStatus
from data_mask_studio.gui.consultant_widget import _render_result
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.composite_text import composite_text
from data_mask_studio.restoration import (
    RestorationConfiguration, SelectedColumn, RepresentationPolicy, MissingCodePolicy,
    RestorationSecurityError, MissingCodeError, restore_csv,
)
from data_mask_studio.restoration.analyzer import analyze_csv
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.vault import VaultRepository, VaultCipher, MappingCandidate
from data_mask_studio.vault.composite_models import CompositeMappingCandidate, CompositeMapping

AES = b'V' * 32
HMAC = b'H' * 32


def setup(tmp_path, original=('Gustavo', '999.999.999-99'), *, exact=False, occurrences=1):
    repo = VaultRepository(tmp_path / 'vault.db', VaultCipher(AES))
    canonical = original if exact else ('gustavo', '99999999999')
    rules = (Rule.EXACT, Rule.EXACT) if exact else (Rule.PERSON_NAME, Rule.CPF)
    item = CompositeMappingCandidate(generate_composite_token(HMAC, 'CORR', canonical), 'CORR',
                                    canonical, original, rules, occurrences)
    repo.composite_repository().upsert_composite_mapping(item)
    return repo, item


def source_config(tmp_path, rows, policy=RepresentationPolicy.FIRST_ORIGINAL, missing=MissingCodePolicy.KEEP):
    source = tmp_path / 'input.csv'
    with source.open('w', encoding='utf-8', newline='') as stream:
        csv.writer(stream).writerows([['DADO'], *[[value] for value in rows]])
    return RestorationConfiguration(source, 'utf-8', ',', ('DADO',), (SelectedColumn(0, 'DADO'),), missing, policy)


def read(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.reader(stream))


@pytest.mark.parametrize('original', [
    ('Gustavo', ''), (' São José 😀 ', '漢字'), ('"a",b\\c\n|', ' x\t '),
])
@pytest.mark.parametrize('policy', list(RepresentationPolicy))
def test_single_original_preserves_every_character(tmp_path, original, policy, monkeypatch):
    repo, item = setup(tmp_path, original, exact=True, occurrences=100)
    def forbidden(*args):
        pytest.fail('Restoration não pode executar normalizadores')
    monkeypatch.setattr('data_mask_studio.vault.composite_repository.normalize_value', forbidden)
    config = source_config(tmp_path, [item.code, item.code], policy)
    before = repo.database_path.read_bytes()
    result = restore_csv(config, tmp_path / 'output.csv', repo)
    assert read(result.output_path) == [['DADO'], [composite_text(original)], [composite_text(original)]]
    assert result.composite_restored_exact == 2 and result.composite_restored_canonical == 0
    assert result.restored_codes == 2
    assert before == repo.database_path.read_bytes()


@pytest.mark.parametrize('reverse', [False, True])
def test_ambiguous_always_uses_stored_canonical(tmp_path, reverse, monkeypatch):
    repo, item = setup(tmp_path)
    repo.composite_repository().upsert_composite_mapping(replace(item, original_values=('GUSTAVO', '99999999999')))
    if reverse:
        with sqlite3.connect(repo.database_path) as connection:
            connection.execute('UPDATE composite_variations SET rowid = rowid + 100')
            connection.execute('UPDATE composite_variations SET rowid = 200 - rowid')
    monkeypatch.setattr('data_mask_studio.vault.composite_repository.normalize_value', lambda *a: pytest.fail('normalizer'))
    config = source_config(tmp_path, [item.code])
    first = restore_csv(config, tmp_path / 'first.csv', repo)
    second = restore_csv(config, tmp_path / 'second.csv', repo)
    assert first.output_path.read_bytes() == second.output_path.read_bytes()
    assert read(first.output_path)[1] == [composite_text(item.canonical_values)]
    assert first.composite_restored_exact == 0 and first.composite_restored_canonical == 1


def test_mixed_scalar_composite_cleartext_and_unknown(tmp_path):
    repo, item = setup(tmp_path)
    scalar = 'CORR-AAAAAAAAAAAA'  # Mesmo prefixo não determina o tipo.
    with repo.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(scalar, 'CORR', 'scalar original', 'DADO')])
    clear = composite_text(('already', 'clear'))
    unknown = 'CORR-BBBBBBBBBBBB'
    headers = ('DADO', 'PESSOA', 'CLARA', 'AUSENTE')
    source = tmp_path / 'mixed.csv'
    with source.open('w', encoding='utf-8', newline='') as stream:
        csv.writer(stream).writerows([headers, [scalar, item.code, clear, unknown]])
    config = RestorationConfiguration(source, 'utf-8', ',', headers,
                                      tuple(SelectedColumn(i, name) for i, name in enumerate(headers)))
    analysis = analyze_csv(config, repo)
    assert analysis.found_codes == 2 and analysis.missing_codes == 1
    result = restore_csv(config, tmp_path / 'output.csv', repo)
    assert read(result.output_path) == [list(headers), ['scalar original', composite_text(item.original_values), clear, unknown]]
    assert result.restored_codes == 2 and result.composite_restored_exact == 1
    assert result.preserved_common_values == 1 and result.missing_codes == 1


@pytest.mark.parametrize('policy', [MissingCodePolicy.EMPTY, MissingCodePolicy.ABORT])
def test_unknown_policy_unchanged(tmp_path, policy):
    repo, item = setup(tmp_path)
    config = source_config(tmp_path, [item.code, 'CORR-BBBBBBBBBBBB'], missing=policy)
    destination = tmp_path / 'output.csv'
    if policy is MissingCodePolicy.ABORT:
        with pytest.raises(MissingCodeError):
            restore_csv(config, destination, repo)
        assert not destination.exists()
    else:
        restore_csv(config, destination, repo)
        assert read(destination)[2] == ['']


@pytest.mark.parametrize('ambiguous', [False, True])
def test_inspection_distinguishes_canonical_original_and_generic_components(tmp_path, ambiguous):
    repo, item = setup(tmp_path)
    if ambiguous:
        repo.composite_repository().upsert_composite_mapping(replace(item, original_values=('GUSTAVO', '99999999999')))
    result = ConsultantService(lambda: repo).consult(item.code)[0]
    assert result.status is ConsultationStatus.FOUND and isinstance(result.mapping, CompositeMapping)
    rendered = _render_result(result)
    assert 'Tipo: Composite' in rendered
    assert f'Valor canônico: {composite_text(item.canonical_values)}' in rendered
    assert f'Variação original 1:' in rendered
    assert 'Componente 1 —' in rendered and 'Componente 2 —' in rendered
    assert 'Cabeçalho de origem' not in rendered
    assert 'Variações originais observadas: ' + ('2' if ambiguous else '1') in rendered
    assert ('ambiguidade' in rendered) == ambiguous


@pytest.mark.parametrize('mutation', [
    'DELETE FROM composite_variations',
    "UPDATE composite_mappings SET encrypted_value = X'00'",
    "UPDATE composite_variations SET encrypted_value = X'00'",
    'UPDATE composite_mappings SET component_count = 3',
    'UPDATE composite_mappings SET payload_version = 2',
    'UPDATE composite_variations SET occurrence_count = 0',
    "UPDATE composite_variations SET identifier = 'invalid'",
])
def test_corruption_fails_closed_without_plaintext(tmp_path, mutation):
    repo, item = setup(tmp_path)
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute('PRAGMA ignore_check_constraints = ON')
        connection.execute(mutation)
    destination = tmp_path / 'output.csv'
    with pytest.raises(RestorationSecurityError) as error:
        restore_csv(source_config(tmp_path, [item.code]), destination, repo)
    assert not destination.exists() and not list(tmp_path.glob('.*.tmp'))
    result = ConsultantService(lambda: repo).consult(item.code)[0]
    assert result.status is ConsultationStatus.RECOVERY_FAILED
    for value in (*item.canonical_values, *item.original_values):
        assert value not in str(error.value) and value not in _render_result(result)


@pytest.mark.parametrize('kind', ['malformed', 'arity', 'version'])
def test_authenticated_invalid_payload_is_rejected(tmp_path, kind):
    from data_mask_studio.vault.composite_payload import composite_aad, encode_tuple
    repo, item = setup(tmp_path)
    cipher = VaultCipher(AES)
    with sqlite3.connect(repo.database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute('SELECT * FROM composite_mappings').fetchone())
        if kind == 'arity':
            variation = connection.execute('SELECT * FROM composite_variations').fetchone()
            encrypted = cipher.encrypt_payload(encode_tuple(('A', 'B', 'C')), composite_aad(row, variation['identifier']))
            connection.execute('UPDATE composite_variations SET encrypted_value=?, nonce=?', (encrypted.ciphertext, encrypted.nonce))
        else:
            payload = b'PRIVATE_INVALID_PAYLOAD'
            if kind == 'version':
                row['payload_version'] = 2
                connection.execute('UPDATE composite_mappings SET payload_version=2')
            encrypted = cipher.encrypt_payload(payload, composite_aad(row))
            connection.execute('UPDATE composite_mappings SET encrypted_value=?, nonce=?', (encrypted.ciphertext, encrypted.nonce))
    with pytest.raises(RestorationSecurityError) as error:
        restore_csv(source_config(tmp_path, [item.code]), tmp_path / 'output.csv', repo)
    assert 'PRIVATE' not in str(error.value)


def test_backup_environment_round_trip_restores_composite(tmp_path):
    from data_mask_studio.backup import EnvironmentPaths, create_backup, restore_backup
    from data_mask_studio.security import LocalKeyProvider
    repo, item = setup(tmp_path)
    def paths(directory):
        return EnvironmentPaths(directory, directory / 'secret.key', directory / 'vault_key.dpapi',
                                directory / 'vault.db', directory / 'profiles.json')
    class Provider:
        def __init__(self, key): self.key = key
        def get_key(self): return self.key
    class Protector:
        def protect(self, data): return b'TEST-ONLY:' + data
        def unprotect(self, data):
            assert data.startswith(b'TEST-ONLY:')
            return data[len(b'TEST-ONLY:'):]
    password = 'senha de teste suficientemente longa'
    backup = tmp_path / 'test.dmsbackup'
    create_backup(backup, password, password, paths=paths(tmp_path),
                  hmac_key_provider=Provider(HMAC), vault_key_provider=Provider(AES))
    target = paths(tmp_path / 'restored-environment')
    restore_backup(backup, password, paths=target, protector=Protector())
    key = LocalKeyProvider(target.directory, Protector(), key_file_name=target.vault_key_path.name).get_key()
    restored_repo = VaultRepository(target.vault_database_path, VaultCipher(key), read_only=True)
    result = restore_csv(source_config(tmp_path, [item.code]), tmp_path / 'output.csv', restored_repo)
    assert read(result.output_path)[1] == [composite_text(item.original_values)]
    assert restored_repo.get_mapping_for_inspection(item.code) == repo.get_mapping_for_inspection(item.code)


def test_cold_consultation_has_no_processing_import_dependency(tmp_path):
    import subprocess
    import sys
    repo, item = setup(tmp_path)
    program = '''
import sys
from data_mask_studio.consultant import ConsultantService, ConsultationStatus
from data_mask_studio.vault import VaultRepository, VaultCipher
repo = VaultRepository(sys.argv[1], VaultCipher(b'V' * 32), read_only=True)
result = ConsultantService(lambda: repo).consult(sys.argv[2])[0]
assert result.status is ConsultationStatus.FOUND
assert result.mapping.component_count == 2
'''
    subprocess.run([sys.executable, '-c', program, str(repo.database_path), item.code], check=True, capture_output=True)


def test_consultant_widget_displays_composite_without_automatic_copy(tmp_path):
    from data_mask_studio.app import create_application
    from data_mask_studio.gui.consultant_widget import ConsultantWidget
    app = create_application([])
    repo, item = setup(tmp_path)
    widget = ConsultantWidget(lambda: repo)
    app.clipboard().setText('sentinel')
    try:
        widget.codes_input.setPlainText(item.code)
        widget.consult()
        assert 'Tipo: Composite' in widget.results_output.toPlainText()
        assert 'Valor canônico:' in widget.results_output.toPlainText()
        assert app.clipboard().text() == 'sentinel'
        widget.clear_consultation()
        assert not widget.results_output.toPlainText()
    finally:
        app.clipboard().clear()
        widget.close()
        widget.deleteLater()
        app.processEvents()
