"""Fail-fast, cross-process Windows locking for one profiles document."""

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from data_mask_studio.profiles.exceptions import ProfileStorageError

_local = threading.local()


@contextmanager
def profile_write_lock(path: Path) -> Iterator[None]:
    if os.name != "nt":
        raise ProfileStorageError("A gravação segura dos perfis requer Windows.")
    import msvcrt

    try:
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ProfileStorageError("Não foi possível bloquear a gravação dos perfis.") from error
    name = os.path.normcase(str(path))
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = set()
    if name in held:
        yield
        return
    try:
        # Lock a stable sidecar, never the JSON inode replaced by os.replace().
        lock_path = path.with_name(f".{path.name}.lock")
        if lock_path.is_symlink():
            raise ProfileStorageError("O lock de gravação dos perfis é inválido.")
        stream = lock_path.open("a+b")
    except OSError as error:
        raise ProfileStorageError("Não foi possível bloquear a gravação dos perfis.") from error

    try:
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise ProfileStorageError(
                "Os perfis estão em uso ou sendo atualizados por outra instância. "
                "Tente novamente após a operação concluir."
            ) from error
        held.add(name)
        try:
            yield
        finally:
            held.remove(name)
    finally:
        # Closing the handle releases the OS byte-range lock, also on errors.
        # Process death releases it too; sidecar existence never means 'busy'.
        stream.close()


def profile_write_guarded(path):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with profile_write_lock(path(*args, **kwargs)):
                return function(*args, **kwargs)
        return call
    return decorate
