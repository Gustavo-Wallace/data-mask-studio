import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QLabel

from data_mask_studio.app import APP_USER_MODEL_ID, create_application
from data_mask_studio.branding import (
    MONOGRAM_BACKGROUND,
    MONOGRAM_BORDER,
    MONOGRAM_FOREGROUND,
)
from data_mask_studio.gui.about_dialog import AboutDialog
from data_mask_studio.gui.styles import application_stylesheet


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRANDING = PROJECT_ROOT / "assets" / "branding"


def _ico_sizes(path: Path) -> tuple[int, ...]:
    data = path.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", data)
    assert reserved == 0
    assert image_type == 1
    sizes = []
    for index in range(count):
        width, height = struct.unpack_from("<BB", data, 6 + index * 16)
        resolved_width = width or 256
        resolved_height = height or 256
        assert resolved_width == resolved_height
        sizes.append(resolved_width)
    return tuple(sizes)


def _pixel_bounds(
    image: QImage,
    predicate,
) -> tuple[int, int, int, int]:
    left = image.width()
    top = image.height()
    right = -1
    bottom = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if predicate(image.pixelColor(x, y)):
                left = min(left, x)
                top = min(top, y)
                right = max(right, x)
                bottom = max(bottom, y)
    assert right >= left and bottom >= top
    return left, top, right, bottom


def test_official_branding_assets_are_valid_and_font_independent() -> None:
    svg = BRANDING / "dms_icon.svg"
    png = BRANDING / "dms_icon_1024.png"
    ico = BRANDING / "dms_icon.ico"

    assert all(path.is_file() and path.stat().st_size > 0 for path in (svg, png, ico))
    svg_content = svg.read_text(encoding="utf-8")
    assert "<text" not in svg_content.casefold()
    image = QImage(str(png))
    assert not image.isNull()
    assert (image.width(), image.height()) == (1024, 1024)
    assert _ico_sizes(ico) == (16, 24, 32, 48, 64, 128, 256)


def test_icon_geometry_colors_transparency_and_spacing_match_badge() -> None:
    image = QImage(str(BRANDING / "dms_icon_1024.png"))
    svg = (BRANDING / "dms_icon.svg").read_text(encoding="utf-8").casefold()
    stylesheet = application_stylesheet().casefold()
    background = QColor(MONOGRAM_BACKGROUND)
    border = QColor(MONOGRAM_BORDER)
    foreground = QColor(MONOGRAM_FOREGROUND)

    for color in (MONOGRAM_BACKGROUND, MONOGRAM_BORDER, MONOGRAM_FOREGROUND):
        assert color.casefold() in svg
        assert color.casefold() in stylesheet
    for x, y in ((0, 0), (1023, 0), (0, 1023), (1023, 1023)):
        assert image.pixelColor(x, y).alpha() == 0

    plate = _pixel_bounds(image, lambda color: color.alpha() > 16)
    plate_width = plate[2] - plate[0] + 1
    assert 0.82 <= plate_width / image.width() <= 0.88

    center_y = image.height() // 2
    border_pixels = [
        x
        for x in range(image.width() // 2)
        if image.pixelColor(x, center_y).rgb() == border.rgb()
    ]
    assert border_pixels
    assert 0.015 <= len(border_pixels) / image.width() <= 0.025
    assert image.pixelColor(image.width() // 2, 200).rgb() == background.rgb()

    text = _pixel_bounds(image, lambda color: color.rgb() == foreground.rgb())
    text_width = text[2] - text[0] + 1
    text_height = text[3] - text[1] + 1
    internal_width = plate_width - 2 * len(border_pixels)
    assert 0.75 <= text_width / internal_width <= 0.78
    assert 0.14 <= text_height / image.height() <= 0.27
    assert text[0] - plate[0] >= image.width() * 0.10
    assert plate[2] - text[2] >= image.width() * 0.10


def test_qapplication_uses_official_icon_and_stable_windows_identity() -> None:
    application = create_application([])

    assert not application.windowIcon().isNull()
    assert application.windowIcon().availableSizes()
    assert APP_USER_MODEL_ID == "com.gustavowallace.datamaskstudio"


def test_about_dialog_contains_only_public_information() -> None:
    application = create_application([])
    dialog = AboutDialog()
    text = " ".join(label.text() for label in dialog.findChildren(QLabel))

    assert "Data Mask Studio" in text
    assert "Versão 1.0.1" in text
    assert "Schema suportado: 3" in text
    assert "Processamento local" in text
    assert "Sem telemetria" in text
    assert "github.com/Gustavo-Wallace/data-mask-studio" in text
    links = dialog.findChild(QLabel, "aboutLinks")
    assert links is not None
    assert "/blob/main/SECURITY.md" in links.text()
    assert "/blob/main/PRIVACY.md" in links.text()
    assert "/blob/main/COMPATIBILITY.md" in links.text()
    assert "/releases" in links.text()
    assert "/security/advisories/new" not in links.text()
    assert "não possuem assinatura digital" in text
    assert "LOCALAPPDATA" not in text
    assert "vault.db" not in text
    assert "secret.key" not in text
    assert "C:\\Users" not in text

    dialog.close()
    application.processEvents()
