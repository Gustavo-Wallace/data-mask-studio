"""Sidebar decoration must not change navigation, localization or theme policy."""

from importlib import resources
from pathlib import Path
import tomllib

import pytest
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtSvg import QSvgRenderer

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.gui.components.navigation_icons import (
    ICON_SIZE, NAVIGATION_ICONS, navigation_icon,
)
from data_mask_studio.gui.styles import apply_widget_palette
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.i18n import Translator
from test_bootstrap_localization import app
from test_navigation_localization import build_localized_window, EXPECTED_NAVIGATION


def assert_icon_color(icon, color, *, mode=QIcon.Mode.Normal, state=QIcon.State.Off, ratio=1):
    pixmap = icon.pixmap(QSize(ICON_SIZE, ICON_SIZE), ratio, mode, state)
    assert not pixmap.isNull()
    assert pixmap.deviceIndependentSize().width() == ICON_SIZE
    image = pixmap.toImage()
    assert image.pixelColor(0, 0).alpha() == 0
    opaque = [image.pixelColor(x, y) for x in range(image.width())
              for y in range(image.height()) if image.pixelColor(x, y).alpha() == 255]
    assert opaque, "The SVG must actually render, not just return a non-null icon"
    # Qt's high-DPI resampling can round premultiplied channels by one unit.
    expected = QColor(color).getRgb()[:3]
    assert all(all(abs(actual - target) <= 1 for actual, target in zip(
        pixel.getRgb()[:3], expected, strict=True,
    )) for pixel in opaque), (
        color, mode, state, ratio, {pixel.name() for pixel in opaque},
    )


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_all_nine_icons_preserve_sidebar_structure_and_retranslation(app, tmp_path, colors, locale, monkeypatch):
    window = build_localized_window(tmp_path, Translator(locale))
    apply_widget_palette(window, colors)
    window.show()
    app.processEvents()
    nav = window.navigation
    buttons = (*nav.buttons, nav.about_button)
    pages = window.page_widgets
    assert len(buttons) == len(NAVIGATION_ICONS) == 9
    assert nav.width() == nav.minimumWidth() == nav.maximumWidth() == 220
    margins = nav.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (14, 18, 14, 18)
    assert nav.layout().spacing() == 4
    assert [button.text() for button in nav.buttons] == EXPECTED_NAVIGATION[locale]
    assert nav.about_button.text() == Translator(locale).text("common.about")
    for index, button in enumerate(buttons):
        assert button.iconSize() == QSize(18, 18)
        assert button.accessibleName()
        # The exclusive Qt button group manages TabFocus on inactive entries.
        assert button.focusPolicy() & Qt.FocusPolicy.ClickFocus
        assert_icon_color(button.icon(), colors.navigation_active_text if index == 0 else colors.muted_text)

    nav.buttons[3].click()
    nav.buttons[3].setFocus()
    app.processEvents()
    focus = app.focusWidget()
    focus_policies = [button.focusPolicy() for button in buttons]
    keys = [button.icon().cacheKey() for button in buttons]
    translators = [Translator(language) for language in ("en", "pt-BR", locale)]

    def unexpected_io(*args, **kwargs):
        pytest.fail("Retranslation must reuse icons without resource reads")

    monkeypatch.setattr(resources, "files", unexpected_io)
    for translator in translators:
        window.retranslate(translator)
        assert [button.text() for button in nav.buttons] == EXPECTED_NAVIGATION[translator.locale]
        assert (*nav.buttons, nav.about_button) == buttons
        assert [button.icon().cacheKey() for button in buttons] == keys
        assert window.page_widgets == pages
        assert window.current_page_index() == 3 and nav.buttons[3].isChecked()
        assert app.focusWidget() is focus
        assert [button.focusPolicy() for button in buttons] == focus_policies


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_icons_follow_existing_active_hover_disabled_and_scoped_palette_states(app, tmp_path, colors):
    window = build_localized_window(tmp_path)
    window.show()
    app.processEvents()
    # Exercise a palette applied after construction, not just startup defaults.
    apply_widget_palette(window, colors)
    app.processEvents()
    nav = window.navigation
    checked, normal = nav.buttons[:2]
    assert_icon_color(checked.icon(), colors.navigation_active_text, state=QIcon.State.On)
    assert_icon_color(normal.icon(), colors.muted_text)
    QApplication.sendEvent(normal, QEvent(QEvent.Type.Enter))
    assert_icon_color(normal.icon(), colors.muted_text)  # Same foreground as the existing QSS.
    QApplication.sendEvent(nav.about_button, QEvent(QEvent.Type.Enter))
    assert_icon_color(nav.about_button.icon(), colors.hover_text, mode=QIcon.Mode.Active)
    QApplication.sendEvent(nav.about_button, QEvent(QEvent.Type.Leave))
    assert_icon_color(nav.about_button.icon(), colors.muted_text)

    nav.set_current_index(1)
    assert_icon_color(normal.icon(), colors.navigation_active_text, state=QIcon.State.On)
    assert_icon_color(checked.icon(), colors.muted_text)
    nav.set_page_enabled(1, False)
    assert_icon_color(normal.icon(), colors.disabled_text, mode=QIcon.Mode.Disabled, state=QIcon.State.On)
    nav.set_page_enabled(1, True)
    assert_icon_color(normal.icon(), colors.navigation_active_text, state=QIcon.State.On)
    nav.about_button.setEnabled(False)
    assert_icon_color(nav.about_button.icon(), colors.disabled_text, mode=QIcon.Mode.Disabled)
    nav.about_button.setEnabled(True)
    assert_icon_color(nav.about_button.icon(), colors.muted_text)


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_all_svg_icons_render_at_normal_and_high_dpi_without_native_recoloring(app, colors):
    for name in NAVIGATION_ICONS.values():
        icon = navigation_icon(name, colors.navigation_active_text, colors.disabled_text)
        for ratio in (1, 2, 3):
            for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected, QIcon.Mode.Disabled):
                color = colors.disabled_text if mode == QIcon.Mode.Disabled else colors.navigation_active_text
                for state in (QIcon.State.On, QIcon.State.Off):
                    try:
                        assert_icon_color(icon, color, mode=mode, state=state, ratio=ratio)
                    except AssertionError as error:
                        raise AssertionError(name) from error


