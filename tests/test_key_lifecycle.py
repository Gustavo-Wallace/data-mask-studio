import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from data_mask_studio.security.key_provider import KeyProviderError, LocalKeyProvider
from data_mask_studio.security.key_provider import MissingKeyError
from data_mask_studio.vault.key_provider import VaultKeyProvider
from data_mask_studio.vault import VaultCipher, VaultRepository
from data_mask_studio.vault.initializer import initialize_existing_vault


class Protector:
    def protect(self, data):
        return b"test-protected:" + data[::-1]

    def unprotect(self, data):
        if not data.startswith(b"test-protected:"):
            raise RuntimeError("synthetic DPAPI failure")
        return data[len(b"test-protected:"):][::-1]


@pytest.mark.parametrize("provider_type", [LocalKeyProvider, VaultKeyProvider])
@pytest.mark.parametrize("marker", ["vault.db", "vault.db-wal", "vault.db-shm", "vault.db-journal"])
def test_persisted_state_prevents_missing_key_generation(tmp_path, provider_type, marker):
    (tmp_path / marker).write_bytes(b"existing")
    provider = provider_type(tmp_path, Protector())
    with pytest.raises(KeyProviderError, match="Restaure um backup") as error:
        provider.get_key()
    assert str(tmp_path) not in str(error.value)
    assert not provider.key_path.exists()
    assert (tmp_path / marker).read_bytes() == b"existing"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("provider_type", [LocalKeyProvider, VaultKeyProvider])
@pytest.mark.parametrize("content", [b"", b"corrupt", Protector().protect(b"short"), Protector().protect(b"x" * 33)])
def test_invalid_key_is_never_replaced(tmp_path, provider_type, content):
    provider = provider_type(tmp_path, Protector())
    provider.key_path.write_bytes(content)
    with pytest.raises(KeyProviderError):
        provider.get_key()
    assert provider.key_path.read_bytes() == content
    assert not list(tmp_path.glob("*.tmp"))


def test_partial_environment_completes_and_existing_environment_reuses_bytes(tmp_path):
    hmac = LocalKeyProvider(tmp_path, Protector())
    aes = VaultKeyProvider(tmp_path, Protector())
    hmac_key = hmac.get_key()
    original = hmac.key_path.read_bytes()
    assert not aes.key_path.exists()
    aes_key = aes.get_key()
    assert len(hmac_key) == len(aes_key) == 32
    assert aes_key != hmac_key
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(aes_key))
    assert repository.count() == 0
    stored = aes.key_path.read_bytes()
    assert hmac.get_key() == hmac_key
    assert aes.get_key() == aes_key
    assert hmac.key_path.read_bytes() == original
    assert aes.key_path.read_bytes() == stored


@pytest.mark.parametrize("provider_type", [LocalKeyProvider, VaultKeyProvider])
@pytest.mark.parametrize("callers", [2, 8])
def test_concurrent_publication_losers_reread_winner(tmp_path, monkeypatch, provider_type, callers):
    barrier = Barrier(callers)
    real_link = os.link
    candidates = []
    losers = []

    def synchronized_link(source, destination):
        # Synchronize immediately before the REAL atomic operation, so every
        # participant has generated a different protected candidate.
        candidates.append(Path(source).read_bytes())
        barrier.wait(timeout=20)
        try:
            real_link(source, destination)
        except FileExistsError:
            losers.append(Path(source).read_bytes())
            raise

    monkeypatch.setattr(os, "link", synchronized_link)
    with ThreadPoolExecutor(max_workers=callers) as pool:
        results = list(pool.map(lambda _: provider_type(tmp_path, Protector()).get_key(), range(callers)))
    persisted = provider_type(tmp_path, Protector()).load_existing_key()
    assert results == [persisted] * callers
    assert len(set(candidates)) == callers
    assert len(losers) == callers - 1
    assert all(Protector().unprotect(candidate) != persisted for candidate in losers)
    assert not list(tmp_path.glob("*.tmp"))


def _process_create(directory, key_name, barrier, queue):
    class SynchronizedProtector(Protector):
        def protect(self, data):
            protected = super().protect(data)
            barrier.wait(timeout=30)
            return protected

    key = LocalKeyProvider(Path(directory), SynchronizedProtector(), key_file_name=key_name).get_key()
    queue.put(key)


