import sys
import os
import traceback
import logging
import faulthandler

#os.environ.setdefault("QT_QPA_PLATFORMTHEME", "xdgdesktopportal")
os.environ.setdefault("QT_QPA_PLATFORMTHEME", "generic")
os.environ["QT_LOGGING_RULES"] = "qt.qpa.services.warning=false;qt.qpa.portal.warning=false;qt.qpa.wayland.textinput=false"

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
from gui import MacFlashGUI

if getattr(sys, "frozen", False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

# faulthandler — дамп C++ стека при segfault в Data/fault.log
_fault_path = os.path.join(base_path, "Data", "fault.log")
try:
    _fault_file = open(_fault_path, "w")
    faulthandler.enable(file=_fault_file)
except Exception:
    faulthandler.enable()

# ------------------------------------------------------------------ #
# Глобальный перехватчик необработанных исключений
# ------------------------------------------------------------------ #
_log_path = os.path.join(base_path, "Data", "launcher.log")

def _setup_logging():
    os.makedirs(os.path.join(base_path, "Data"), exist_ok=True)
    logging.basicConfig(
        filename=_log_path,
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )

def _show_crash_dialog(title: str, message: str):
    """Показать диалог ошибки, если Qt-приложение живо."""
    try:
        app = QApplication.instance()
        if app is not None:
            msg = QMessageBox()
            msg.setWindowTitle(title)
            msg.setText(message[:1000])
            msg.setIcon(QMessageBox.Critical)
            msg.exec()
    except Exception:
        pass

def _handle_exception(exc_type, exc_value, exc_tb):
    """sys.excepthook — ловит все необработанные исключения в главном потоке."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logging.critical("Необработанное исключение:\n%s", text)
    print(text, file=sys.stderr)
    _show_crash_dialog("Критическая ошибка", f"{exc_type.__name__}: {exc_value}")

def _handle_thread_exception(args):
    """threading.excepthook — ловит исключения в daemon-потоках."""
    if args.exc_type is SystemExit:
        return
    text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_tb))
    logging.critical("Исключение в потоке %s:\n%s", args.thread, text)
    print(text, file=sys.stderr)


def main():
    _setup_logging()
    sys.excepthook = _handle_exception
    import threading
    threading.excepthook = _handle_thread_exception

    app = QApplication(sys.argv)
    icon_path = os.path.join(base_path, "Data", "run.svg")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(icon_path))
    app.setApplicationName("MacFlashUtility")
    app.setApplicationDisplayName("MacFlashUtility")
    app.setDesktopFileName("MacFlashUtility")
    app.setOrganizationName("pcuser")

    try:
        gui = MacFlashGUI(base_path)
        gui.setWindowIcon(QIcon(icon_path))
        gui.show()
    except Exception:
        text = traceback.format_exc()
        logging.critical("Ошибка запуска MacFlashGUI:\n%s", text)
        _show_crash_dialog("Ошибка запуска", text)
        sys.exit(1)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()