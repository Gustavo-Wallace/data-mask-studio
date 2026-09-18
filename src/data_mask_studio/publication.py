"""Recoverable CSV publication; READY is ambiguous, never a commit proof."""

import hashlib
import hmac
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from data_mask_studio.environment import guarded


class PublicationError(RuntimeError):
    """Safe operational failure, with evidence retained for recovery."""


def fingerprint(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise PublicationError("Arquivo da operação ausente ou inválido; evidências preservadas.")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return {"size": size, "sha256": digest.hexdigest()}


def publish(temp: Path, destination: Path, overwrite: bool) -> None:
    if overwrite:
        os.replace(temp, destination)
    else:
        # Same-volume, atomic create-if-absent on Windows/NTFS. No unsafe fallback.
        os.link(temp, destination)
        temp.unlink()


def _encoded(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _signature(data: dict, key: bytes) -> str:
    # Separate namespace; never used by token identity or vault authentication.
    return hmac.new(key, b"DMS-operation-journal-v1\0" + _encoded(data), hashlib.sha256).hexdigest()


class Publication:
    def __init__(self, database: Path, source: Path, destination: Path, key: bytes, overwrite: bool):
        self.key = key
        identifier = uuid.uuid4().hex
        self.directory = database.resolve().parent / "publication-operations"
        self.path = self.directory / f".dms-operation-{identifier}.json"
        self.data = {
            "operation_journal_version": 1, "id": identifier, "state": "READY",
            "database": str(database.resolve()), "source": str(source.resolve()),
            "destination": str(destination.resolve()),
            "overwrite": overwrite,
        }
        self.temp_prefix = f".dms-operation-{identifier}-"
        self.retained = False

    def _write(self) -> None:
        self.directory.mkdir(exist_ok=True)
        if self.directory.is_symlink():
            raise PublicationError("Diretório de operações inválido.")
        content = _encoded({"data": self.data, "signature": _signature(self.data, self.key)})
        staged = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".dms-journal-", delete=False) as stream:
                staged = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, self.path)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    def prepare(self, temp: Path) -> None:
        self.data["temp"] = str(temp.resolve())
        self.data["output"] = fingerprint(temp)
        destination = Path(self.data["destination"])
        self.data["previous"] = fingerprint(destination) if destination.exists() else None
        if self.data["previous"] is not None and not self.data["overwrite"]:
            raise PublicationError("O arquivo de destino já existe.")
        self._write()
        # From this point, an exceptional commit outcome must retain evidence.
        self.retained = True

    def committed(self) -> None:
        self.data["state"] = "COMMITTED"
        self._write()

    def complete(self) -> None:
        self.path.unlink()
        self.retained = False


def _load(path: Path, database: Path, key: bytes) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError()
        envelope = json.loads(path.read_bytes())
        if set(envelope) != {"data", "signature"}:
            raise ValueError()
        data = envelope["data"]
        if set(data) != {"operation_journal_version", "id", "state", "database", "source", "destination", "overwrite", "temp", "output", "previous"}:
            raise ValueError()
        if not hmac.compare_digest(envelope["signature"], _signature(data, key)):
            raise ValueError()
        if type(data["operation_journal_version"]) is not int or data["operation_journal_version"] != 1:
            raise ValueError()
        identifier = data["id"]
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError()
        if path.name != f".dms-operation-{identifier}.json" or data["database"] != str(database.resolve()):
            raise ValueError()
        if data["state"] not in ("READY", "COMMITTED") or type(data["overwrite"]) is not bool:
            raise ValueError()
        source, temp, destination = (Path(data[name]) for name in ("source", "temp", "destination"))
        for item in (source, temp, destination):
            if not item.is_absolute() or item.is_symlink() or str(item.resolve()) != str(item):
                raise ValueError()
        if temp.parent != destination.parent or source in (temp, destination) or temp == destination:
            raise ValueError()
        if not re.fullmatch(rf"\.dms-operation-{identifier}-[a-z0-9_]{{8}}\.tmp", temp.name):
            raise ValueError()
        for value in (data["output"], data["previous"]):
            if value is None:
                continue
            if set(value) != {"size", "sha256"} or type(value["size"]) is not int or value["size"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
                raise ValueError()
        if data["output"] is None:
            raise ValueError()
        return data
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise PublicationError("Journal de publicação inválido; evidências preservadas.") from None


@guarded(lambda database, key: database.parent)
def recover_publications(database: Path, key: bytes) -> int:
    """Filesystem-only recovery; no vault mutation or repeated processing."""
    directory = database.resolve().parent / "publication-operations"
    if directory.is_symlink():
        raise PublicationError("Diretório de operações inválido.")
    recovered = 0
    for path in sorted(directory.glob(".dms-operation-*.json")):
        data = _load(path, database, key)
        if data["state"] != "COMMITTED":
            raise PublicationError(
                "Há uma operação com confirmação do cofre indeterminada. "
                "A publicação foi bloqueada e as evidências foram preservadas."
            )
        temp, destination = Path(data["temp"]), Path(data["destination"])
        try:
            final_matches = destination.exists() and fingerprint(destination) == data["output"]
            if not final_matches:
                if fingerprint(temp) != data["output"]:
                    raise PublicationError("Temporário da operação divergente; evidências preservadas.")
                if destination.exists():
                    if not data["overwrite"] or fingerprint(destination) != data["previous"]:
                        raise PublicationError("Destino da operação divergente; evidências preservadas.")
                    # Recovery never overwrites a destination: even a matching
                    # previous file can change after the comparison (F06).
                    raise PublicationError("Publicação pendente com destino existente; evidências preservadas.")
                publish(temp, destination, False)
            elif temp.exists():
                if fingerprint(temp) != data["output"]:
                    raise PublicationError("Temporário da operação divergente; evidências preservadas.")
                temp.unlink()
            path.unlink()
            recovered += 1
        except OSError:
            raise PublicationError("Não foi possível concluir a publicação pendente; evidências preservadas.") from None
    return recovered