@pytest.mark.parametrize("key_name", ["secret.key", "vault_key.dpapi"])
def test_independent_processes_converge(tmp_path, key_name):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [context.Process(target=_process_create, args=(str(tmp_path), key_name, barrier, queue)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        results = [queue.get(timeout=40) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        persisted = LocalKeyProvider(tmp_path, Protector(), key_file_name=key_name).load_existing_key()
        assert results == [persisted, persisted]
        assert not list(tmp_path.glob("*.tmp"))
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        queue.close()


@pytest.mark.parametrize("stage", ["fsync", "link"])
def test_publication_failure_never_leaves_partial_final_key(tmp_path, monkeypatch, stage):
    def fail(*args):
        raise OSError("synthetic failure")

    monkeypatch.setattr(os, stage, fail)
    provider = LocalKeyProvider(tmp_path, Protector())
    with pytest.raises(KeyProviderError):
        provider.get_key()
    assert not provider.key_path.exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("missing", ["secret.key", "vault_key.dpapi"])
def test_startup_rejects_missing_key_before_opening_vault(tmp_path, missing):
    hmac = LocalKeyProvider(tmp_path, Protector())
    aes = VaultKeyProvider(tmp_path, Protector())
    hmac.get_key()
    VaultRepository(tmp_path / "vault.db", VaultCipher(aes.get_key()))
    (tmp_path / missing).unlink()
    before = (tmp_path / "vault.db").read_bytes()
    with pytest.raises(KeyProviderError):
        initialize_existing_vault(tmp_path / "vault.db", aes, hmac)
    assert not (tmp_path / missing).exists()
    assert (tmp_path / "vault.db").read_bytes() == before


def test_default_factory_persists_both_keys_before_database(tmp_path, monkeypatch):
    from data_mask_studio.vault import defaults

    monkeypatch.setattr(defaults, "default_vault_directory", lambda: tmp_path)
    monkeypatch.setattr(defaults, "default_database_path", lambda: tmp_path / "vault.db")
    monkeypatch.setattr(defaults, "LocalKeyProvider", lambda path: LocalKeyProvider(path, Protector()))
    monkeypatch.setattr(defaults, "VaultKeyProvider", lambda path: VaultKeyProvider(path, Protector()))
    real_repository = defaults.VaultRepository

    def repository(path, cipher, **kwargs):
        assert len(LocalKeyProvider(tmp_path, Protector()).load_existing_key()) == 32
        assert len(VaultKeyProvider(tmp_path, Protector()).load_existing_key()) == 32
        return real_repository(path, cipher, **kwargs)

    monkeypatch.setattr(defaults, "VaultRepository", repository)
    assert defaults.create_default_vault_repository().count() == 0
    assert defaults.create_default_read_only_vault_repository().count() == 0


def test_load_existing_never_creates(tmp_path):
    provider = LocalKeyProvider(tmp_path, Protector())
    with pytest.raises(KeyProviderError):
        provider.load_existing_key()
    assert not provider.key_path.exists()


def test_winner_can_finish_environment_after_loser_observed_missing_key(tmp_path, monkeypatch):
    provider = LocalKeyProvider(tmp_path, Protector())
    winner = b"W" * 32
    load = provider.load_existing_key
    first = True

    def raced_load():
        nonlocal first
        if first:
            first = False
            provider.key_path.write_bytes(Protector().protect(winner))
            (tmp_path / "vault.db").write_bytes(b"persisted-state")
            raise MissingKeyError("synthetic stale observation")
        return load()

    monkeypatch.setattr(provider, "load_existing_key", raced_load)
    assert provider.get_key() == winner
    assert provider.key_path.read_bytes() == Protector().protect(winner)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_real_dpapi_round_trip_uses_only_isolated_files(tmp_path):
    hmac = LocalKeyProvider(tmp_path)
    aes = VaultKeyProvider(tmp_path)
    keys = (hmac.get_key(), aes.get_key())
    VaultRepository(tmp_path / "vault.db", VaultCipher(keys[1]))
    assert LocalKeyProvider(tmp_path).get_key() == keys[0]
    assert VaultKeyProvider(tmp_path).get_key() == keys[1]
    assert keys[0] != keys[1]
    assert all(len(key) == 32 for key in keys)
    assert keys[0] not in hmac.key_path.read_bytes()
    assert keys[1] not in aes.key_path.read_bytes()


def test_publication_contains_only_complete_protected_material(tmp_path, monkeypatch):
    real_link = os.link
    key = b"K" * 32
    monkeypatch.setattr("data_mask_studio.security.key_provider.secrets.token_bytes", lambda size: key)

    class OpaqueProtector:
        def protect(self, data):
            assert data == key
            return b"synthetic opaque DPAPI blob"

        def unprotect(self, data):
            assert data == b"synthetic opaque DPAPI blob"
            return key

    def checked_link(source, destination):
        assert Path(source).read_bytes() == b"synthetic opaque DPAPI blob"
        assert key not in Path(source).read_bytes()
        assert not Path(destination).exists()
        real_link(source, destination)

    monkeypatch.setattr(os, "link", checked_link)
    provider = LocalKeyProvider(tmp_path, OpaqueProtector())
    assert provider.get_key() == key
    assert provider.load_existing_key() == key
    assert not list(tmp_path.glob("*.tmp"))
