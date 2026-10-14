"""Deterministic, real-process profile read/modify/write races."""

import multiprocessing
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import pytest

from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.profiles import (
    ProfileRepository, ProfileService, ProfileStorageError, ProfileValidationError,
)
from data_mask_studio.profiles.locking import profile_write_lock
import data_mask_studio.profiles.repository as repository_module


def configurations(prefix="ORIGINAL"):
    return [ColumnConfig("Synthetic", True, prefix)]


def apply_change(service, operation):
    kind, value, argument = operation
    if kind == "create":
        return service.create(value, configurations(argument))
    if kind == "update":
        return service.update(value, configurations(argument))
    if kind == "rename":
        return service.rename(value, argument)
    if kind == "delete":
        return service.delete(value)
    raise AssertionError("Unknown synthetic operation")


def controlled_change(path, operation, channel):
    # Spawned children inherit no real LOCALAPPDATA and never use default paths.
    os.environ["LOCALAPPDATA"] = str(path.parent / "isolated-appdata")

    class ControlledRepository(ProfileRepository):
        def load(self):
            result = super().load()
            channel.send(("loaded",))
            assert channel.recv() == "continue"
            return result

    try:
        result = apply_change(ProfileService(ControlledRepository(path)), operation)
        channel.send(("success", result.identifier if result is not None else None))
    except Exception as error:
        channel.send(("error", type(error).__name__, str(error)))
    finally:
        channel.close()


def receive(channel):
    assert channel.poll(10), "Synthetic child process did not reach its checkpoint"
    return channel.recv()


@contextmanager
def child_process(target, args):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=target, args=(*args, child))
    process.start()
    child.close()
    try:
        yield process, parent
    finally:
        process.join(5)
        if process.is_alive():
            process.terminate()  # Only the synthetic test-owned child.
            process.join(5)
        parent.close()
        assert not process.is_alive()


def interleave(path, first, second, *, first_saves_first=False):
    with child_process(controlled_change, (path, first)) as (process_a, channel_a):
        assert receive(channel_a) == ("loaded",)
        with child_process(controlled_change, (path, second)) as (process_b, channel_b):
            assert process_a.pid != process_b.pid != os.getpid()
            second_checkpoint = receive(channel_b)
            if second_checkpoint == ("loaded",):
                # Pre-fix: both writers loaded the old document successfully.
                if first_saves_first:
                    channel_a.send("continue")
                    first_result = receive(channel_a)
                    channel_b.send("continue")
                    second_result = receive(channel_b)
                else:
                    channel_b.send("continue")
                    second_result = receive(channel_b)
                    channel_a.send("continue")
                    first_result = receive(channel_a)
            else:
                # Safe fail-fast contention must occur BEFORE the second load.
                assert second_checkpoint[:2] == ("error", "ProfileStorageError")
                second_result = second_checkpoint
                channel_a.send("continue")
                first_result = receive(channel_a)
            process_a.join(5)
            process_b.join(5)
            assert process_a.exitcode == process_b.exitcode == 0
            return first_result, second_result


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    return ProfileService(ProfileRepository(tmp_path / "profiles.json"))


def successful_identifiers(results):
    return {result[1] for result in results if result[0] == "success" and result[1]}


def uncontended_change(path, operation):
    with child_process(controlled_change, (path, operation)) as (process, channel):
        assert receive(channel) == ("loaded",)
        channel.send("continue")
        result = receive(channel)
        process.join(5)
        assert process.exitcode == 0
        return result


def test_concurrent_creates_never_lose_an_acknowledged_profile(service):
    first = ("create", "First synthetic profile", "FIRST")
    second = ("create", "Second synthetic profile", "SECOND")
    results = interleave(service.repository.path, first, second)
    persisted = {profile.identifier for profile in service.list_profiles()}
    assert successful_identifiers(results) <= persisted
    if results[1][0] == "error":
        apply_change(service, second)  # Explicit retry reads the current document.
    assert {profile.name for profile in service.list_profiles()} == {first[1], second[1]}


