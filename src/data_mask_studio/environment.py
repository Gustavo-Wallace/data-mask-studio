"""Windows cross-process leases for a local environment (not lockfile existence)."""

import ctypes
import os
import threading
from contextlib import contextmanager, nullcontext
from functools import wraps
from pathlib import Path
from ctypes import wintypes

RESTORE_JOURNAL = ".dms-environment-restore.json"
GENERATION_FILE = ".dms-environment-generation"


class EnvironmentError(RuntimeError):
    """Safe error for busy, changed or incomplete environments."""


class _Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


_local = threading.local()


@contextmanager
def environment_lease(directory: Path, *, exclusive: bool = False, recovery: bool = False):
    try:
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise EnvironmentError("Não foi possível acessar o ambiente local.") from None
    name = os.path.normcase(str(directory))
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = {}
    if name in held:
        if exclusive and not held[name][0]:
            raise EnvironmentError("O ambiente está em uso. Tente novamente após a operação concluir.")
        held[name][1] += 1
        try:
            yield
        finally:
            _release(held, name)
        return
    lock_path = directory / ".dms-environment.lock"
    if lock_path.is_symlink():
        raise EnvironmentError("Lock do ambiente inválido.")
    if os.name != "nt":
        raise EnvironmentError("A exclusividade do ambiente requer Windows.")
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    try:
        stream = lock_path.open("a+b")
    except OSError:
        raise EnvironmentError("Não foi possível bloquear o ambiente local.") from None
    try:
        handle = msvcrt.get_osfhandle(stream.fileno())
        overlapped = _Overlapped()
        if not kernel.LockFileEx(handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlapped)):
            raise EnvironmentError(
                "O ambiente está em uso ou sendo atualizado por outra instância. "
                "Tente novamente após a operação concluir."
            )
        held[name] = [exclusive, 1, stream, kernel, handle, overlapped]
        try:
            if not recovery and (directory / RESTORE_JOURNAL).exists():
                raise EnvironmentError("O ambiente exige recuperação de uma restauração interrompida.")
            yield
        finally:
            _release(held, name)
    except BaseException:
        if name not in held:
            stream.close()
        raise


def _release(held, name):
    entry = held[name]
    entry[1] -= 1
    if entry[1] == 0:
        del held[name]
        _, _, stream, kernel, handle, overlapped = entry
        kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped))
        stream.close()


def guarded(directory, *, exclusive=False, recovery=False, error_type=None):
    """Hold a lease for a complete synchronous operation."""
    def decorate(function):
        def lease(args, kwargs):
            root = directory(*args, **kwargs)
            return environment_lease(root, exclusive=exclusive, recovery=recovery) if root is not None else nullcontext()
        @wraps(function)
        def call(*args, **kwargs):
            try:
                with lease(args, kwargs):
                    return function(*args, **kwargs)
            except EnvironmentError as error:
                if error_type is not None:
                    raise error_type(str(error)) from error
                raise
        return call
    return decorate


def generation(directory: Path) -> bytes:
    path = Path(directory) / GENERATION_FILE
    try:
        return path.read_bytes() if path.exists() else b""
    except OSError:
        raise EnvironmentError("Não foi possível validar a geração do ambiente.") from None


def provider_directory(provider):
    path = getattr(provider, "key_path", None)
    return path.parent if path is not None else None
