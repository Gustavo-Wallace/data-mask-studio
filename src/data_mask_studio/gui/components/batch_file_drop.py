"""Batch drops delegate discovery, filtering and deduplication to existing flows."""

from collections.abc import Callable, Iterable
from pathlib import Path
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISDIR, S_ISLNK, S_ISREG

from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QWidget

from data_mask_studio.gui.components.single_file_drop import FileDrop, local_drop_path


def _local_kind(path: Path) -> str | None:
    # Inspect components from the root down, without resolving through links.
    try:
        for component in (*reversed(path.parents), path):
            info = component.lstat()
            if S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
                return None  # Windows FILE_ATTRIBUTE_REPARSE_POINT (includes junctions).
        return "directory" if S_ISDIR(info.st_mode) else "file" if S_ISREG(info.st_mode) else None
    except (OSError, ValueError):
        return None


class BatchFileDrop(FileDrop):
    def __init__(
        self, owner: QWidget, targets: Iterable[QWidget], *,
        extensions: frozenset[str], select_paths: Callable[[tuple[Path, ...]], object],
        available: Callable[[], bool],
    ) -> None:
        self._extensions = extensions
        super().__init__(owner, targets, select_paths=select_paths, available=available)

    def _paths_from_mime(self, mime: QMimeData) -> tuple[Path, ...] | None:
        if not mime.hasUrls() or not (urls := mime.urls()):
            return None
        paths = []
        for url in urls:
            path = local_drop_path(url)
            if path is None or (kind := _local_kind(path)) is None:
                return None
            if kind == "directory" or path.suffix.casefold() in self._extensions:
                paths.append(path)
        # Existing add_files remains authoritative for filtering/deduplication.
        return tuple(paths) or None


def discover_drop_files(
    paths: Iterable[Path], discover: Callable[..., list[Path]],
) -> list[Path]:
    """Expand once on drop, never during drag; collect before mutating GUI state."""
    files = []
    for path in paths:
        kind = _local_kind(path)
        if kind == "directory":
            files.extend(entry for entry in discover(path, follow_symlinks=False)
                         if _local_kind(entry) == "file")
        elif kind == "file":
            files.append(path)
    return files
