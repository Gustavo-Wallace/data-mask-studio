"""Shared page hierarchy, wrapping and scoped typography, without pixel snapshots."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.components import PageHeader, PageShell
from data_mask_studio.gui.visual_tokens import COLORS, METRICS
from test_main_navigation import build_window


DESCRIPTIONS = (
    "Defina como cada coluna será tratada no CSV de saída: preservar, mascarar ou excluir.",
    "Processe vários arquivos CSV com um perfil de configuração salvo.",
    "Recupere valores de colunas selecionadas usando o cofre local ou um pacote de transferência.",
    "Recupere códigos presentes em HTML e dashboards locais.",
    "Restaure vários arquivos CSV ou HTML usando o cofre local.",
    "Consulte códigos específicos sem expor todo o conteúdo do cofre.",
    "Crie ou restaure um backup criptografado do ambiente local.",
    "Verifique as chaves, o cofre e os perfis sem modificar os dados.",
    "Diagnostique, limpe temporários e compacte o cofre com segurança.",
)


def test_all_main_pages_share_header_hierarchy_alignment_and_original_texts(tmp_path):
    app = create_application([])
    window = build_window(tmp_path)
    window.show()
    app.processEvents()
    assert window.current_page_index() == 0
    assert len(window.page_shells) == len(DESCRIPTIONS) == 9

    for index, shell in enumerate(window.page_shells):
        window.set_current_page(index)
        app.processEvents()
        header = shell.header
        assert isinstance(header, PageHeader)
        assert header.frameShape() == QFrame.Shape.NoFrame
        assert header.findChildren(QPushButton) == []
        assert len(header.findChildren(QLabel)) == 2
        assert header.title_label.text() == window.navigation.buttons[index].text()
        assert header.title_label.accessibleName() == f"Título da página: {header.title_label.text()}"
        assert header.description_label.text() == DESCRIPTIONS[index]
        assert header.description_label.text() == window.navigation.buttons[index].accessibleDescription()
        assert header.description_label.wordWrap()
        assert header.description_label.textInteractionFlags() == Qt.TextInteractionFlag.TextSelectableByMouse
        assert header.title_label.font().pixelSize() == METRICS["page_title_font_size"]
        assert header.description_label.font().pixelSize() == METRICS["description_font_size"]
        assert header.title_label.font().pixelSize() > header.description_label.font().pixelSize()
        assert header.layout().spacing() == METRICS["header_text_spacing"]
        assert shell.layout().spacing() == METRICS["header_content_spacing"]
        title_x = header.title_label.mapTo(shell, QPoint(0, 0)).x()
        assert title_x == header.description_label.mapTo(shell, QPoint(0, 0)).x()
        assert title_x == shell.scroll_area.geometry().left()
        assert header.geometry().bottom() < shell.scroll_area.geometry().top()
        assert shell.scroll_area.widget() is window.page_widgets[index]
        assert shell.content.property("pageContent")
        assert window.navigation.buttons[index].isChecked()


@pytest.mark.parametrize("width", [320, 640])
@pytest.mark.parametrize("description", [
    "Orientação sintética curta.",
    "Orientação sintética para verificar a quebra de linha e a leitura completa da descrição. " * 3,
])
def test_wrapped_descriptions_remain_accessible_and_leave_room_for_content(width, description):
    app = create_application([])
    content = QWidget()
    content_layout = QVBoxLayout(content)
    field = QLineEdit("unchanged input")
    content_layout.addWidget(field)
    shell = PageShell("Título sintético", description, content)
    shell.resize(width, 400)
    shell.show()
    app.processEvents()

    header = shell.header
    label = header.description_label
    assert label.text() == description and label.wordWrap()
    assert label.height() >= label.heightForWidth(label.width())
    assert header.rect().contains(label.geometry())
    assert header.geometry().bottom() < shell.scroll_area.geometry().top()
    assert shell.scroll_area.viewport().height() > 0
    assert shell.scroll_area.widget() is content and field.text() == "unchanged input"
    assert content.layout().contentsMargins().isNull()


def test_header_typography_is_scoped_and_does_not_style_labels_outside_page_header():
    create_application([])
    header = PageHeader("Título", "Descrição")
    outside = QWidget()
    outside_layout = QVBoxLayout(outside)
    for name in ("pageTitle", "pageDescription"):
        label = QLabel("Outside the shared header")
        label.setObjectName(name)
        font = label.font()
        font.setPixelSize(12)
        label.setFont(font)
        outside_layout.addWidget(label)
        label.ensurePolished()
        assert label.font().pixelSize() == 12
    header.ensurePolished()
    for label, size, color in (
        (header.title_label, "page_title_font_size", "text"),
        (header.description_label, "description_font_size", "muted_text"),
    ):
        label.ensurePolished()
        assert label.parentWidget() is header
        assert label.font().pixelSize() == METRICS[size]
        assert label.palette().color(QPalette.ColorRole.WindowText) == QColor(COLORS[color])