def test_concurrent_same_name_creates_resolve_against_current_document(service):
    first = ("create", "Same synthetic profile", "FIRST")
    second = ("create", "same synthetic profile", "SECOND")
    results = interleave(service.repository.path, first, second)
    persisted = service.list_profiles()
    assert successful_identifiers(results) <= {profile.identifier for profile in persisted}
    assert len(persisted) == 1
    if results[1][0] == "error":
        with pytest.raises(ProfileValidationError, match="Já existe"):
            apply_change(service, second)


def test_concurrent_create_and_update_preserve_both_changes(service):
    original = service.create("Existing synthetic profile", configurations())
    first = ("update", original.identifier, "UPDATED")
    second = ("create", "Additional synthetic profile", "ADDITIONAL")
    results = interleave(service.repository.path, first, second)
    profiles = {profile.identifier: profile for profile in service.list_profiles()}
    assert successful_identifiers(results) <= profiles.keys()
    assert profiles[original.identifier].columns[0].prefix == "UPDATED"
    if results[1][0] == "error":
        apply_change(service, second)
    assert len(service.list_profiles()) == 2


def test_concurrent_updates_of_different_profiles_do_not_erase_each_other(service):
    a = service.create("First synthetic profile", configurations())
    b = service.create("Second synthetic profile", configurations())
    results = interleave(service.repository.path,
                         ("update", a.identifier, "FIRST"), ("update", b.identifier, "SECOND"))
    profiles = {profile.identifier: profile for profile in service.list_profiles()}
    assert profiles[a.identifier].columns[0].prefix == "FIRST"
    if results[1][0] == "success":
        assert profiles[b.identifier].columns[0].prefix == "SECOND"
    else:
        service.update(b.identifier, configurations("SECOND"))
    profiles = {profile.identifier: profile for profile in service.list_profiles()}
    assert profiles[a.identifier].columns[0].prefix == "FIRST"
    assert profiles[b.identifier].columns[0].prefix == "SECOND"


@pytest.mark.parametrize("second_kind", ["rename", "update"])
def test_delete_racing_with_mutation_cannot_resurrect_deleted_profile(service, second_kind):
    profile = service.create("Deleted synthetic profile", configurations())
    result_a, result_b = interleave(
        service.repository.path, ("delete", profile.identifier, ""),
        (second_kind, profile.identifier, "Changed" if second_kind == "rename" else "CHANGED"),
        first_saves_first=True,
    )
    assert result_a[0] == "success"
    assert service.list_profiles() == []
    assert result_b[:2] == ("error", "ProfileStorageError")
    with pytest.raises(ProfileValidationError, match="não existe"):
        apply_change(service, (second_kind, profile.identifier, "Changed"))


def test_concurrent_rename_and_delete_of_distinct_profiles_keep_both_changes(service):
    a = service.create("Renamed synthetic profile", configurations())
    b = service.create("Deleted synthetic profile", configurations())
    results = interleave(service.repository.path,
                         ("rename", a.identifier, "New synthetic name"), ("delete", b.identifier, ""))
    profiles = {profile.identifier: profile for profile in service.list_profiles()}
    assert profiles[a.identifier].name == "New synthetic name"
    if results[1][0] == "success":
        assert b.identifier not in profiles
    else:
        service.delete(b.identifier)
    assert [profile.name for profile in service.list_profiles()] == ["New synthetic name"]


@pytest.mark.parametrize("failure_point", ["dump", "fsync", "replace"])
def test_failed_write_preserves_document_and_releases_lock(service, monkeypatch, failure_point):
    profile = service.create("Unchanged synthetic profile", configurations())
    previous = service.repository.path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("Synthetic disk failure")

    with monkeypatch.context() as scoped:
        target = repository_module.json if failure_point == "dump" else repository_module.os
        scoped.setattr(target, failure_point, fail)
        with pytest.raises(ProfileStorageError, match="salvar"):
            service.update(profile.identifier, configurations("CHANGED"))
    assert service.repository.path.read_bytes() == previous
    assert service.list_profiles() == [profile]
    assert not list(service.repository.path.parent.glob(".profiles.json.*.tmp"))
    result = uncontended_change(service.repository.path, ("create", "After failure", "AFTER"))
    assert result[0] == "success"  # Another PROCESS can acquire after the exception.
    assert len(service.list_profiles()) == 2


