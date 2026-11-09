"""Resolve a stored choice once; presentation and persistence remain separate."""
from collections.abc import Callable
import sys
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from data_mask_studio.preferences.models import ThemePreference

EffectiveTheme = Literal["light", "dark"]


def detect_windows_theme() -> EffectiveTheme | None:
    """Read Qt's Windows appearance hint, never the already-applied palette."""
    if sys.platform != "win32":
        return None
    try:
        application = QGuiApplication.instance()
        if application is None:
            return None
        scheme = application.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "light"
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:
        # Best-effort presentation metadata must not prevent startup.
        pass
    return None


def resolve_theme(
    preference: ThemePreference | str, *,
    system_theme_provider: Callable[[], EffectiveTheme | None] | None = None,
) -> EffectiveTheme:
    preference = ThemePreference(preference)
    if preference is ThemePreference.LIGHT:
        return "light"
    if preference is ThemePreference.DARK:
        return "dark"
    try:
        return "light" if (system_theme_provider or detect_windows_theme)() == "light" else "dark"
    except Exception:
        return "dark"
