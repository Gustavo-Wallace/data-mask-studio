"""Nonblocking interprocess lock for one preference document, not its environment.

The stable empty sidecar is not removed: unlinking it could split concurrent
writers across different locks. Reads never acquire/create it. Closing the
noninheritable descriptor releases the OS lock, including on process death.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import stat


@contextmanager
def preferences_write_lock(path: Path) -> Iterator[None]:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    if lock_path.is_symlink() or lock_path.is_junction():
        raise OSError("Invalid preferences lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        os.set_inheritable(descriptor, False)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size != 0
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or lock_path.is_symlink() or lock_path.is_junction()):
            raise OSError("Invalid preferences lock")
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        elif os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            raise OSError("Preferences locking unavailable")
        yield
    finally:
        os.close(descriptor)
