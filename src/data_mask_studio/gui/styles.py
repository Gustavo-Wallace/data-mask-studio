from data_mask_studio.branding import (
    MONOGRAM_BACKGROUND,
    MONOGRAM_BADGE_BORDER,
    MONOGRAM_BADGE_HEIGHT,
    MONOGRAM_BADGE_RADIUS,
    MONOGRAM_BADGE_WIDTH,
    MONOGRAM_BORDER,
    MONOGRAM_FOREGROUND,
)


def application_stylesheet() -> str:
    stylesheet = """
    QMainWindow { background: #151a22; }
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
    QLabel#aboutDetails, QLabel#aboutLinks, QLabel#aboutSignatureNotice { color: #bdc8d5; }
    QLabel#pageDescription { color: #bdc8d5; font-size: 13px; }
    QScrollArea#pageScrollArea { background: transparent; }
    QGroupBox { background: #1a212b; border: 1px solid #303b49; border-radius: 6px; margin-top: 10px; padding-top: 10px; font-weight: 600; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
    QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget { background: #10161e; color: #edf2f7; border: 1px solid #354253; border-radius: 4px; selection-background-color: #315b82; }
    QLineEdit, QComboBox { min-height: 30px; padding: 0 7px; }
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QTableWidget:focus { border-color: #6f9bc8; }
    QLineEdit:disabled, QComboBox:disabled, QPushButton:disabled { color: #8592a3; background: #1a2029; border-color: #303a47; }
    QLineEdit[readOnly="true"] { color: #c2ccd8; }
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
