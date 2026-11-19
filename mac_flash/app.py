"""Small standalone GUI for writing a manually supplied MAC to U-Boot."""
import os
import re
import sys
import threading

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

# ssh.py lives in the project root; this app remains isolated in mac_flash/.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ssh import flash_mac_direct  # noqa: E402
from log import fix_layout  # noqa: E402
from style import apply_dark_style  # noqa: E402


def normalize_mac(value: str) -> str:
    """Correct Russian keyboard layout and return a normalized, validated MAC."""
    corrected = fix_layout((value or "").strip())
    compact = re.sub(r"[:-]", "", corrected)
    if not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
        raise ValueError("Введите MAC из 12 шестнадцатеричных символов (например, 00:11:22:AA:BB:CC).")
    compact = compact.lower()
    return ":".join(compact[i:i + 2] for i in range(0, 12, 2))


class FlashSignals(QObject):
    log = Signal(str)
    finished = Signal(object)


class MacFlashWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Прямая прошивка MAC")
        self.setMinimumSize(560, 360)
        self.setFont(QFont("Consolas", 10))
        apply_dark_style(self)
        self.signals = FlashSignals()
        self.signals.log.connect(self.append_log)
        self.signals.finished.connect(self.on_finished)
        self.worker = None
        self._blink_state = False
        self._blink_color = "blue"
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(450)
        self._blink_timer.timeout.connect(self._toggle_input_blink)

        layout = QVBoxLayout(self)
        title = QLabel("Сканируйте или введите MAC-адрес")
        title.setStyleSheet("color: #9bc7ff; font-size: 14pt; font-weight: 700; padding: 4px 0;")
        layout.addWidget(title)
        self.mac_field = QLineEdit()
        self.mac_field.setPlaceholderText("00:11:22:AA:BB:CC")
        self.mac_field.setClearButtonEnabled(True)
        self.mac_field.returnPressed.connect(self.start_flash)
        self.mac_field.textChanged.connect(self._stop_input_blink)
        layout.addWidget(self.mac_field)

        row = QHBoxLayout()
        self.status = QLabel("Ожидание MAC")
        self.flash_button = QPushButton("Прошить MAC")
        self.flash_button.clicked.connect(self.start_flash)
        row.addWidget(self.status)
        row.addStretch()
        row.addWidget(self.flash_button)
        layout.addLayout(row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Здесь появится ход подключения и прошивки.")
        layout.addWidget(self.log)
        QTimer.singleShot(0, self.mac_field.setFocus)

    def append_log(self, message):
        self.log.appendPlainText(str(message))

    def start_flash(self):
        try:
            mac = normalize_mac(self.mac_field.text())
        except ValueError:
            self._prepare_for_next_mac(color="red")
            return

        self.mac_field.setText(mac)
        self.flash_button.setEnabled(False)
        self.status.setText(f"Прошивка {mac}…")
        self.append_log(f"Начата прошивка MAC: {mac}")

        def log(message):
            self.signals.log.emit(str(message))

        def run():
            try:
                result = flash_mac_direct(
                    wan_mac=mac,
                    reboot=True,
                    log=log,
                )
            except Exception as exc:
                result = {"ok": False, "msg": str(exc)}
            self.signals.finished.emit(result or {"ok": False, "msg": "Пустой результат прошивки"})

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def on_finished(self, result):
        ok = bool(result.get("ok"))
        message = result.get("msg", "")
        self.append_log(("Готово: " if ok else "Ошибка: ") + str(message))
        self.status.setText("Готово — сканируйте следующий MAC" if ok else "Не удалось прошить — можно повторить")
        self.flash_button.setEnabled(True)

        self._prepare_for_next_mac()

    def _prepare_for_next_mac(self, color="blue"):
        self.mac_field.clear()
        self.mac_field.setFocus()
        self._blink_state = False
        self._blink_color = color
        self._blink_timer.start()

    def _stop_input_blink(self, text):
        if text:
            self._blink_timer.stop()
            self.mac_field.setStyleSheet("")

    def _toggle_input_blink(self):
        self._blink_state = not self._blink_state
        if self._blink_state:
            background, border = (
                ("#4a2024", "#ff6666") if self._blink_color == "red"
                else ("#263a55", "#58a6ff")
            )
            self.mac_field.setStyleSheet(
                f"QLineEdit {{ background-color: {background}; border: 2px solid {border}; "
                "border-radius: 6px; padding: 5px 7px; color: #f0f6ff; }"
            )
        else:
            self.mac_field.setStyleSheet("")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MacFlashWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
