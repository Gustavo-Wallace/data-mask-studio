"""Durable A/B restore protocol; no user-supplied paths are read from the journal."""

import json
import os
import re
import shutil
import uuid
import ctypes
from ctypes import wintypes
from pathlib import Path

from data_mask_studio.environment import EnvironmentError, GENERATION_FILE, RESTORE_JOURNAL, guarded
from data_mask_studio.publication import fingerprint

NAMES = ("secret.key", "vault_key.dpapi", "vault.db", "profiles.json", GENERATION_FILE,
         "vault.db-wal", "vault.db-shm", "vault.db-journal")


def durable_replace(source: Path, destination: Path) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    move = kernel.MoveFileExW
    move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move.restype = wintypes.BOOL
    if not move(str(source), str(destination), 0x1 | 0x8):
        raise OSError("Não foi possível publicar um artefato da restauração.")


def durable_copy(source: Path, target: Path) -> None:
    with source.open("rb") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())


def durable_bytes(path: Path, data: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def validate_paths(paths) -> None:
    root = paths.directory.resolve()
    for attr, name in (("hmac_key_path", "secret.key"), ("vault_key_path", "vault_key.dpapi"),
                       ("vault_database_path", "vault.db"), ("profiles_path", "profiles.json")):
        path = Path(getattr(paths, attr))
        if path.is_symlink() or path.absolute() != root / name:
            raise EnvironmentError("Caminhos do ambiente incompatíveis com a restauração segura.")


class RestoreOperation:
    def __init__(self, directory: Path, protector):
        self.directory = directory.resolve()
        self.protector = protector
        self.identifier = uuid.uuid4().hex
        self.work = self.directory / f".dms-restore-operation-{self.identifier}"
        self.work.mkdir()
        (self.work / "A").mkdir()
        (self.work / "B").mkdir()
        self.data = {"restore_journal_version": 1, "id": self.identifier, "state": "STAGED",
                     "environment": str(self.directory)}

    def record(self, state: str) -> None:
        data = dict(self.data, state=state)
        content = self.protector.protect(json.dumps(data, sort_keys=True).encode())
        temp = self.work / "journal.write"
        durable_bytes(temp, content)
        durable_replace(temp, self.directory / RESTORE_JOURNAL)
        self.data = data

    def prepare(self) -> None:
        # All current files are stable under the exclusive lease. Preserve
        # even a damaged A exactly, including sidecars, for exception rollback.
        for name in NAMES:
            target = self.directory / name
            if target.is_symlink():
                raise EnvironmentError("Artefato do ambiente inválido.")
            if target.exists():
                durable_copy(target, self.work / "A" / name)
        self.data["generations"] = {
            which: {name: fingerprint(self.work / which / name) if (self.work / which / name).exists() else None
                    for name in NAMES} for which in ("A", "B")
        }
        self.record("STAGED")

    def install(self, which: str, replace=None) -> None:
        install_generation(self.directory, self.work, self.data, which, replace)

    def cleanup(self) -> None:
        # Journal remains until all generation files can be discarded. In
        # CLEANUP state recovery never installs A or B again.
        self.record("CLEANUP")
        cleanup(self.directory, self.work)


def install_generation(directory, work, data, which, replace=None):
    replace = replace or durable_replace
    manifest = data["generations"][which]
    # Validate the ENTIRE selected generation before the first active mutation.
    for name, expected in manifest.items():
        staged = work / which / name
        if staged.resolve().parent != (work / which).resolve() or (work / which).resolve().parent != work:
            raise EnvironmentError("Caminho de recuperação inválido.")
        if expected is not None and fingerprint(staged) != expected:
            raise EnvironmentError("Geração de recuperação inválida; evidências preservadas.")
        if (directory / name).is_symlink():
            raise EnvironmentError("Artefato do ambiente inválido.")
    for name in NAMES:
        target = directory / name
        expected = manifest[name]
        if expected is None:
            target.unlink(missing_ok=True)
        else:
            # Keep immutable recovery copies: a second crash can retry safely.
            temp = work / "install.write"
            durable_copy(work / which / name, temp)
            replace(temp, target)


def cleanup(directory: Path, work: Path) -> None:
    if work.exists():
        if work.is_symlink() or work.parent != directory or not re.fullmatch(r"\.dms-restore-operation-[0-9a-f]{32}", work.name):
            raise EnvironmentError("Diretório de recuperação inválido.")
        shutil.rmtree(work)
    (directory / RESTORE_JOURNAL).unlink()


def _load(directory, protector):
    try:
        journal = directory / RESTORE_JOURNAL
        if journal.is_symlink() or journal.stat().st_size > 65536:
            raise ValueError()
        data = json.loads(protector.unprotect(journal.read_bytes()))
        if set(data) != {"restore_journal_version", "id", "state", "generations", "environment"}:
            raise ValueError()
        if data["environment"] != str(directory):
            raise ValueError()
        if type(data["restore_journal_version"]) is not int or data["restore_journal_version"] != 1:
            raise ValueError()
        if not re.fullmatch(r"[0-9a-f]{32}", data["id"]) or data["state"] not in {"STAGED", "SWAPPING", "COMMITTED", "CLEANUP"}:
            raise ValueError()
        if set(data["generations"]) != {"A", "B"}:
            raise ValueError()
        for manifest in data["generations"].values():
            if set(manifest) != set(NAMES):
                raise ValueError()
            for value in manifest.values():
                if value is not None and (set(value) != {"size", "sha256"} or type(value["size"]) is not int or value["size"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
                    raise ValueError()
        if any(data["generations"]["B"][name] is None for name in
               ("secret.key", "vault_key.dpapi", "vault.db", GENERATION_FILE)):
            raise ValueError()
        work = directory / f".dms-restore-operation-{data['id']}"
        if work.is_symlink() or work.resolve().parent != directory:
            raise ValueError()
        for which in ("A", "B"):
            if (work / which).is_symlink():
                raise ValueError()
        return work, data
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError):
        raise EnvironmentError("Journal de restauração inválido; evidências preservadas.") from None


@guarded(lambda directory, protector: directory, exclusive=True, recovery=True)
def recover_restore(directory: Path, protector) -> bool:
    directory = directory.resolve()
    if not (directory / RESTORE_JOURNAL).exists():
        return False
    work, data = _load(directory, protector)
    try:
        if data["state"] != "CLEANUP":
            install_generation(directory, work, data, "B" if data["state"] == "COMMITTED" else "A")
            # Persist cleanup authority before deleting any immutable recovery copy.
            data["state"] = "CLEANUP"
            durable_bytes(work / "journal.write", protector.protect(json.dumps(data, sort_keys=True).encode()))
            durable_replace(work / "journal.write", directory / RESTORE_JOURNAL)
        cleanup(directory, work)
    except OSError:
        raise EnvironmentError("Não foi possível concluir a recuperação; evidências preservadas.") from None
    return True
