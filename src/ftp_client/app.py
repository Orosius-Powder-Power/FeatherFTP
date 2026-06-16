from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def choose_ui_font() -> QFont:
    preferred = [
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "SimHei",
        "SimSun",
        "Arial",
    ]
    families = set(QFontDatabase.families())
    for family in preferred:
        if family in families:
            font = QFont(family, 10)
            font.setStyleStrategy(QFont.PreferDefault)
            return font
    return QFont("Sans Serif", 10)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Socket FTP Client")
    app.setOrganizationName("Computer Networks Practice")
    app.setFont(choose_ui_font())
    window = MainWindow()
    window.show()
    return app.exec()
