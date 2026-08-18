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


WINDOW_COLOR = "#151a22"
TEXT_COLOR = "#e8edf5"
BASE_COLOR = "#10161e"
DISABLED_TEXT_COLOR = "#8592a3"
HIGHLIGHT_COLOR = "#315b82"
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
            background = QColor("#1a2029")
            border = QColor("#46515f")
            mark = QColor(DISABLED_TEXT_COLOR)
        elif checked or partial:
            background = QColor("#27669a")
            border = QColor("#79a8d8")
            mark = QColor("#ffffff")
        else:
            background = QColor("#1c2531" if hovered else BASE_COLOR)
            border = QColor("#79a8d8" if hovered else "#6f7f91")
            mark = QColor("#ffffff")

        rect = option.rect.adjusted(1, 1, -1, -1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(border, 1.25))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 3, 3)

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
        QPalette.ColorRole.AlternateBase: "#1a212b",
        QPalette.ColorRole.ToolTipBase: "#222b37",
        QPalette.ColorRole.ToolTipText: "#edf2f7",
        QPalette.ColorRole.Text: "#edf2f7",
        QPalette.ColorRole.Button: "#252e3a",
        QPalette.ColorRole.ButtonText: "#e5ebf3",
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: HIGHLIGHT_COLOR,
        QPalette.ColorRole.HighlightedText: "#ffffff",
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
    QMainWindow, QDialog, QMessageBox { background: #151a22; color: #e8edf5; }
    QWidget { color: #e8edf5; }
    QWidget#mainWorkspace, QWidget#pageShell, QWidget[pageContent="true"] { background: #151a22; }
    QScrollArea#pageScrollArea, QScrollArea#pageScrollArea > QWidget > QWidget { background: #151a22; }
    QLabel { background: transparent; }
    QWidget#sidebarNavigation { background: #10151d; border-right: 1px solid #2a3442; }
    QWidget#applicationIdentity { background: transparent; }
    QLabel#identityMonogram { min-width: __BADGE_WIDTH__px; min-height: __BADGE_HEIGHT__px; max-width: __BADGE_WIDTH__px; max-height: __BADGE_HEIGHT__px; border: __BADGE_BORDER__px solid __BORDER_COLOR__; border-radius: __BADGE_RADIUS__px; background: __BACKGROUND_COLOR__; color: __FOREGROUND_COLOR__; font-size: 11px; font-weight: 700; qproperty-alignment: AlignCenter; }
    QLabel#identityName { font-size: 15px; font-weight: 700; color: #f4f7fb; }
    QWidget#navigationCategory { background: transparent; }
    QLabel#navigationGroup { color: #94a5b8; font-size: 10px; font-weight: 650; }
    QFrame#navigationDivider { color: #334050; background: transparent; }
    QPushButton#navigationItem { text-align: left; min-height: 34px; padding: 0 14px; border: 1px solid transparent; border-radius: 5px; background: transparent; color: #d2dbe6; }
    QPushButton#navigationItem:hover { background: #1c2531; }
    QPushButton#navigationItem:checked { background: #22354b; border-color: #3b6b99; color: #ffffff; font-weight: 600; }
    QPushButton#navigationItem:focus { border-color: #79a8d8; }
    QPushButton#navigationUtility { text-align: left; min-height: 30px; padding: 0 14px; border: 1px solid transparent; background: transparent; color: #aebac8; }
    QPushButton#navigationUtility:hover { background: #1c2531; color: #ffffff; }
    QPushButton#navigationUtility:focus { border-color: #79a8d8; }
    QLabel#pageTitle { font-size: 22px; font-weight: 650; color: #f4f7fb; }
    QLabel#aboutTitle { font-size: 20px; font-weight: 700; color: #f4f7fb; }
    QLabel#aboutDetails, QLabel#aboutLinks, QLabel#aboutCopyright, QLabel#aboutSignatureNotice { color: #bdc8d5; }
    QLabel#aboutLinks { link-color: #79a8d8; }
    QLabel#pageDescription { color: #bdc8d5; font-size: 13px; }
    QScrollArea#pageScrollArea { background: transparent; }
    QGroupBox { background: #1a212b; border: 1px solid #303b49; border-radius: 6px; margin-top: 10px; padding-top: 10px; font-weight: 600; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
    QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget { background: #10161e; color: #edf2f7; border: 1px solid #354253; border-radius: 4px; selection-background-color: #315b82; }
    QLineEdit, QComboBox { min-height: 30px; padding: 0 7px; }
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QTableWidget:focus { border-color: #6f9bc8; }
    QLineEdit:disabled, QComboBox:disabled, QPushButton:disabled { color: #8592a3; background: #1a2029; border-color: #303a47; }
    QLineEdit[readOnly="true"] { color: #c2ccd8; }
    QCheckBox { spacing: 7px; color: #e8edf5; }
    QCheckBox:hover { color: #ffffff; }
    QCheckBox:disabled { color: #8592a3; }
    QHeaderView::section { background: #222b37; color: #dce4ee; border: 0; border-right: 1px solid #354253; border-bottom: 1px solid #354253; padding: 7px; font-weight: 600; }
    QPushButton { min-height: 30px; padding: 0 12px; border: 1px solid #435064; border-radius: 4px; background: #252e3a; color: #e5ebf3; }
    QPushButton:hover { background: #303b49; }
    QPushButton:focus { border-color: #79a8d8; }
    QPushButton[role="primary"] { background: #27669a; border-color: #347db8; color: white; font-weight: 600; }
    QPushButton[role="primary"]:hover { background: #3078b2; }
    QPushButton[role="destructive"] { color: #ffb4ae; border-color: #854842; background: #352322; }
    QPushButton[role="attention"] { color: #f3d29b; border-color: #8b6b35; background: #342c20; font-weight: 600; }
    QPushButton[role="attention"]:hover { background: #433724; border-color: #a68040; }
    QProgressBar { min-height: 16px; border: 1px solid #354253; border-radius: 3px; text-align: center; background: #10161e; }
    QProgressBar::chunk { background: #347db8; }
    QTabWidget::pane { border: 1px solid #354253; }
    QTabBar::tab { background: #1c2430; padding: 7px 12px; }
    QTabBar::tab:selected { background: #2a3d52; }
    QScrollBar:vertical { background: #10161e; width: 12px; margin: 0; }
    QScrollBar::handle:vertical { background: #465569; min-height: 28px; border-radius: 5px; }
    QScrollBar::handle:vertical:hover { background: #5b6d83; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar:horizontal { background: #10161e; height: 12px; margin: 0; }
    QScrollBar::handle:horizontal { background: #465569; min-width: 28px; border-radius: 5px; }
    QScrollBar::handle:horizontal:hover { background: #5b6d83; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QToolTip { color: #edf2f7; background: #222b37; border: 1px solid #536176; padding: 4px; }
    """
    return (
        stylesheet.replace("__BADGE_WIDTH__", str(MONOGRAM_BADGE_WIDTH))
        .replace("__BADGE_HEIGHT__", str(MONOGRAM_BADGE_HEIGHT))
        .replace("__BADGE_BORDER__", str(MONOGRAM_BADGE_BORDER))
        .replace("__BADGE_RADIUS__", str(MONOGRAM_BADGE_RADIUS))
        .replace("__BACKGROUND_COLOR__", MONOGRAM_BACKGROUND)
        .replace("__BORDER_COLOR__", MONOGRAM_BORDER)
        .replace("__FOREGROUND_COLOR__", MONOGRAM_FOREGROUND)
    )
