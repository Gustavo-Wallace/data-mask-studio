"""Gera os assets finais do ícone DMS e uma comparação visual opcional.

A fonte é usada somente nesta ferramenta de desenvolvimento. O SVG resultante
contém caminhos vetoriais, e o build/runtime consome apenas os assets prontos.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QRawFont, QTransform
from PySide6.QtSvg import QSvgGenerator, QSvgRenderer
from PySide6.QtWidgets import QApplication

from data_mask_studio.branding import (
    MONOGRAM_BACKGROUND,
    MONOGRAM_BADGE_HEIGHT,
    MONOGRAM_BADGE_WIDTH,
    MONOGRAM_BORDER,
    MONOGRAM_FOREGROUND,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRANDING_DIRECTORY = PROJECT_ROOT / "assets" / "branding"
SVG_PATH = BRANDING_DIRECTORY / "dms_icon.svg"
PNG_PATH = BRANDING_DIRECTORY / "dms_icon_1024.png"
ICO_PATH = BRANDING_DIRECTORY / "dms_icon.ico"
COMPARISON_PATH = PROJECT_ROOT / "benchmarks" / ".data" / "icon-validation" / "comparison.png"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
CANVAS_SIZE = 1024
PLATE_RECT = QRectF(82, 82, 860, 860)
PLATE_BORDER_WIDTH = 18
PLATE_RADIUS = 96
TEXT_TARGET_WIDTH = 640
GENERATION_FONT = Path("C:/Windows/Fonts/seguisb.ttf")


def _monogram_path() -> QPainterPath:
    font = QRawFont(str(GENERATION_FONT), 230)
    if not font.isValid():
        raise RuntimeError(
            "A fonte Segoe UI Semibold do Windows não está disponível para gerar o asset."
        )
    glyphs = font.glyphIndexesForString("DMS")
    advances = font.advancesForGlyphIndexes(glyphs)
    path = QPainterPath()
    cursor = 0.0
    for glyph, advance in zip(glyphs, advances, strict=True):
        glyph_path = font.pathForGlyph(glyph)
        transform = QTransform()
        transform.translate(cursor, 0)
        path.addPath(transform.map(glyph_path))
        cursor += advance.x() + 10
    bounds = path.boundingRect()
    scale = TEXT_TARGET_WIDTH / bounds.width()
    transform = QTransform()
    transform.scale(scale, scale)
    path = transform.map(path)
    bounds = path.boundingRect()
    transform.reset()
    transform.translate(
        (CANVAS_SIZE - bounds.width()) / 2 - bounds.x(),
        (CANVAS_SIZE - bounds.height()) / 2 - bounds.y() + 3,
    )
    return transform.map(path)


def _render_badge_reference(monogram: QPainterPath) -> QImage:
    scale = 8
    image = QImage(
        MONOGRAM_BADGE_WIDTH * scale,
        MONOGRAM_BADGE_HEIGHT * scale,
        QImage.Format.Format_ARGB32,
    )
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(MONOGRAM_BORDER), scale))
    painter.setBrush(QColor(MONOGRAM_BACKGROUND))
    painter.drawRoundedRect(
        QRectF(scale / 2, scale / 2, image.width() - scale, image.height() - scale),
        5 * scale,
        5 * scale,
    )
    bounds = monogram.boundingRect()
    text_scale = 23 * scale / bounds.width()
    transform = QTransform()
    transform.scale(text_scale, text_scale)
    badge_path = transform.map(monogram)
    bounds = badge_path.boundingRect()
    transform.reset()
    transform.translate(
        (image.width() - bounds.width()) / 2 - bounds.x(),
        (image.height() - bounds.height()) / 2 - bounds.y(),
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(MONOGRAM_FOREGROUND))
    painter.drawPath(transform.map(badge_path))
    painter.end()
    return image


def generate_svg() -> None:
    BRANDING_DIRECTORY.mkdir(parents=True, exist_ok=True)
    generator = QSvgGenerator()
    generator.setFileName(str(SVG_PATH))
    generator.setSize(QSize(CANVAS_SIZE, CANVAS_SIZE))
    generator.setViewBox(QRectF(0, 0, CANVAS_SIZE, CANVAS_SIZE))
    generator.setTitle("Ícone do Data Mask Studio")
    generator.setDescription("Monograma DMS baseado no badge da barra lateral")

    painter = QPainter(generator)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(
        QPen(
            QColor(MONOGRAM_BORDER),
            PLATE_BORDER_WIDTH,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.SquareCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(QColor(MONOGRAM_BACKGROUND))
    painter.drawRoundedRect(PLATE_RECT, PLATE_RADIUS, PLATE_RADIUS)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(MONOGRAM_FOREGROUND))
    painter.drawPath(_monogram_path())
    painter.end()


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    render_size = size * 4 if size <= 64 else size
    image = QImage(QSize(render_size, render_size), QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, render_size, render_size))
    painter.end()
    if render_size != size:
        image = image.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Não foi possível renderizar o ícone em {size}x{size}.")
    return bytes(data)


def write_ico(images: tuple[tuple[int, bytes], ...]) -> None:
    offset = 6 + 16 * len(images)
    entries: list[bytes] = []
    payloads: list[bytes] = []
    for size, payload in images:
        encoded_size = 0 if size == 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                encoded_size,
                encoded_size,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        payloads.append(payload)
        offset += len(payload)
    ICO_PATH.write_bytes(
        struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries + payloads)
    )


def write_comparison_sheet(renderer: QSvgRenderer) -> None:
    badge = _render_badge_reference(_monogram_path())
    samples = (("Badge", badge),) + tuple(
        (f"{size}×{size}", QImage.fromData(render_png(renderer, size), "PNG"))
        for size in (1024, 256, 64, 32, 24, 16)
    )
    sheet = QImage(1500, 700, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#eef2f6"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    x = 30
    for _label, image in samples:
        display_size = {
            1024: 210,
            304: 210,
            256: 210,
            64: 192,
            32: 160,
            24: 144,
            16: 128,
        }[image.width()]
        display_height = display_size * image.height() / image.width()
        top = 100 + (210 - display_height) / 2
        bottom = 400 + (210 - display_height) / 2
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            image.width() >= 128,
        )
        painter.drawImage(
            QRectF(x, top, display_size, display_height),
            image,
            QRectF(image.rect()),
        )
        painter.fillRect(QRectF(x, bottom, display_size, display_height), QColor("#121821"))
        painter.drawImage(
            QRectF(x, bottom, display_size, display_height),
            image,
            QRectF(image.rect()),
        )
        x += display_size + 28
    painter.end()
    COMPARISON_PATH.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(str(COMPARISON_PATH), "PNG")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison-sheet", action="store_true")
    arguments = parser.parse_args()
    application = QApplication.instance() or QApplication([])
    generate_svg()
    renderer = QSvgRenderer(str(SVG_PATH))
    if not renderer.isValid():
        raise RuntimeError("O SVG oficial do Data Mask Studio é inválido.")
    png_images = tuple((size, render_png(renderer, size)) for size in ICO_SIZES)
    PNG_PATH.write_bytes(render_png(renderer, CANVAS_SIZE))
    write_ico(png_images)
    if arguments.comparison_sheet:
        write_comparison_sheet(renderer)
    application.processEvents()


if __name__ == "__main__":
    main()
