from PySide6.QtWidgets import QPlainTextEdit, QWidget, QVBoxLayout, QPushButton, QLabel
from PySide6.QtGui import QTextCharFormat, QColor, QFont, QTextCursor
from PySide6.QtCore import Qt, QTimer
import re
import qtawesome as qta

def fix_layout(text: str) -> str:
    layout = dict(zip(
        "йцукенгшщзхъфывапролджэячсмитьбюЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ",
        "qwertyuiop[]asdfghjkl;'zxcvbnm,.QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM,."
    ))
    return "".join(layout.get(c, c) for c in text)

def instruction():
    return {
            "eeprom_spi": {
                "title": "Инструкция: EEPROM SPI",
                "text": "1) Сканируйте S/N.\n2) Убедитесь, что найден MAC.\n3) Дождитесь завершения прошивки и postcheck.\n4) Сканируйте B/N (или используйте авто-режим)."
            },
            "u-boot_environment": {
                "title": "Инструкция: U-Boot Environment",
                "text": "1) Сканируйте S/N.\n2) Проверьте, что устройство доступно по сети.\n3) Дождитесь записи eth1addr и перезагрузки.\n4) Выполнится postcheck."
            }
        }

class CustomContextMenu(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setStyleSheet("""
            background-color: #2b2d31;
            border: 1px solid #3a3a3a;
            border-radius: 8px;
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.btn_copy = QPushButton()
        self.btn_copy.setIcon(qta.icon("fa5s.copy", color="#e1e1e1"))
        self.btn_copy.setToolTip("Копировать")
        self.btn_select_all = QPushButton()
        self.btn_select_all.setIcon(qta.icon("fa5s.crosshairs", color="#e1e1e1"))
        self.btn_select_all.setToolTip("Выделить всё")
        self.btn_clear = QPushButton()
        self.btn_clear.setIcon(qta.icon("fa5s.trash-alt", color="#e1e1e1"))
        self.btn_clear.setToolTip("Очистить лог")

        for btn in (self.btn_copy, self.btn_select_all, self.btn_clear):
            btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #e1e1e1;
                    padding: 6px 20px;
                    text-align: left;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #3c3f44;
                }
            """)
            layout.addWidget(btn)

        self.setLayout(layout)
        self.adjustSize()

class LogWidget(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("JetBrains Mono", 10))
        self.setStyleSheet("""
            QPlainTextEdit {
                background-color: #121314;
                color: #dcdcdc;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
                padding: 6px;
            }
        """)

        self._tooltip = QLabel(self)
        self._tooltip.setStyleSheet("""
            QLabel {
                background-color: rgba(50,50,50,220);
                color: #ffffff;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 10pt;
            }
        """)
        self._tooltip.setAlignment(Qt.AlignCenter)
        self._tooltip.hide()

    def show_temp_tooltip(self, text, duration=1500):
        """Показывает временную подсказку по центру нижней части виджета."""
        self._tooltip.setText(text)
        self._tooltip.adjustSize()
        w, h = self._tooltip.width(), self._tooltip.height()
        self._tooltip.move((self.width()-w)//2, self.height()-h-10)  # 10px от низа
        self._tooltip.show()
        QTimer.singleShot(duration, self._tooltip.hide)

    def clear_log(self):
        self.clear()
        self.show_temp_tooltip("Лог очищен")

    def contextMenuEvent(self, event):
        menu = CustomContextMenu(self)
        pos = self.mapToGlobal(event.pos())
        menu.move(pos)
        menu.show()

        menu.btn_copy.clicked.connect(lambda: self._menu_action(menu, self.copy, "Скопировано"))
        menu.btn_select_all.clicked.connect(lambda: self._menu_action(menu, self.selectAll, "Выделено всё"))
        menu.btn_clear.clicked.connect(lambda: self._menu_action(menu, self.clear_log))

    def _menu_action(self, menu_widget, func, tooltip_text=None):
        """Выполняет действие кнопки, закрывает меню, выводит подсказку."""
        func()
        menu_widget.close()
        if tooltip_text:
            self.show_temp_tooltip(tooltip_text)

    def append_log(self, text: str):
        COLOR_MAP = {
            "OK": "#66cc66",
            "GS": "#00c853",
            "INFO": "#4da6ff",
            "WAIT": "#4da6ff",
            "→": "#4da6ff",
            "STEP": "#ffaa00",
            "FLASH": "#ffaa00",
            "BUSY": "#ffaa00",
            "SCP": "#ffaa00",
            "SSH": "#ffaa00",
            "SPI": "#ffaa00",
            "WARN": "#ffcc00",
            "ERR": "#ff5555",
            "FAIL": "#ff5555",
            "EEPROM mismatch": "#ff5555",
            "+": "#cccccc",
            "DEBUG": "#D400FF",
        }

        color = "#dcdcdc"
        prefixes = re.findall(r"\[([A-Z]+)\]", text)
        message = re.sub(r"(\[[A-Z]+\])+ ?", "", text).strip()

        for p in prefixes:
            if p in COLOR_MAP:
                color = COLOR_MAP[p]
                if p in ("ERR", "WARN", "FAIL"):
                    break

        if "EEPROM mismatch" in text:
            color = COLOR_MAP["EEPROM mismatch"]

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(message + "\n", fmt)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    append = append_log
