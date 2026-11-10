"""Bundled outline icons, tinted with the sidebar's existing semantic colors."""

from functools import lru_cache
from importlib import resources
from types import MappingProxyType

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


ICON_SIZE = 18  # Logical pixels; pixmaps below also cover high-DPI displays.
NAVIGATION_ICONS = MappingProxyType({
    "navigation.mask_file": "file-lock-2",
    "navigation.batch_masking": "files",
    "navigation.restore_file": "file-output",
    "navigation.batch_restoration": "list-restart",
    "navigation.browse_vault": "search",
    "navigation.backup": "archive-restore",
    "navigation.maintenance": "wrench",
    "common.settings": "settings",
    "common.about": "info",
})


@lru_cache(maxsize=9)
def _svg(name: str) -> bytes:
    if name not in NAVIGATION_ICONS.values():
        raise ValueError("Unknown navigation icon")
    return resources.files("data_mask_studio.gui").joinpath("icons", f"{name}.svg").read_bytes()


@lru_cache(maxsize=64)
def navigation_icon(name: str, foreground: str, disabled: str) -> QIcon:
    """Use explicit Qt modes so native styles cannot recolor disabled icons.

    Source SVGs are cached, never fetched from the network. Hover/selection
    changes only choose colors; they do not reread resources or user files.
    """
    icon = QIcon()
    for color, modes in (
        (foreground, (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected)),
        (disabled, (QIcon.Mode.Disabled,)),
    ):
        renderer = QSvgRenderer(_svg(name).replace(b"currentColor", color.encode("ascii")))
        for ratio in (1, 2, 3):
            pixmap = QPixmap(ICON_SIZE * ratio, ICON_SIZE * ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            try:
                renderer.render(painter)
            finally:
                painter.end()
            pixmap.setDevicePixelRatio(ratio)
            for mode in modes:
                for state in (QIcon.State.Off, QIcon.State.On):
                    icon.addPixmap(pixmap, mode, state)
    return icon
