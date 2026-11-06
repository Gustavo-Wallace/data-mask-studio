"""Visual foundation only: immutable values/rendering, never theme activation."""

from dataclasses import FrozenInstanceError, asdict, fields, replace
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

import pytest
from PySide6.QtCore import QLocale, QThread, QTranslator
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.gui import styles
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE, ThemePalette
from data_mask_studio.gui.visual_tokens import COLORS
from data_mask_studio.preferences import PreferencesService
from test_bootstrap_localization import app
from test_localization_integration import worker_references
from test_navigation_localization import build_localized_window


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_palettes_are_complete_typed_immutable_and_valid_rgb(colors):
    assert isinstance(colors, ThemePalette)
    assert len(fields(colors)) == 60
    assert all(field.type is str for field in fields(colors))
    assert all(QColor(value).isValid() for value in asdict(colors).values())
    with pytest.raises(FrozenInstanceError):
        colors.window = "#000000"
    copied = asdict(colors)
    copied["window"] = "#000000"
    assert colors.window != copied["window"]


@pytest.mark.parametrize("invalid", [None, 1, "red", "#fff", "#123456; color: red", "url(secret)"])
def test_palette_rejects_missing_values_or_qss_fragments(invalid):
    with pytest.raises(ValueError, match="Invalid semantic palette color"):
        replace(LIGHT_PALETTE, text=invalid)


def test_entire_legacy_dark_color_map_and_qss_states_are_preserved():
    # Legacy COLORS is unchanged. QSS golden includes 05N.4's explicit sidebar
    # surface/border, matching the previously rendered Dark pixels (the old
    # QWidget did not paint these two declarations). All other states stay exact.
    colors = json.dumps(dict(COLORS), sort_keys=True).encode()
    assert hashlib.sha256(colors).hexdigest() == "0c8f98cd914c81a363a493e9a8b9ebbb404a89449569aad622ea1a45ae3d0616"
    qss = " ".join(re.sub(r"\bwhite\b", "#ffffff", styles.application_stylesheet()).split())
    processing = re.search(r"/\* Processing presentation \*/.*?/\* End processing presentation \*/", qss)
    assert processing is not None
    # The new processing rules migrate formerly local CSS. Outside that bounded
    # section, all previous shared rules still match the complete 05N.4 golden.
    previous_qss = " ".join(qss.replace(processing.group(), "").split())
    assert hashlib.sha256(previous_qss.encode()).hexdigest() == "9102f119a412fb929d700b2ba28fda3e1b7a86cc8119797e9a7005fe4c5c9177"
    # Preserve the previous whole-stylesheet assertion as well: only the
    # previously unpainted sidebar declaration is allowed to differ from 05N.3.
    sidebar = f"QWidget#sidebarNavigation {{ background: {DARK_PALETTE.navigation_surface}; border-right: 1px solid {DARK_PALETTE.navigation_border}; }}"
    assert previous_qss.count(sidebar) == 1
    legacy_qss = previous_qss.replace(sidebar, "QWidget#sidebarNavigation { background: #10161e; border-right: 1px solid #303b49; }")
    assert hashlib.sha256(legacy_qss.encode()).hexdigest() == "00f5703f372164d91efbf397dd9aa973dd8bc67d6987118c0840b91be9492d6a"
    assert styles.application_stylesheet() == styles.application_stylesheet(DARK_PALETTE)


@pytest.mark.parametrize("role,legacy", [
    ("indicator_hover_surface", "#1c2531"), ("indicator_border", "#6f7f91"),
    ("indicator_disabled_border", "#46515f"),
])
def test_custom_indicator_legacy_values_not_present_in_qss_are_preserved(role, legacy):
    assert getattr(DARK_PALETTE, role) == legacy


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_stylesheet_is_deterministic_complete_and_uses_same_structure(colors):
    first = styles.application_stylesheet(colors)
    assert all(styles.application_stylesheet(colors) is first for _ in range(4))
    assert "$" not in first and "__BADGE_" not in first and "__BACKGROUND_" not in first
    selectors = lambda css: [part.strip() for part in re.findall(r"([^{}]+)\{[^{}]*\}", css)]
    assert selectors(first) == selectors(styles.application_stylesheet(DARK_PALETTE))
    assert first.count("{") == first.count("}")
    assert f"QProgressBar::chunk {{ background: {colors.progress_fill}; }}" in first
    assert f"QLineEdit[readOnly=\"true\"] {{ color: {colors.readonly_text}; }}" in first


def test_equal_dark_colors_remain_distinct_roles_and_render_independently():
    assert DARK_PALETTE.accent == DARK_PALETTE.progress_fill
    custom = replace(DARK_PALETTE, progress_fill="#123456")
    rendered = styles.application_stylesheet(custom)
    assert "QProgressBar::chunk { background: #123456; }" in rendered
    assert f"border-left-color: {DARK_PALETTE.accent}" in rendered
    assert DARK_PALETTE.progress_fill == "#347db8"


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_qpalettes_are_fresh_mutable_copies_with_semantic_groups(colors):
    palette = styles.application_palette(colors)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        assert palette.color(group, QPalette.ColorRole.Window) == QColor(colors.window)
        assert palette.color(group, QPalette.ColorRole.Base) == QColor(colors.base)
        assert palette.color(group, QPalette.ColorRole.HighlightedText) == QColor(colors.selection_text)
    assert palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == QColor(colors.disabled_text)
    assert palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight) == QColor(colors.disabled_selection)
    palette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
    assert styles.application_palette(colors).color(QPalette.ColorRole.Window) == QColor(colors.window)