def test_only_selected_offline_svg_assets_and_licenses_are_packaged(app):
    from PyInstaller.utils.hooks import collect_data_files

    project_root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((project_root / "pyproject.toml").read_text("utf-8"))
    patterns = project["tool"]["setuptools"]["package-data"]["data_mask_studio.gui"]
    assert patterns == ["icons/*.svg", "icons/*.txt"]
    spec = (project_root / "packaging/windows/DataMaskStudio.spec").read_text("utf-8")
    assert 'collect_data_files("data_mask_studio.gui", includes=["icons/*.svg", "icons/*.txt"])' in spec
    collected = collect_data_files("data_mask_studio.gui", includes=patterns)
    names = {Path(source).name for source, _ in collected}
    assert names == {f"{name}.svg" for name in NAVIGATION_ICONS.values()} | {
        "LUCIDE-LICENSE.txt", "FEATHER-LICENSE.txt", "SOURCES.txt",
    }
    assert len(collected) == 12
    assert all(Path(destination).as_posix() == "data_mask_studio/gui/icons" for _, destination in collected)
    assets = resources.files("data_mask_studio.gui").joinpath("icons")
    for name in NAVIGATION_ICONS.values():
        source = assets.joinpath(f"{name}.svg").read_bytes()
        assert QSvgRenderer(source).isValid()
        assert b'stroke="currentColor"' in source and b'fill="none"' in source
        assert b"href=" not in source and b"<script" not in source
    assert "ISC License" in assets.joinpath("LUCIDE-LICENSE.txt").read_text("utf-8")
    assert "Cole Bemis" in assets.joinpath("FEATHER-LICENSE.txt").read_text("utf-8")
