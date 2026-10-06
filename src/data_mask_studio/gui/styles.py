from string import Template

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleOption

from data_mask_studio.branding import (
    MONOGRAM_BACKGROUND,
    MONOGRAM_BADGE_BORDER,
    MONOGRAM_BADGE_HEIGHT,
    MONOGRAM_BADGE_RADIUS,
    MONOGRAM_BADGE_WIDTH,
    MONOGRAM_BORDER,
    MONOGRAM_FOREGROUND,
)
from data_mask_studio.gui.visual_tokens import COLORS, METRICS


# Preserva os imports já utilizados pela aplicação e pelos testes.
WINDOW_COLOR = COLORS["window"]
TEXT_COLOR = COLORS["text"]
BASE_COLOR = COLORS["base"]
DISABLED_TEXT_COLOR = COLORS["disabled_text"]
HIGHLIGHT_COLOR = COLORS["selection"]
APPLICATION_THEME_NAME = "DataMaskStudioDark"
_application_style: "DataMaskStudioStyle | None" = None


class DataMaskStudioStyle(QProxyStyle):
    """Fusion com indicador de checkbox previsível e acessível."""

    def __init__(self) -> None:
        super().__init__("Fusion")
        self.setObjectName("DataMaskStudioFusion")

    def drawPrimitive(
        self,
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget=None,
    ) -> None:
        if element != QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return

        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        checked = bool(option.state & QStyle.StateFlag.State_On)
        partial = bool(option.state & QStyle.StateFlag.State_NoChange)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if not enabled:
            background = QColor(COLORS["disabled_surface"])
            border = QColor("#46515f")
            mark = QColor(DISABLED_TEXT_COLOR)
        elif checked or partial:
            background = QColor(COLORS["primary"])
            border = QColor(COLORS["focus"])
            mark = QColor(COLORS["white"])
        else:
            background = QColor("#1c2531" if hovered else BASE_COLOR)
            border = QColor(COLORS["focus"] if hovered else "#6f7f91")
            mark = QColor(COLORS["white"])

        rect = option.rect.adjusted(1, 1, -1, -1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(border, 1.25))
        painter.setBrush(background)
        radius = METRICS["indicator_radius"]
        painter.drawRoundedRect(rect, radius, radius)

        pen = QPen(mark, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if checked:
            path = QPainterPath()
            path.moveTo(QPointF(rect.left() + rect.width() * 0.22, rect.center().y()))
            path.lineTo(
                QPointF(
                    rect.left() + rect.width() * 0.43,
                    rect.bottom() - rect.height() * 0.24,
                )
            )
            path.lineTo(
                QPointF(
                    rect.right() - rect.width() * 0.17,
                    rect.top() + rect.height() * 0.23,
                )
            )
            painter.drawPath(path)
        elif partial:
            y = rect.center().y()
            painter.drawLine(
                QPointF(rect.left() + rect.width() * 0.24, y),
                QPointF(rect.right() - rect.width() * 0.24, y),
            )
        painter.restore()


def application_palette() -> QPalette:
    """Retorna a paleta oficial sem herdar cores essenciais do sistema."""
    palette = QPalette()
    active_roles = {
        QPalette.ColorRole.Window: WINDOW_COLOR,
        QPalette.ColorRole.WindowText: TEXT_COLOR,
        QPalette.ColorRole.Base: BASE_COLOR,
        QPalette.ColorRole.AlternateBase: COLORS["surface"],
        QPalette.ColorRole.ToolTipBase: COLORS["raised_surface"],
        QPalette.ColorRole.ToolTipText: COLORS["input_text"],
        QPalette.ColorRole.Text: COLORS["input_text"],
        QPalette.ColorRole.Button: COLORS["button"],
        QPalette.ColorRole.ButtonText: COLORS["button_text"],
        QPalette.ColorRole.BrightText: COLORS["white"],
        QPalette.ColorRole.Highlight: HIGHLIGHT_COLOR,
        QPalette.ColorRole.HighlightedText: COLORS["white"],
        QPalette.ColorRole.PlaceholderText: DISABLED_TEXT_COLOR,
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, color in active_roles.items():
            palette.setColor(group, role, QColor(color))

    for role, color in active_roles.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(
            QPalette.ColorGroup.Disabled, role, QColor(DISABLED_TEXT_COLOR)
        )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor("#344252")
    )
    return palette


def apply_application_theme(application: QApplication) -> None:
    """Aplica estilo, paleta e stylesheet oficiais em toda a aplicação."""
    global _application_style
    style = DataMaskStudioStyle()
    application.setStyle(style)
    _application_style = style
    application.setProperty("dataMaskStudioTheme", APPLICATION_THEME_NAME)
    application.setPalette(application_palette())
    application.setStyleSheet(application_stylesheet())


def application_stylesheet() -> str:
    stylesheet = """
    QMainWindow, QDialog, QMessageBox { background: $window; color: $text; }
    QWidget { color: $text; }
    QWidget#mainWorkspace, QWidget#pageShell, QWidget[pageContent="true"] { background: $window; }
    QScrollArea#pageScrollArea, QScrollArea#pageScrollArea > QWidget > QWidget { background: $window; }
    QLabel { background: transparent; }
    QWidget#sidebarNavigation { background: $base; border-right: 1px solid $panel_border; }
    QWidget#applicationIdentity { background: transparent; }
    QLabel#identityMonogram { min-width: __BADGE_WIDTH__px; min-height: __BADGE_HEIGHT__px; max-width: __BADGE_WIDTH__px; max-height: __BADGE_HEIGHT__px; border: __BADGE_BORDER__px solid __BORDER_COLOR__; border-radius: __BADGE_RADIUS__px; background: __BACKGROUND_COLOR__; color: __FOREGROUND_COLOR__; font-size: 11px; font-weight: 700; qproperty-alignment: AlignCenter; }
    QLabel#identityName { font-size: 15px; font-weight: 700; color: #f4f7fb; }
    QWidget#navigationCategory { background: transparent; }
    QLabel#navigationGroup { color: $disabled_text; font-size: 10px; font-weight: 600; }
    QFrame#navigationDivider { border: 0; background: $panel_border; }
    QPushButton#navigationItem { text-align: left; min-height: 34px; padding: 0 ${button_padding}px; border: 1px solid transparent; border-left: ${navigation_marker_width}px solid transparent; border-radius: ${control_radius}px; background: transparent; color: $muted_text; }
    QPushButton#navigationItem:hover { background: $surface; }
    QPushButton#navigationItem:pressed { background: $navigation_pressed; }
    QPushButton#navigationItem:checked { background: $navigation_active; border-left-color: $accent; color: $white; font-weight: 600; }
    QPushButton#navigationItem:checked:hover, QPushButton#navigationItem:checked:pressed { background: $navigation_pressed; }
    QPushButton#navigationItem:focus { border-color: $focus; }
    QPushButton#navigationItem:checked:focus { border-left-color: $accent; }
    QPushButton#navigationItem:disabled { color: $disabled_text; background: transparent; border-color: transparent; }
    QPushButton#navigationItem:checked:disabled { background: $navigation_active; border-left-color: $disabled_border; }
    QPushButton#navigationUtility { text-align: left; min-height: 30px; padding: 0 ${button_padding}px; border: 1px solid transparent; border-left: ${navigation_marker_width}px solid transparent; border-radius: ${control_radius}px; background: transparent; color: $muted_text; }
    QPushButton#navigationUtility:hover { background: $surface; color: $white; }
    QPushButton#navigationUtility:pressed { background: $navigation_pressed; }
    QPushButton#navigationUtility:focus { border-color: $focus; }
    QPushButton#navigationUtility:disabled { color: $disabled_text; background: transparent; border-color: transparent; }
    QFrame#pageHeader > QLabel#pageTitle { font-size: ${page_title_font_size}px; font-weight: 650; color: $text; }
    QLabel#aboutTitle { font-size: 20px; font-weight: 700; color: #f4f7fb; }
    QLabel#aboutDetails, QLabel#aboutLinks, QLabel#aboutCopyright, QLabel#aboutSignatureNotice { color: $muted_text; }
    QLabel#aboutLinks { link-color: $focus; }
    QFrame#pageHeader > QLabel#pageDescription { color: $muted_text; font-size: ${description_font_size}px; }
    QScrollArea#pageScrollArea { background: transparent; }
    QGroupBox { background: $surface; border: 1px solid $panel_border; border-radius: ${panel_radius}px; margin-top: ${panel_spacing}px; padding-top: ${panel_spacing}px; font-weight: 600; }
    QGroupBox::title { subcontrol-origin: margin; left: ${panel_spacing}px; padding: 0 5px; }
    QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget { background: $base; color: $input_text; border: 1px solid $border; border-radius: ${control_radius}px; selection-background-color: $selection; }
    QLineEdit, QComboBox { min-height: ${control_height}px; padding: 0 ${input_padding}px; }
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QTableWidget:focus { border-color: $input_focus; }
    QLineEdit:disabled, QComboBox:disabled, QPushButton:disabled { color: $disabled_text; background: $disabled_surface; border-color: $disabled_border; }
    QLineEdit[readOnly="true"] { color: #c2ccd8; }
    QCheckBox { spacing: 7px; color: $text; }
    QCheckBox:hover { color: $white; }
    QCheckBox:disabled { color: $disabled_text; }
    QHeaderView::section { background: $raised_surface; color: #dce4ee; border: 0; border-right: 1px solid $border; border-bottom: 1px solid $border; padding: 7px; font-weight: 600; }
    QTableView, QTableWidget { background: $base; alternate-background-color: $table_alternate; color: $input_text; border: 1px solid $panel_border; border-radius: ${control_radius}px; gridline-color: $table_grid; selection-background-color: $selection; selection-color: $white; }
    QTableView:focus, QTableWidget:focus { border-color: $input_focus; }
    QTableView:disabled, QTableWidget:disabled { color: $disabled_text; background: $disabled_surface; border-color: $disabled_border; }
    QTableView QHeaderView::section { background: $raised_surface; color: $text; border: 0; border-right: 1px solid $table_grid; border-bottom: 1px solid $panel_border; padding: ${table_header_padding}px ${input_padding}px; font-weight: 600; }
    QTableView QHeaderView::section:disabled { background: $disabled_surface; color: $disabled_text; }
    QTableView QTableCornerButton::section { background: $raised_surface; border: 0; border-right: 1px solid $table_grid; border-bottom: 1px solid $panel_border; }
    QTableView::item:hover:!selected { background: $table_hover; }
    QTableView::item:selected { background: $selection; color: $white; }
    QTableView::item:focus { border: 1px solid $focus; }
    QTableView::item:selected:disabled { background: $disabled_surface; color: $disabled_text; }
    QPushButton { min-height: ${control_height}px; padding: 0 ${button_padding}px; border: 1px solid #435064; border-radius: ${control_radius}px; background: $button; color: $button_text; }
    QPushButton:hover { background: $button_hover; }
    QPushButton:focus { border-color: $focus; }
    QPushButton[role="primary"] { background: $primary; border-color: $accent; color: white; font-weight: 600; }
    QPushButton[role="primary"]:hover { background: #3078b2; }
    QPushButton[role="destructive"] { color: $danger_text; border-color: $danger_border; background: $danger_surface; }
    QPushButton[role="attention"] { color: $warning_text; border-color: $warning_border; background: $warning_surface; font-weight: 600; }
    QPushButton[role="attention"]:hover { background: #433724; border-color: #a68040; }
    QProgressBar { min-height: 16px; border: 1px solid $border; border-radius: ${indicator_radius}px; text-align: center; background: $base; }
    QProgressBar::chunk { background: $accent; }
    QTabWidget::pane { border: 1px solid $border; }
    QTabBar::tab { background: #1c2430; padding: 7px 12px; }
    QTabBar::tab:selected { background: #2a3d52; }
    QScrollBar:vertical { background: $base; width: 12px; margin: 0; }
    QScrollBar::handle:vertical { background: $neutral_indicator; min-height: 28px; border-radius: 5px; }
    QScrollBar::handle:vertical:hover { background: #5b6d83; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar:horizontal { background: $base; height: 12px; margin: 0; }
    QScrollBar::handle:horizontal { background: $neutral_indicator; min-width: 28px; border-radius: 5px; }
    QScrollBar::handle:horizontal:hover { background: #5b6d83; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QToolTip { color: $input_text; background: $raised_surface; border: 1px solid #536176; padding: 4px; }
    """
    return (
        Template(stylesheet).substitute(COLORS, **METRICS)
        .replace("__BADGE_WIDTH__", str(MONOGRAM_BADGE_WIDTH))
        .replace("__BADGE_HEIGHT__", str(MONOGRAM_BADGE_HEIGHT))
        .replace("__BADGE_BORDER__", str(MONOGRAM_BADGE_BORDER))
        .replace("__BADGE_RADIUS__", str(MONOGRAM_BADGE_RADIUS))
        .replace("__BACKGROUND_COLOR__", MONOGRAM_BACKGROUND)
        .replace("__BORDER_COLOR__", MONOGRAM_BORDER)
        .replace("__FOREGROUND_COLOR__", MONOGRAM_FOREGROUND)
    )


def section_title_stylesheet() -> str:
    """Estilo local compartilhado, sem ampliar os seletores globais de QLabel."""
    return f"font-size: {METRICS['section_font_size']}px; font-weight: 600;"
