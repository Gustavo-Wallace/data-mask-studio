def application_stylesheet() -> str:
    return """
    QMainWindow, QWidget { background: #151a22; color: #e8edf5; }
    QWidget#sidebarNavigation { background: #10151d; border-right: 1px solid #2a3442; }
    QLabel#navigationBrand { font-size: 17px; font-weight: 700; color: #f4f7fb; }
    QLabel#navigationGroup { color: #9ba9ba; font-size: 11px; font-weight: 700; padding: 4px 8px; }
    QPushButton#navigationItem { text-align: left; padding: 9px 11px; border: 1px solid transparent; border-radius: 5px; background: transparent; color: #cbd5e1; }
    QPushButton#navigationItem:hover { background: #1c2531; }
    QPushButton#navigationItem:checked { background: #22354b; border-color: #3b6b99; color: #ffffff; font-weight: 600; }
    QPushButton#navigationItem:focus { border-color: #79a8d8; }
    QLabel#pageTitle { font-size: 22px; font-weight: 650; color: #f4f7fb; }
    QLabel#pageDescription { color: #aeb9c8; font-size: 13px; }
    QScrollArea#pageScrollArea { background: transparent; }
    QGroupBox { background: #1a212b; border: 1px solid #303b49; border-radius: 6px; margin-top: 10px; padding-top: 10px; font-weight: 600; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
    QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget { background: #10161e; color: #edf2f7; border: 1px solid #354253; border-radius: 4px; selection-background-color: #315b82; }
    QLineEdit, QComboBox { min-height: 30px; padding: 0 7px; }
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QTableWidget:focus { border-color: #6f9bc8; }
    QLineEdit:disabled, QComboBox:disabled, QPushButton:disabled { color: #707c8c; background: #1a2029; }
    QHeaderView::section { background: #222b37; color: #dce4ee; border: 0; border-right: 1px solid #354253; border-bottom: 1px solid #354253; padding: 7px; font-weight: 600; }
    QPushButton { min-height: 30px; padding: 0 12px; border: 1px solid #435064; border-radius: 4px; background: #252e3a; color: #e5ebf3; }
    QPushButton:hover { background: #303b49; }
    QPushButton:focus { border-color: #79a8d8; }
    QPushButton[role="primary"] { background: #27669a; border-color: #347db8; color: white; font-weight: 600; }
    QPushButton[role="primary"]:hover { background: #3078b2; }
    QPushButton[role="destructive"] { color: #ffb4ae; border-color: #854842; background: #352322; }
    QProgressBar { min-height: 16px; border: 1px solid #354253; border-radius: 3px; text-align: center; background: #10161e; }
    QProgressBar::chunk { background: #347db8; }
    QTabWidget::pane { border: 1px solid #354253; }
    QTabBar::tab { background: #1c2430; padding: 7px 12px; }
    QTabBar::tab:selected { background: #2a3d52; }
    """
