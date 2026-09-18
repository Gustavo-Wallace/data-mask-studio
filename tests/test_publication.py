import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv, CSVAnonymizationError, ProcessingCancelled
from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.publication import Publication, PublicationError, recover_publications, _signature
from data_mask_studio.vault import VaultRepository, VaultCipher

KEY = b"H" * 32


def case(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("Name\nAlice\nAlice\n", encoding="utf-8")
    destination = tmp_path / "out.csv"
    repo = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    kwargs = dict(encoding="utf-8", delimiter=",", configurations=[ColumnConfig("Name", anonymize=True, prefix="NAME")], secret_key=KEY, vault_repository=repo)
    return source, destination, repo, kwargs


def journals(tmp_path):
    return list((tmp_path / "publication-operations").glob(".dms-operation-*.json"))


def counts(repo):
    with sqlite3.connect(repo.database_path) as connection:
        return tuple(tuple(connection.execute(f"SELECT * FROM {table}")) for table in
                     ("vault_mappings", "vault_variations", "composite_mappings", "composite_variations"))


def fail_publish(*args):
    raise PermissionError("synthetic failure")


def pending(tmp_path, monkeypatch):
    import data_mask_studio.csv_tools.csv_anonymizer as module
    source, destination, repo, kwargs = case(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(module, "publish", fail_publish)
        with pytest.raises(CSVAnonymizationError, match="pendente"):
            anonymize_csv(source, destination, **kwargs)
    assert repo.count() == 1
    assert not destination.exists()
    assert len(journals(tmp_path)) == 1
    return source, destination, repo, kwargs


def test_happy_path_and_recovery_idempotence(tmp_path, monkeypatch):
    source, destination, repo, kwargs = pending(tmp_path, monkeypatch)
    before = counts(repo)
    assert recover_publications(repo.database_path, KEY) == 1
    published = destination.read_bytes()
    assert recover_publications(repo.database_path, KEY) == 0
    assert counts(repo) == before
    assert b"Alice" not in published
    assert source.read_text() == "Name\nAlice\nAlice\n"
    assert not journals(tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
    anonymize_csv(source, destination, **kwargs, overwrite=True)
    assert destination.read_bytes() == published
    assert not journals(tmp_path)


@pytest.mark.parametrize("failure", ["fsync", "prepare", "cancel"])
def test_before_commit_rolls_back_and_cleans(tmp_path, monkeypatch, failure):
    source, destination, repo, kwargs = case(tmp_path)
    if failure == "fsync":
        monkeypatch.setattr(os, "fsync", fail_publish)
    elif failure == "prepare":
        original = Publication.prepare
        def prepare(self, path):
            original(self, path)
            raise OSError("after journal durable, before commit")
        monkeypatch.setattr(Publication, "prepare", prepare)
    else:
        kwargs["should_cancel"] = lambda: True
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs)
    assert repo.count() == 0
    assert not destination.exists()
    assert not journals(tmp_path)
    assert not list(tmp_path.glob("*.tmp"))


def test_crash_between_commit_and_confirmation_is_ambiguous(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    monkeypatch.setattr(Publication, "committed", fail_publish)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs)
    before = counts(repo)
    assert repo.count() == 1
    assert json.loads(journals(tmp_path)[0].read_bytes())["data"]["state"] == "READY"
    with pytest.raises(PublicationError, match="indeterminada"):
        recover_publications(repo.database_path, KEY)
    assert counts(repo) == before
    assert list(tmp_path.glob("*.tmp"))
    assert not destination.exists()


def test_published_cleanup_failure_recognized_without_reprocessing(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    monkeypatch.setattr(Publication, "complete", fail_publish)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs)
    original = destination.read_bytes()
    before = counts(repo)
    assert recover_publications(repo.database_path, KEY) == 1
    assert destination.read_bytes() == original
    assert counts(repo) == before
    assert recover_publications(repo.database_path, KEY) == 0


def test_no_overwrite_is_atomic_even_after_last_exists_check(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    link = os.link
    def competing_link(temp, final):
        Path(final).write_bytes(b"other process")
        link(temp, final)
    monkeypatch.setattr(os, "link", competing_link)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs)
    assert destination.read_bytes() == b"other process"
    with pytest.raises(PublicationError):
        recover_publications(repo.database_path, KEY)
    assert destination.read_bytes() == b"other process"
    assert journals(tmp_path)


@pytest.mark.parametrize("change", ["missing", "corrupt"])
def test_bad_temp_retains_evidence(tmp_path, monkeypatch, change):
    _, destination, repo, _ = pending(tmp_path, monkeypatch)
    temp = next(tmp_path.glob("*.tmp"))
    if change == "missing":
        temp.unlink()
    else:
        temp.write_bytes(b"corrupt")
    with pytest.raises(PublicationError):
        recover_publications(repo.database_path, KEY)
    assert not destination.exists()
    assert journals(tmp_path)


@pytest.mark.parametrize("change", ["json", "signature", "version", "path", "id", "temp", "relative", "state"])
def test_invalid_journal_cannot_publish(tmp_path, monkeypatch, change):
    _, destination, repo, _ = pending(tmp_path, monkeypatch)
    path = journals(tmp_path)[0]
    envelope = json.loads(path.read_bytes())
    if change == "json":
        path.write_bytes(b"{")
    else:
        data = envelope["data"]
        if change == "signature":
            envelope["signature"] = "0" * 64
        elif change == "path":
            data["destination"] = str(tmp_path / "arbitrary.csv")
        else:
            if change == "version": data["operation_journal_version"] = 2
            if change == "id": data["id"] = "../arbitrary"
            if change == "temp": data["temp"] = str(tmp_path / "other-app.tmp")
            if change == "relative": data["destination"] = "../arbitrary.csv"
            if change == "state": data["state"] = "FUTURE"
            # Authenticated invalid metadata must still fail schema validation.
            envelope["signature"] = _signature(data, KEY)
        path.write_text(json.dumps(envelope))
    with pytest.raises(PublicationError) as error:
        recover_publications(repo.database_path, KEY)
    assert "Alice" not in str(error.value)
    assert not destination.exists()
    assert not (tmp_path / "arbitrary.csv").exists()
    assert journals(tmp_path)


def test_journal_has_only_operational_metadata_and_cleanup_does_not_claim_temp(tmp_path, monkeypatch):
    from data_mask_studio.maintenance.temporary_cleanup import locate_temporaries
    _, _, repo, _ = pending(tmp_path, monkeypatch)
    content = journals(tmp_path)[0].read_bytes()
    assert b"Alice" not in content and KEY not in content
    assert b"NAME-" not in content
    assert not locate_temporaries(tmp_path, [tmp_path], now=10**12)


def test_startup_finishes_publication(tmp_path, monkeypatch):
    from data_mask_studio.vault.initializer import initialize_existing_vault
    _, destination, repo, _ = pending(tmp_path, monkeypatch)
    class Provider:
        def __init__(self, key): self.key = key
        def get_key(self): return self.key
    before = counts(repo)
    assert initialize_existing_vault(repo.database_path, Provider(b"V" * 32), Provider(KEY))
    assert destination.exists()
    assert counts(repo) == before


def test_startup_validates_both_keys_before_recovery(tmp_path, monkeypatch):
    from data_mask_studio.vault.initializer import initialize_existing_vault
    from data_mask_studio.security.key_provider import KeyProviderError
    _, destination, repo, _ = pending(tmp_path, monkeypatch)
    class Hmac:
        def get_key(self): return KEY
    class MissingAES:
        def get_key(self): raise KeyProviderError("Chave ausente.")
    with pytest.raises(KeyProviderError):
        initialize_existing_vault(repo.database_path, MissingAES(), Hmac())
    assert not destination.exists() and journals(tmp_path)


def test_cancel_after_commit_does_not_interrupt_publication(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    cancelled = False
    original = Publication.committed
    def committed(self):
        nonlocal cancelled
        original(self)
        cancelled = True
    monkeypatch.setattr(Publication, "committed", committed)
    anonymize_csv(source, destination, **kwargs, should_cancel=lambda: cancelled)
    assert destination.exists() and repo.count() == 1


def test_cancel_after_journal_before_commit_rolls_back(tmp_path, monkeypatch):
    source, destination, repo, kwargs = case(tmp_path)
    cancelled = False
    original = Publication.prepare
    def prepared(self, path):
        nonlocal cancelled
        original(self, path)
        cancelled = True
    monkeypatch.setattr(Publication, "prepare", prepared)
    with pytest.raises(ProcessingCancelled):
        anonymize_csv(source, destination, **kwargs, should_cancel=lambda: cancelled)
    assert repo.count() == 0
    assert not destination.exists()
    assert not journals(tmp_path)
    assert not list(tmp_path.glob("*.tmp"))


def test_mixed_scalar_composite_recovery_preserves_all_counters(tmp_path, monkeypatch):
    from test_composite_csv import setup_case
    import data_mask_studio.csv_tools.csv_anonymizer as module
    source, destination, repo, kwargs = setup_case(tmp_path, [("Alice", "99999999999", "24")],
        [ColumnConfig("NOME", anonymize=True, prefix="NAME"), ColumnConfig("CPF"), ColumnConfig("IDADE")])
    with monkeypatch.context() as patch:
        patch.setattr(module, "publish", fail_publish)
        with pytest.raises(CSVAnonymizationError):
            anonymize_csv(source, destination, **kwargs)
    before = counts(repo)
    assert before[0] and before[2]
    recover_publications(repo.database_path, KEY)
    assert counts(repo) == before


@pytest.mark.parametrize("stage", ["temp_durable", "ready", "committed", "published"])
def test_process_crash_at_protocol_boundaries(tmp_path, stage):
    source, destination, repo, _ = case(tmp_path)
    program = '''
import os, sys
from pathlib import Path
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv
from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.vault import VaultRepository, VaultCipher
from data_mask_studio.publication import Publication
directory, stage = Path(sys.argv[1]), sys.argv[2]
method = 'prepare' if stage in ('temp_durable', 'ready') else 'committed' if stage == 'committed' else 'complete'
original = getattr(Publication, method)
def crash(self, *args):
    if stage in ('ready', 'committed'):
        original(self, *args)
    os._exit(73)
setattr(Publication, method, crash)
repo = VaultRepository(directory / 'vault.db', VaultCipher(b'V' * 32))
anonymize_csv(directory / 'source.csv', directory / 'out.csv', encoding='utf-8', delimiter=',', configurations=[ColumnConfig('Name', anonymize=True, prefix='NAME')], secret_key=b'H' * 32, vault_repository=repo)
'''
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path), stage], capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    before = counts(repo)
    if stage == "ready":
        assert repo.count() == 0  # SQLite rolled back, but journal alone cannot prove it.
        with pytest.raises(PublicationError, match="indeterminada"):
            recover_publications(repo.database_path, KEY)
        assert journals(tmp_path) and not destination.exists()
    elif stage == "temp_durable":
        assert repo.count() == 0 and not journals(tmp_path)
        assert recover_publications(repo.database_path, KEY) == 0
        assert list(tmp_path.glob("*.tmp"))  # Old/no-journal temps are never inferred.
    else:
        assert repo.count() == 1
        assert recover_publications(repo.database_path, KEY) == 1
        assert destination.exists()
        assert recover_publications(repo.database_path, KEY) == 0
    assert counts(repo) == before
    assert source.read_text() == "Name\nAlice\nAlice\n"


def test_overwrite_pending_with_existing_destination_is_not_destructive(tmp_path, monkeypatch):
    import data_mask_studio.csv_tools.csv_anonymizer as module
    source, destination, repo, kwargs = case(tmp_path)
    destination.write_bytes(b"previous output")
    monkeypatch.setattr(module, "publish", fail_publish)
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, **kwargs, overwrite=True)
    with pytest.raises(PublicationError, match="existente"):
        recover_publications(repo.database_path, KEY)
    assert destination.read_bytes() == b"previous output"
    assert journals(tmp_path) and list(tmp_path.glob("*.tmp"))


def test_startup_reports_pending_without_traceback(monkeypatch):
    import data_mask_studio.app as app
    messages = []
    monkeypatch.setattr(app, "MainWindow", lambda: (_ for _ in ()).throw(PublicationError("Operação pendente.")))
    monkeypatch.setattr(app.QMessageBox, "warning", lambda parent, title, text: messages.append(text))
    assert app.run() == 1
    assert messages == ["Operação pendente."]