@pytest.mark.parametrize("kind", ["create", "update", "rename", "delete"])
def test_validation_exceptions_release_profile_lock(service, kind):
    profile = service.create("Existing synthetic profile", configurations())
    other = service.create("Other synthetic profile", configurations())
    before = service.repository.path.read_bytes()
    operations = {
        "create": ("create", profile.name, "ORIGINAL"),
        "update": ("update", profile.identifier, ""),
        "rename": ("rename", profile.identifier, other.name),
        "delete": ("delete", "missing-synthetic-identifier", ""),
    }
    with pytest.raises(ProfileValidationError):
        apply_change(service, operations[kind])
    assert service.repository.path.read_bytes() == before
    assert uncontended_change(service.repository.path, ("create", "After exception", "AFTER"))[0] == "success"


def hold_write_lock(path, channel):
    os.environ["LOCALAPPDATA"] = str(path.parent / "isolated-appdata")
    with profile_write_lock(path):
        channel.send(("locked",))
        assert channel.recv() == "release"
    channel.send(("released",))
    assert channel.recv() == "exit"  # Stay alive while the parent verifies release.
    channel.close()


def test_lock_release_does_not_require_process_exit_or_sidecar_deletion(service):
    path = service.repository.path
    with child_process(hold_write_lock, (path,)) as (process, channel):
        assert receive(channel) == ("locked",)
        with pytest.raises(ProfileStorageError, match="outra instância"):
            service.create("Blocked synthetic profile", configurations())
        assert not path.exists()
        channel.send("release")
        assert receive(channel) == ("released",)
        assert process.is_alive()
        assert path.with_name(f".{path.name}.lock").is_file()
        service.create("After release", configurations())
        channel.send("exit")
        process.join(5)
        assert process.exitcode == 0


def test_process_termination_releases_profile_lock_without_stale_lockfile(service):
    path = service.repository.path
    with child_process(hold_write_lock, (path,)) as (process, channel):
        assert receive(channel) == ("locked",)
        with pytest.raises(ProfileStorageError):
            service.create("Blocked synthetic profile", configurations())
        process.terminate()
        process.join(5)
        assert process.exitcode is not None and process.exitcode != 0
        assert path.with_name(f".{path.name}.lock").is_file()
        service.create("After process death", configurations())
        assert [profile.name for profile in service.list_profiles()] == ["After process death"]


@pytest.mark.parametrize("same_document", [True, False])
def test_thread_contention_is_per_document_not_per_environment(service, same_document):
    path = service.repository.path
    target = path if same_document else path.with_name("other-profiles.json")
    results = []

    def mutate():
        try:
            result = ProfileService(ProfileRepository(target)).create("Synthetic profile", configurations())
            results.append(result)
        except ProfileStorageError as error:
            results.append(error)

    with profile_write_lock(path):
        thread = Thread(target=mutate)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        assert len(results) == 1
        assert isinstance(results[0], ProfileStorageError) == same_document
    service.create("Owner can write after release", configurations())


def test_service_and_repository_save_share_reentrant_lock_for_path_aliases(service):
    path = service.repository.path
    path.parent.joinpath("alias").mkdir()
    alias = path.parent / "alias" / ".." / path.name.upper()
    with profile_write_lock(path):
        created = ProfileService(ProfileRepository(alias)).create("Nested synthetic profile", configurations())
        assert service.list_profiles() == [created]  # Readers are not write-locked.
    assert service.list_profiles() == [created]


def test_direct_repository_save_cannot_bypass_an_active_service_mutation(service):
    original = service.create("Existing synthetic profile", configurations())
    with child_process(controlled_change, (service.repository.path,
                       ("rename", original.identifier, "Changed synthetic name"))) as (process, channel):
        assert receive(channel) == ("loaded",)
        with pytest.raises(ProfileStorageError, match="outra instância"):
            service.repository.save([])
        assert service.list_profiles() == [original]
        channel.send("continue")
        assert receive(channel)[0] == "success"
        process.join(5)
        assert process.exitcode == 0
    assert service.list_profiles()[0].name == "Changed synthetic name"
