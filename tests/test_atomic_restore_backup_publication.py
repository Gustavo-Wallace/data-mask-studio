"""Force a competing writer at the filesystem publication boundary."""
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from test_backup import prepare_environment, PASSWORD
from test_restoration import make_repository, configuration, CPF_CODE
from data_mask_studio.backup import create_backup, BackupError, validate_backup
from data_mask_studio.restoration import restore_csv, RestorationError
from data_mask_studio.html_restoration import inspect_html, restore_html, HTMLRestorationError


@pytest.fixture(params=['csv', 'html', 'backup'])
def operation(request, tmp_path):
    if request.param == 'backup':
        paths, hmac, aes, _ = prepare_environment(tmp_path)
        destination = tmp_path / 'output.dmsbackup'
        sources = [paths.hmac_key_path, paths.vault_key_path, paths.profiles_path]
        database = paths.vault_database_path
        def run(overwrite=False):
            return create_backup(destination, PASSWORD, PASSWORD, paths=paths,
                                 hmac_key_provider=hmac, vault_key_provider=aes, overwrite=overwrite)
        error = BackupError
    else:
        repository = make_repository(tmp_path)
        database = repository.database_path
        source = tmp_path / f'input.{request.param}'
        destination = tmp_path / f'output.{request.param}'
        source.write_text(f'CPF;Nome;Tipo\n{CPF_CODE};Ana;test\n' if request.param == 'csv'
                          else f'<html><body><p>{CPF_CODE}</p></body></html>', encoding='utf-8')
        sources = [source]
        def run(overwrite=False):
            if request.param == 'csv':
                return restore_csv(configuration(source), destination, repository, overwrite=overwrite)
            return restore_html(inspect_html(source), destination, repository, overwrite=overwrite)
        error = RestorationError if request.param == 'csv' else HTMLRestorationError
    before = [p.read_bytes() for p in sources]
    def snapshot():
        with sqlite3.connect(database) as connection:
            return tuple(connection.iterdump())
    vault_before = snapshot()
    unrelated = tmp_path / 'other-app.tmp'
    unrelated.write_bytes(b'not owned by DMS')
    yield run, destination, error
    assert [p.read_bytes() for p in sources] == before
    assert snapshot() == vault_before
    assert unrelated.read_bytes() == b'not owned by DMS'
    assert list(tmp_path.glob('.*.tmp')) == []
    assert list(tmp_path.glob('.dms-backup-*')) == []


@pytest.mark.parametrize('overwrite', [False, True])
def test_competing_destination_at_publication(operation, monkeypatch, overwrite):
    run, destination, error = operation
    concurrent = b'CONCURRENT FILE MUST SURVIVE'
    calls = []
    for name in ('replace', 'link'):
        original = getattr(os, name)
        def publish(source, target, *args, _original=original, **kwargs):
            if Path(target) == destination:
                assert not destination.exists()
                destination.write_bytes(concurrent)
                calls.append(Path(source))
            return _original(source, target, *args, **kwargs)
        monkeypatch.setattr(os, name, publish)
    if overwrite:
        run(overwrite=True)
        assert destination.read_bytes() != concurrent
    else:
        with pytest.raises(error):
            run()
        assert destination.read_bytes() == concurrent
    assert len(calls) == 1
    assert not calls[0].exists()


@pytest.mark.parametrize('existing,overwrite', [(False, False), (True, False), (True, True)])
def test_normal_publication(operation, existing, overwrite):
    run, destination, error = operation
    if existing:
        destination.write_bytes(b'previous')
    if existing and not overwrite:
        with pytest.raises(error):
            run()
        assert destination.read_bytes() == b'previous'
    else:
        run(overwrite=overwrite)
        assert destination.read_bytes() != b'previous'
        if destination.suffix == '.dmsbackup':
            assert validate_backup(destination, PASSWORD).mapping_count == 1


def test_unavailable_atomic_link_fails_closed(operation, monkeypatch):
    run, destination, error = operation
    def unavailable(*args, **kwargs):
        raise OSError('Unsupported atomic link')
    def forbidden(*args, **kwargs):
        pytest.fail('No check-and-replace fallback is allowed')
    monkeypatch.setattr(os, 'link', unavailable)
    monkeypatch.setattr(os, 'replace', forbidden)
    with pytest.raises(error):
        run()
    assert not destination.exists()


def test_replacement_failure_preserves_existing_file(operation, monkeypatch):
    run, destination, error = operation
    destination.write_bytes(b'existing file')
    def unavailable(*args, **kwargs):
        raise PermissionError('Publication unavailable')
    monkeypatch.setattr(os, 'replace', unavailable)
    with pytest.raises(error):
        run(overwrite=True)
    assert destination.read_bytes() == b'existing file'


@pytest.mark.parametrize('kind', ['csv', 'html'])
@pytest.mark.parametrize('alias', [False, True])
def test_restoration_cannot_publish_over_source(tmp_path, kind, alias):
    repository = make_repository(tmp_path)
    source = tmp_path / f'input.{kind}'
    source.write_text(f'CPF;Nome;Tipo\n{CPF_CODE};Ana;test\n' if kind == 'csv'
                      else f'<p>{CPF_CODE}</p>', encoding='utf-8')
    before = source.read_bytes()
    destination = source
    if alias:
        destination = tmp_path / f'alias.{kind}'
        os.link(source, destination)
    error = RestorationError if kind == 'csv' else HTMLRestorationError
    with pytest.raises(error):
        if kind == 'csv':
            restore_csv(configuration(source), destination, repository, overwrite=True)
        else:
            restore_html(inspect_html(source), destination, repository, overwrite=True)
    assert source.read_bytes() == destination.read_bytes() == before


@pytest.mark.parametrize('alias', [False, True])
def test_backup_cannot_replace_environment_source(tmp_path, alias):
    paths, hmac, aes, _ = prepare_environment(tmp_path)
    destination = tmp_path / 'source.dmsbackup'
    if alias:
        os.link(paths.profiles_path, destination)
    else:
        destination.write_bytes(paths.profiles_path.read_bytes())
        paths = replace(paths, profiles_path=destination)
    before = destination.read_bytes()
    with pytest.raises(BackupError, match='ambiente local'):
        create_backup(destination, PASSWORD, PASSWORD, paths=paths,
                      hmac_key_provider=hmac, vault_key_provider=aes, overwrite=True)
    assert destination.read_bytes() == before
