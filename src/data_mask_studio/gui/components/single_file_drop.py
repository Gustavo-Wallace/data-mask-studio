"""Opt-in file selection on existing controls, never file transfer or processing."""

from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget


class SingleFileDrop(QObject):
    def __init__(
        self, owner: QWidget, targets: Iterable[QWidget], *,
        extensions: frozenset[str], select_file: Callable[[str], object],
        available: Callable[[], bool],
    ) -> None:
        super().__init__(owner)
        self._targets = tuple(targets)
        self._extensions = extensions
        self._select_file = select_file
        self._available = available
        for target in self._targets:
            target.setAcceptDrops(True)
            target.installEventFilter(self)

    def _candidate(self, target: QWidget, mime: QMimeData) -> str | None:
        if (not target.isEnabled() or not target.isVisible()
                or QApplication.activeModalWidget() is not None or not self._available()):
            return None
        if not mime.hasUrls():
            return None
        urls = mime.urls()
        if len(urls) != 1:
            return None
        url = urls[0]
        if (not url.isValid() or not url.isLocalFile() or url.host()
                or url.hasQuery() or url.hasFragment()):
            return None
        path = Path(url.toLocalFile())
        # Do not probe network authorities, relative paths or directories.
        if (not path.is_absolute() or path.drive.startswith("\\\\")
                or path.suffix.casefold() not in self._extensions):
            return None
        try:
            return str(path) if path.is_file() else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _highlight(target: QWidget, active: bool) -> None:
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
                    self._select_file(path)
            # Invalid MIME data must not fall through to QLineEdit's text drop.
            return True
        return super().eventFilter(target, event)