def contrast(foreground, background):
    def luminance(value):
        channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    dark, light = sorted((luminance(foreground), luminance(background)))
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize("foreground,background", [
    ("text", "window"), ("text", "raised_surface"), ("input_text", "base"),
    ("muted_text", "window"), ("muted_text", "surface"), ("readonly_text", "base"),
    ("heading_text", "window"), ("header_text", "raised_surface"),
    ("button_text", "button"), ("button_text", "button_hover"),
    ("text_on_accent", "primary"), ("text_on_accent", "primary_hover"),
    ("selection_text", "selection"), ("navigation_active_text", "navigation_active"),
    ("hover_text", "surface"), ("link_text", "window"),
    ("success_text", "window"), ("warning_text", "window"),
    ("warning_text", "warning_surface"), ("warning_text", "warning_hover_surface"),
    ("danger_text", "window"), ("danger_text", "danger_surface"),
    ("disabled_text", "disabled_surface"),
])
def test_meaningful_text_pairs_reach_normal_text_contrast_in_both_palettes(foreground, background):
    for colors in (DARK_PALETTE, LIGHT_PALETTE):
        assert contrast(getattr(colors, foreground), getattr(colors, background)) >= 4.5


@pytest.mark.parametrize("foreground,background", [
    ("focus", "window"), ("focus", "base"), ("focus", "button"),
    ("focus", "button_hover"), ("focus", "selection"), ("input_focus", "base"),
    ("interactive_border", "base"), ("indicator_border", "base"),
    ("progress_fill", "base"),
])
def test_light_focus_and_relevant_nontext_indicators_have_three_to_one_contrast(foreground, background):
    assert contrast(getattr(LIGHT_PALETTE, foreground), getattr(LIGHT_PALETTE, background)) >= 3


def test_light_progress_label_pair_and_documented_legacy_exceptions():
    assert contrast(LIGHT_PALETTE.text, LIGHT_PALETTE.progress_fill) >= 4.5
    # Preserve legacy Dark, rather than silently changing it to pass new checks.
    assert contrast(DARK_PALETTE.border, DARK_PALETTE.base) < 3
    assert contrast(DARK_PALETTE.text, DARK_PALETTE.progress_fill) < 4.5


def test_rendering_does_not_change_live_window_preferences_inputs_workers_or_translator(app, tmp_path, monkeypatch):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_theme("light").error is None
    window = build_localized_window(tmp_path, preferences_service=service)
    window.show()
    window.set_current_page(7)
    app.processEvents()
    window.backup_widget.create_password_field.setText("Synthetic password")
    fields = window.findChildren(QLineEdit)
    values = [(field.text(), field.echoMode(), field.selectedText()) for field in fields]
    pages, translator = window.page_widgets, window._translator
    focus = app.focusWidget()
    locale, palette, stylesheet, style = QLocale().name(), app.palette(), app.styleSheet(), app.style()
    qt_translators = app.findChildren(QTranslator)
    notification = QSignalSpy(window.settings_widget.language_saved)
    navigation = QSignalSpy(window.navigation.current_changed)
    before = service.path.read_bytes()
    stopped_worker = QThread()
    def forbidden(*args, **kwargs):
        pytest.fail("Style rendering must not read files/settings, resolve language, or start operations")
    with monkeypatch.context() as guard:
        guard.setattr(window.anonymization_widget, "_worker", stopped_worker)
        workers = worker_references(window)
        guard.setattr(PreferencesService, "load", forbidden)
        guard.setattr(PreferencesService, "save_theme", forbidden)
        guard.setattr(PreferencesService, "save_language", forbidden)
        guard.setattr(PreferencesService, "resolve_language", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        guard.setattr(QThread, "start", forbidden)
        guard.setattr(window, "retranslate", forbidden)
        for name in ("open", "read_bytes", "read_text", "stat", "resolve", "mkdir", "write_bytes"):
            guard.setattr(Path, name, forbidden)
        styles._render_stylesheet.cache_clear()  # Exercise uncached generation, not a cache-only no-op.
        for colors in (LIGHT_PALETTE, DARK_PALETTE, LIGHT_PALETTE, DARK_PALETTE):
            assert styles.application_stylesheet(colors)
            assert styles.application_palette(colors).window().color() == QColor(colors.window)
        assert worker_references(window) == workers
    assert window.page_widgets == pages and window._translator is translator
    assert [(field.text(), field.echoMode(), field.selectedText()) for field in fields] == values
    assert app.focusWidget() is focus and window.current_page_index() == 7
    assert app.palette() == palette and app.styleSheet() == stylesheet and app.style() is style
    assert QLocale().name() == locale and app.findChildren(QTranslator) == qt_translators
    assert notification.count() == navigation.count() == 0
    assert service.path.read_bytes() == before
    assert app.property("dataMaskStudioTheme") == "DataMaskStudioDark"


def test_palette_values_import_without_qt_preferences_or_platform_services(tmp_path):
    program = r'''
import sys
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
assert DARK_PALETTE.window != LIGHT_PALETTE.window
assert not any(name.startswith(("PySide6", "data_mask_studio.preferences", "data_mask_studio.i18n", "data_mask_studio.vault", "data_mask_studio.security", "sqlite3", "cryptography")) for name in sys.modules)
'''
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(tmp_path / "isolated-process-appdata")
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-B", "-c", program], env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
