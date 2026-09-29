"""Exclusive Windows output claims; a claim is never a published artifact."""

import ctypes
import hashlib
import os
import threading
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

_owned = threading.local()


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


class OutputReservation:
    def __init__(self, path: Path):
        self.path = path
        name = hashlib.sha256(os.path.normcase(path.name).encode('utf-8')).hexdigest()
        self.claim_path = path.parent / f'.dms-batch-reservation-{name}.lock'
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        # CREATE_NEW + DELETE access + no sharing + DELETE_ON_CLOSE. The kernel
        # owns cleanup of this exact claim, including process death. No unlink
        # by pathname and no stat/delete race; the final path is never deleted.
        self._handle = kernel.CreateFileW(str(self.claim_path), 0x00010000, 0,
                                         None, 1, 0x04000080, None)
        if self._handle == wintypes.HANDLE(-1).value:
            self._handle = None
            error = ctypes.get_last_error()
            if error in (32, 80, 183):
                raise FileExistsError(str(self.claim_path))
            raise ctypes.WinError(error)
        if not hasattr(_owned, 'claims'):
            _owned.claims = {}
        _owned.claims[_key(path)] = self

    def remove(self) -> None:
        self.close()

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            if not self._kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            claims = getattr(_owned, 'claims', {})
            if claims.get(_key(self.path)) is self:
                del claims[_key(self.path)]


@contextmanager
def publication_reservation(path: Path):
    """All publishers honor claims; a producer may use its own held claim."""
    owned = getattr(_owned, 'claims', {}).get(_key(path))
    if owned is not None and owned._handle is not None:
        yield
        return
    reservation = OutputReservation(path)
    try:
        yield
    finally:
        reservation.close()
