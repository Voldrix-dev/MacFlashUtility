import threading, traceback
import subprocess, time
from PySide6.QtCore import QObject, Signal

class WorkerSignals(QObject):
    finished = Signal(object)
    log = Signal(str)

def run_in_thread(fn, args=(), kwargs=None, signals: WorkerSignals = None):
    if kwargs is None:
        kwargs = {}
    def _target():
        try:
            res = fn(*args, **kwargs)
            if signals:
                signals.finished.emit({"ok": True, "result": res})
        except Exception as e:
            trace = traceback.format_exc()
            if signals:
                signals.log.emit(f"[EXC] {e}\n{trace}")
                signals.finished.emit({"ok": False, "exc": e, "trace": trace})
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t

class DeviceWatcher(QObject):
    status_changed = Signal(bool)  # True = online, False = offline
    def __init__(self, host="15.15.15.15", interval=2):
        super().__init__()
        self.host = host
        self.interval = interval
        self.mode = "eeprom_spi"
        self._running = False
        self._last_state = None

    def set_mode(self, mode: str):
        self.mode = mode or "eeprom_spi"
        self._last_state = None

    def start(self):
        self._running = True
        while self._running:
            online = self._ping_device()
            if online != self._last_state:
                self._last_state = online
                self.status_changed.emit(online)
            time.sleep(self.interval)

    def stop(self):
        self._running = False

    def _ping_device(self):
        try:
            result = subprocess.run(["ping", "-c", "1", "-W", "1", self.host], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return result.returncode == 0
        except Exception:
            return False
