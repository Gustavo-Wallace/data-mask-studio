"""Opt-in file selection on existing controls, never file transfer or processing."""

from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QObject, Qt, QUrl
from PySide6.QtWidgets import QAbstractItemView, QApplication, QWidget


def local_drop_path(url: QUrl) -> Path | None:
    """Decode local absolute paths without probing remote URL authorities."""
    if (not url.isValid() or not url.isLocalFile() or url.host()
            or url.hasQuery() or url.hasFragment()):
        return None
    path = Path(url.toLocalFile())
    if not path.is_absolute() or path.drive.startswith("\\\\"):
        return None
    return path


class FileDrop(QObject):
    """Shared Qt delivery/highlighting; subclasses decide acceptable inputs."""

    def __init__(
        self, owner: QWidget, targets: Iterable[QWidget], *,
        select_paths: Callable[[tuple[Path, ...]], object],
        available: Callable[[], bool],
    ) -> None:
        super().__init__(owner)
        self._targets = tuple(targets)
        self._select_paths = select_paths
        self._available = available
        for target in self._targets:
            target.setAcceptDrops(True)
            target.installEventFilter(self)

    def _candidate(self, target: QWidget, mime: QMimeData) -> tuple[Path, ...] | None:
        if (not target.isEnabled() or not target.isVisible()
                or QApplication.activeModalWidget() is not None or not self._available()):
            return None
        return self._paths_from_mime(mime)

    def _paths_from_mime(self, mime: QMimeData) -> tuple[Path, ...] | None:
        raise NotImplementedError

    @staticmethod
    def _highlight(target: QWidget, active: bool) -> None:
        view = target.parentWidget()
        if isinstance(view, QAbstractItemView) and view.viewport() is target:
            target = view  # Events arrive on the viewport; the view owns its border.
        if bool(target.property("fileDropActive")) == active:
            return
        target.setProperty("fileDropActive", active)
        target.style().unpolish(target)
        target.style().polish(target)
        target.update()

    def eventFilter(self, target, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.Hide:
            self._highlight(target, False)
        elif kind == QEvent.Type.EnabledChange:
            if not self._available():
                for control in self._targets:
                    self._highlight(control, False)
        elif kind == QEvent.Type.DragLeave:
            self._highlight(target, False)
            event.accept()
            return True
        elif kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            path = (self._candidate(target, event.mimeData())
                    if event.possibleActions() & Qt.DropAction.CopyAction else None)
            self._highlight(target, path is not None and kind != QEvent.Type.Drop)
            if path is None:
                event.ignore()
            else:
                # Selection never authorizes the drag source to move/delete a file.
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
                if kind == QEvent.Type.Drop:
                    self._select_paths(path)
            # Invalid MIME data must not fall through to QLineEdit's text drop.
            return True
        return super().eventFilter(target, event)


class SingleFileDrop(FileDrop):
    def __init__(
        self, owner: QWidget, targets: Iterable[QWidget], *,
        extensions: frozenset[str], select_file: Callable[[str], object],
        available: Callable[[], bool],
    ) -> None:
        self._extensions = extensions
        super().__init__(owner, targets, select_paths=lambda paths: select_file(str(paths[0])),
                         available=available)

    def _paths_from_mime(self, mime: QMimeData) -> tuple[Path, ...] | None:
        if not mime.hasUrls() or len(urls := mime.urls()) != 1:
            return None
        path = local_drop_path(urls[0])
        if path is None or path.suffix.casefold() not in self._extensions:
            return None
        try:
            return (path,) if path.is_file() else None
        except (OSError, ValueError):
            return None
