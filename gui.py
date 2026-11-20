import os, time
import logging
import threading
import json
import subprocess
import qtawesome as qta
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QMetaObject, QCoreApplication
from PySide6.QtGui import QFont, QAction
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton, QMenuBar,
                               QDialog, QMessageBox, QInputDialog, QFrame, QMenu, QGraphicsDropShadowEffect)
from thread_ import WorkerSignals, DeviceWatcher, run_in_thread
from db import (check_serial, update_mac_single, update_mac_dual, add_board_column, peek_next_board_id, commit_board_id)
from option import OptionsDialog
from style_editor import StyleEditorDialog
from log import LogWidget, fix_layout, instruction
from menu import ScrollableMenu, SheetSelectDialog
from ssh import eeprom_flash, flash_mac_direct
from label import ClickableLabel
from style import apply_dark_style, St
from g_sheets import GoogleSheetsManager, GoogleWorker
from dhcp_server import DhcpServer

def _norm_mac(mac):
    """Приводим MAC к единому виду: только hex-символы в нижнем регистре."""
    return "".join(ch for ch in (mac or "") if ch in "0123456789abcdefABCDEF").lower()

def safe_single_shot(msec, func): # Безопасный вызов QTimer.singleShot в GUI-потоке.
    try:
        app = QCoreApplication.instance()
        if app is None:
            QTimer.singleShot(msec, func)
            return
        QMetaObject.invokeMethod(app, lambda: QTimer.singleShot(msec, func), Qt.QueuedConnection)
    except Exception:
        QTimer.singleShot(msec, func)

class Toast(QLabel):
    """Неблокирующее всплывающее уведомление.

    duration > 0 — само исчезает через указанное время;
    duration <= 0 — висит, пока не закроют через close_toast().
    """

    def __init__(self, parent, text, duration=6000):
        super().__init__(text, parent)
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setMaximumWidth(380)
        self.setStyleSheet(
            "QLabel { background: #262626; color: #ffcc66; border: 1px solid #cc5555;"
            " border-radius: 6px; padding: 10px 14px; font-weight: bold; }"
        )
        self.adjustSize()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._close)
        self._reposition()
        self.show()
        self.raise_()
        if duration > 0:
            self._timer.start(duration)

    def _reposition(self):
        parent = self.parentWidget()
        if parent is not None:
            geo = parent.geometry()
            self.move(max(0, geo.right() - self.width() - 16),
                      max(0, geo.bottom() - self.height() - 16))

    def close_toast(self):
        self._timer.stop()
        self._close()

    def _close(self):
        self.hide()
        self.deleteLater()

class MacFlashGUI(QWidget):
    request_select_sheet = Signal(str)
    request_open_sheet = Signal()
    request_stop_worker = Signal()
    request_add_record = Signal(str, str)
    request_fetch_sheets = Signal()
    request_check_gs_auth = Signal()
    request_delete_record = Signal(str, str)
    flash_mac_confirmed = Signal(str)
    dhcp_start_ready = Signal()
    device_skipped = Signal(str, str, str)
    def __init__(self, base_path=None):
        super().__init__()
        #self.setWindowTitle("Mac Flash Utility")
        self.setGeometry(200, 120, 820, 520)
        self.setFont(QFont("Consolas", 10))
        self.setFocusPolicy(Qt.StrongFocus) # Нужен фокус на главном виджете, иначе keyPressEvent не вызывается.
        apply_dark_style(self)
        if base_path is None:
            base_path = os.path.dirname(os.path.abspath(__file__))
        self.base_path = base_path
        self.config_path = os.path.join(self.base_path, "Data", "options.json")
        self.instructions_path = os.path.join(self.base_path, "Data", "instructions.json")
        self.options = self.load_options()
        self.author = ""
        self.buffer = ""
        self.eeprom_path = ""
        self.serial_number = ""
        self.board_number = ""
        self.wanMac = ""
        self.lanMac = ""
        self.busy = False
        self.last_result_ok = None  # True / False / None
        self.ready_for_bn = False
        self.pending_bn = None
        self.pending_sheets = None
        self._test_mode = False
        self._expected_flash_mac = None
        self._pre_flash_mac = None
        self.current_stop_event = None
        self.is_admin = False
        self.admin_session_seconds = int(self.options.get("admin_session_seconds", 600))
        self.admin_session_timer = QTimer(self)
        self.admin_session_timer.setSingleShot(True)
        self.admin_session_timer.timeout.connect(self._expire_admin_session)
        self.flash_watchdog = QTimer(self)
        self.flash_watchdog.setSingleShot(True)
        self.flash_watchdog.timeout.connect(self._on_flash_timeout)
        self.flash_timed_out = False
        self._await_dhcp_confirm = False
        self._dhcp_confirm_timer = QTimer(self)
        self._dhcp_confirm_timer.setSingleShot(True)
        self._dhcp_confirm_timer.timeout.connect(self._on_dhcp_confirm_timeout)
        self._await_dhcp_start = False
        self._last_dhcp_ack_time = 0.0
        self._dhcp_start_timer = QTimer(self)
        self._dhcp_start_timer.setSingleShot(True)
        self._dhcp_start_timer.timeout.connect(self._on_dhcp_start_timeout)
        self._dhcp_devices = {}
        self._claimed_macs = set()
        self._idle_toasts = []
        self._dhcp_check_timer = QTimer(self)
        self._dhcp_check_timer.timeout.connect(self._check_dhcp_devices)
        self._dhcp_check_timer.start(5000)
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self._signals = WorkerSignals()
        self._closing = False
        self._signals.log.connect(self.log_append)
        self.flash_mac_confirmed.connect(self._on_flash_mac_confirmed)
        self.dhcp_start_ready.connect(self._on_dhcp_start_ready)
        self.device_skipped.connect(self._on_device_skipped)
        self._build_ui()
        self.gs_manager = None
        self.gs_worker = None
        self.gs_thread = None
        self.gs_auth_timer = None
        self.gs_sheet_refresh_timer = None
        self.device_watcher_thread = QThread()
        self.device_watcher = DeviceWatcher("15.15.15.15", interval=1)
        try:
            self.device_watcher.set_mode(self.options.get("flash_mode", "eeprom_spi"))
        except Exception:
            pass
        self.device_watcher.moveToThread(self.device_watcher_thread)
        self.device_watcher_thread.started.connect(self.device_watcher.start)
        self.device_watcher.status_changed.connect(self.update_device_status)
        self.device_watcher_thread.start()
        if self.options.get("board_check", False):
            safe_single_shot(2000, lambda: run_in_thread(add_board_column, kwargs={"debug": self.options.get("debug", False)}))
        if self.options.get("run_sheets", False):
            safe_single_shot(0, self._init_google_sheets)

        self.dhcp_worker = None
        self.dhcp_host = None
        self.dhcp_mac = None
        self._last_dhcp_ack = None
        self._start_dhcp_server()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        menu_bar = QMenuBar(self)
        menu_settings = menu_bar.addMenu("Настройки")
        self.menu_sheets = ScrollableMenu(self, visible_count=6, title="Листы")
        menu_bar.addMenu(self.menu_sheets)
        act_options = QAction("Опции...", self)
        act_options.triggered.connect(self.open_options)
        menu_settings.addAction(act_options)
        act_style_editor = QAction("Редактор стиля листа...", self)
        act_style_editor.setToolTip("Визуальное редактирование conditional_format.json (ширины столбцов, правила, цвета)")
        act_style_editor.triggered.connect(self.open_style_editor)
        menu_settings.addAction(act_style_editor)
        menu_settings.addSeparator()
        self.act_admin_login = QAction("Вход администратора (системный пароль)", self)
        self.act_admin_login.triggered.connect(self.admin_login_via_pkexec)
        menu_settings.addAction(self.act_admin_login)
        self.act_admin_logout = QAction("Выход администратора", self)
        self.act_admin_logout.triggered.connect(self.admin_logout)
        self.act_admin_logout.setEnabled(False)
        menu_settings.addAction(self.act_admin_logout)
        self.act_manual_input = QAction("Ручной ввод S/N и B/N...", self)
        self.act_manual_input.triggered.connect(self.manual_input_dialog)
        self.act_manual_input.setEnabled(False)
        menu_settings.addAction(self.act_manual_input)
        self.act_require_board = QAction("Требовать B/N (админ)", self)
        self.act_require_board.setToolTip("Обязывать оператора сканировать номер платы B/N перед завершением прошивки.")
        self.act_require_board.setCheckable(True)
        self.act_require_board.setChecked(self.options.get("require_board_number", True))
        self.act_require_board.setEnabled(False)
        self.act_require_board.triggered.connect(self._on_toggle_require_board)
        menu_settings.addAction(self.act_require_board)
        self.act_send_board = QAction("Записывать номер платы (B/N) в БД/таблицу (админ)", self)
        self.act_send_board.setToolTip("Если включено — номер платы (B/N) передаётся в БД и Таблицу. Если выключено — в БД пишется NULL, а в Таблицу — дата/время. В режиме восстановления (Recovery Mode) запись заблокирована полностью.")
        self.act_send_board.setCheckable(True)
        self.act_send_board.setChecked(self.options.get("send_board_number", True))
        self.act_send_board.setEnabled(False)
        self.act_send_board.triggered.connect(self._on_toggle_send_board)
        menu_settings.addAction(self.act_send_board)
        self.btn_help = QPushButton()
        self.btn_help.setIcon(qta.icon("fa5s.question-circle", color="#11111b"))
        self.btn_help.setFixedWidth(28)
        self.btn_help.setToolTip("Инструкция по текущему режиму")
        self.btn_help.clicked.connect(self.show_mode_instruction)
        menu_bar.setCornerWidget(self.btn_help, Qt.TopRightCorner)
        main_layout.setMenuBar(menu_bar)
        self.hero = QFrame()
        self.hero.setObjectName("HeroCard")
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(16, 12, 16, 12)
        self.workflow_label = QLabel("Этап: Ожидание сканирования S/N")
        self.workflow_label.setObjectName("WorkflowLabel")
        hero_layout.addWidget(self.workflow_label)
        shadow = QGraphicsDropShadowEffect()  # Без parent: Qt берёт ownership через setGraphicsEffect — иначе double free
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 8)
        self.hero.setGraphicsEffect(shadow)
        main_layout.addWidget(self.hero)
        author_layout = QHBoxLayout()
        self.author_field = QLineEdit()
        St._style_field(self.author_field)
        self.author_field.setPlaceholderText("Введите имя оператора")
        self.author_field.setFocusPolicy(Qt.ClickFocus)
        self.author_field.setToolTip("Оператор, выполняющий запись\nПри необходимости измените!")
        self.author_field.setFixedWidth(180)
        self.btn_reset = QPushButton()
        self.btn_reset.setIcon(qta.icon("fa5s.undo", color="#11111b"))
        self.btn_reset.setToolTip("Сбросить поля")
        self.btn_reset.clicked.connect(self.reset_fields)
        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(qta.icon("fa5s.stop", color="#11111b"))
        self.btn_stop.setToolTip("Остановить текущую операцию")
        self.btn_stop.clicked.connect(self.stop_current_operation)
        self.btn_stop.setEnabled(False)
        author_layout.addWidget(self.author_field)
        author_layout.addStretch()
        author_layout.addWidget(self.btn_stop)
        author_layout.addWidget(self.btn_reset)
        main_layout.addLayout(author_layout)
        saved_author = self.options.get("author", "")
        if saved_author:
            self.author_field.setText(saved_author)
        self.author = saved_author.strip()
        self.author_field.textChanged.connect(self._update_author_option)
        self.author_field.textChanged.connect(self.update_author)
        label_layout = QHBoxLayout()
        self.sn_label = QLabel("Serial:")
        self.bn_label = QLabel("Board:")
        label_layout.addWidget(self.sn_label)
        label_layout.addWidget(self.bn_label)
        main_layout.addLayout(label_layout)
        field_layout = QHBoxLayout()
        self.sn_field = QLineEdit()
        St._style_field(self.sn_field)
        self.sn_field.setReadOnly(True)
        self.sn_field.setFocusPolicy(Qt.NoFocus)
        self.sn_field.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sn_field.customContextMenuRequested.connect(
            lambda pos: self._show_manual_field_menu("sn", self.sn_field.mapToGlobal(pos)))
        self.bn_field = QLineEdit()
        St._style_field(self.bn_field)
        self.bn_field.setReadOnly(True)
        self.bn_field.setFocusPolicy(Qt.NoFocus)
        self.bn_field.setContextMenuPolicy(Qt.CustomContextMenu)
        self.bn_field.customContextMenuRequested.connect(
            lambda pos: self._show_manual_field_menu("bn", self.bn_field.mapToGlobal(pos)))
        field_layout.addWidget(self.sn_field)
        field_layout.addWidget(self.bn_field)
        main_layout.addLayout(field_layout)
        log_panel = QHBoxLayout()
        self.sn_info = QLabel()
        self.update_sn_info(" Not serial ", False)
        self.gs = QLabel("●")
        self.current_sheet_label = QLabel("Лист: не выбран")
        self.current_sheet_label.setToolTip("Текущий активный лист Google Sheets")
        self.gs_lbl = ClickableLabel("Google Sheets")
        self.gs_lbl.clicked.connect(self.on_gs_label_click)
        log_panel.addWidget(self.sn_info)
        log_panel.addStretch()
        skip_caption = QLabel("Пропуск:")
        self.skip_status_label = QLabel("●")
        St.status_level(self.skip_status_label, "green")
        self.skip_status_label.setToolTip("Нет пропущенных устройств")
        log_panel.addWidget(skip_caption)
        log_panel.addWidget(self.skip_status_label)
        log_panel.addStretch()
        log_panel.addWidget(self.current_sheet_label)
        log_panel.addWidget(self.gs_lbl)
        log_panel.addWidget(self.gs)
        main_layout.addLayout(log_panel)
        self.log = LogWidget()
        self.log.setReadOnly(True)
        self.log.setFocusPolicy(Qt.NoFocus) # Лог не должен перехватывать фокус клавиатуры.
        main_layout.addWidget(self.log)
        status_layout = QHBoxLayout()
        conn = QLabel("Connected")
        self.device_status_label = QLabel("●")
        St.status(self.device_status_label, False)
        self.timer_label = QLabel("Время: 00:00")
        self.timer_label.setStyleSheet("color: #ffcc33; font-weight: bold; margin: 0 10px;")
        finish = QLabel("Done")
        self.finish_status = QLabel("●")
        St.finish(self.finish_status, False)
        status_layout.addWidget(conn)
        status_layout.addWidget(self.device_status_label)
        self.dhcp_status_label = QLabel("●")
        St.status_level(self.dhcp_status_label, "red")
        self.dhcp_status_label.setToolTip("DHCP-сервер")
        status_layout.addWidget(self.dhcp_status_label)
        status_layout.addStretch()
        status_layout.addWidget(self.timer_label)
        status_layout.addStretch()
        status_layout.addWidget(finish)
        status_layout.addWidget(self.finish_status)
        main_layout.addLayout(status_layout)
        self.setLayout(main_layout)
        self.update_ui()
        self.setFocus(Qt.OtherFocusReason) # Перехватить клавиатуру сразу после построения UI.

    def _admin_helper_path(self):
        return os.path.join(self.base_path, "scripts", "admin_auth_helper.sh")

    def _set_admin_state(self, enabled: bool):
        self.is_admin = bool(enabled)
        self.act_admin_login.setEnabled(not self.is_admin)
        self.act_admin_logout.setEnabled(self.is_admin)
        self.act_manual_input.setEnabled(self.is_admin)
        self.act_require_board.setEnabled(self.is_admin)
        self.act_send_board.setEnabled(self.is_admin)
        if self.is_admin:
            self.admin_session_timer.start(max(1, self.admin_session_seconds) * 1000)
            self.log_signal("[ADMIN] Админ-сессия активирована")
        else:
            self.admin_session_timer.stop()
            self.options["require_board_number"] = True
            self.options["send_board_number"] = True
            self.act_require_board.setChecked(True)
            self.act_send_board.setChecked(True)
            self.save_options()
            self.log_signal("[ADMIN] Админ-сессия завершена (параметры B/N сброшены в штатное состояние)")

    def _on_toggle_require_board(self, checked: bool):
        if not self.is_admin:
            self.act_require_board.setChecked(self.options.get("require_board_number", True))
            return
        self.options["require_board_number"] = bool(checked)
        self.save_options()
        if checked:
            self.log_signal("[ADMIN] Требование B/N включено")
        else:
            self.log_signal("[ADMIN] Требование B/N отключено (DB/GS могут быть пропущены)")

    def _on_toggle_send_board(self, checked: bool):
        if not self.is_admin:
            self.act_send_board.setChecked(self.options.get("send_board_number", True))
            return
        self.options["send_board_number"] = bool(checked)
        self.save_options()
        if checked:
            self.log_signal("[ADMIN] Отправка B/N включена")
        else:
            self.log_signal("[ADMIN] Отправка B/N отключена (в таблицу пойдёт дата/время, в БД — пусто)")

    def _expire_admin_session(self):
        if self.is_admin:
            self._set_admin_state(False)
            QMessageBox.information(self, "Сессия администратора", "Время админ-сессии истекло.")

    def _start_flash_watchdog(self): # Максимальное время ожидания прошивки, чтобы UI не "вис" навсегда
        timeout_ms = int(self.options.get("flash_watchdog_ms", 180000))
        self.flash_timed_out = False
        self.flash_watchdog.start(timeout_ms)

    def _stop_flash_watchdog(self):
        if self.flash_watchdog.isActive():
            self.flash_watchdog.stop()

    def _on_flash_timeout(self):
        self.busy = False
        self.flash_timed_out = True
        self.last_result_ok = False
        self.ready_for_bn = False
        self.pending_bn = None
        self.pending_sheets = None
        self._await_dhcp_confirm = False
        self._dhcp_confirm_timer.stop()
        self.reset_operation_timer()
        self.log_signal("[ERR] Таймаут прошивки — разблокировали ввод. Можно повторить.")
        self._set_workflow("Этап: Таймаут прошивки")
        try:
            self.btn_stop.setEnabled(False)
        except Exception:
            pass

    def _start_dhcp_confirm_wait(self):
        """Ждём подтверждения нового MAC по DHCP перед записью в БД/таблицу."""
        self._await_dhcp_confirm = True
        self._last_mac_mismatch = None
        self._set_workflow("Этап: Ожидание подтверждения MAC по DHCP")
        self.log_signal("[WAIT] Ждём подтверждения нового MAC по DHCP...")
        self._dhcp_confirm_timer.start(120000)

    def _start_dhcp_start_wait(self):
        """Ждём, пока DHCP-сервер выдаст аренду — без этого прошивать нельзя."""
        self._await_dhcp_start = True
        self._set_workflow("Этап: Ожидание адреса от DHCP-сервера")
        self.log_signal("[WAIT] Устройство не получило адрес по DHCP — ждём...")
        self._dhcp_start_timer.start(120000)

    def _on_dhcp_start_ready(self):
        if self._closing:
            return
        if not self._await_dhcp_start:
            return
        self._await_dhcp_start = False
        self._dhcp_start_timer.stop()
        self.log_signal("[OK] DHCP-сервер выдал адрес — начинаем прошивку")
        self.eeprom_flash_async()

    def _on_dhcp_start_timeout(self):
        if not self._await_dhcp_start:
            return
        self._await_dhcp_start = False
        self._dhcp_start_timer.stop()
        self.stop_operation_timer()
        self._set_field_color("sn", "#ff6666")
        self._set_field_color("bn", "#ff6666")
        self._set_workflow("Этап: Устройство не появилось в DHCP")
        self.log_signal("[ERR] Устройство не получило адрес по DHCP — прошивка отменена")
        self.pending_bn = None
        self.pending_sheets = None
        if self.options.get("auto_reset", True):
            safe_single_shot(2000, lambda: self.reset_fields(reset_timer=False))

    def _on_dhcp_confirm_timeout(self):
        if not self._await_dhcp_confirm:
            return
        self._await_dhcp_confirm = False
        self._expected_flash_mac = None
        self._pre_flash_mac = None
        self.stop_operation_timer()
        self._set_field_color("sn", "#ff6666")
        self._set_field_color("bn", "#ff6666")
        self._set_workflow("Этап: MAC не подтверждён по DHCP")
        self.log_signal("[ERR] Новый MAC не подтверждён по DHCP — запись в БД/таблицу отменена")
        self.pending_bn = None
        self.pending_sheets = None
        if self.options.get("auto_reset", True):
            safe_single_shot(2000, lambda: self.reset_fields(reset_timer=False))

    def _check_dhcp_devices(self):
        """Периодическая проверка: устройства без операций."""
        if self._closing:
            return
        now = time.time()
        lease = int(self.options.get("dhcp_lease_time", 60) or 60)
        stale_limit = lease + 15
        if len(self._dhcp_devices) > 1000:
            cutoff = now - 3600
            self._dhcp_devices = {m: i for m, i in self._dhcp_devices.items() if i["last_seen"] > cutoff}
        if len(self._claimed_macs) > 5000:
            self._claimed_macs.clear()
        for mac, info in list(self._dhcp_devices.items()):
            if info.get("notified") or mac in self._claimed_macs:
                continue
            age = now - info["first_seen"]
            idle = now - info["last_seen"]
            if idle > stale_limit:
                self._notify_skipped_device(mac, "disconnected")
            elif age > 60:
                self._notify_skipped_device(mac, "idle")
        self._update_skip_status()

    def _notify_skipped_device(self, mac, reason):
        """Пометить устройство пропущенным и уведомить GUI-поток.

        Может вызываться из sniff-потока: виджеты не трогаем, только
        обновляем реестр и эмитим сигнал (Qt доставит его в GUI-поток).
        """
        info = self._dhcp_devices.get(mac)
        if info is None or info.get("notified"):
            return
        info["notified"] = True
        self.device_skipped.emit(mac, info.get("ip", "?"), reason)

    def _on_device_skipped(self, mac, ip, reason):
        """Слот GUI-потока: показать уведомление о пропущенном устройстве."""
        if self._closing:
            return
        if reason == "disconnected":
            text = f"Устройство {mac} (IP {ip}) отключилось, но операций с ним не было."
            log_reason = "отключилось без операций"
            Toast(self, text, duration=6000)
        else:
            text = f"Устройство {mac} (IP {ip}) подключено более минуты без операций."
            log_reason = "60с без операций"
            # Не исчезает само — закроется при вводе S/N или B/N
            toast = Toast(self, text, duration=0)
            self._idle_toasts.append(toast)
        self.log_signal(f"[WARN] Пропущено устройство: {mac} (IP {ip}) — {log_reason}")

    def _dismiss_idle_toasts(self):
        """Закрыть висящие уведомления о простое (при вводе S/N или B/N)."""
        for toast in list(self._idle_toasts):
            try:
                toast.close_toast()
            except Exception:
                pass
        self._idle_toasts.clear()
        self._update_skip_status()

    def _mark_claimed(self, mac):
        norm = _norm_mac(mac)
        if not norm:
            return
        self._claimed_macs.add(norm)
        # Устройство взято в работу — снимаем отметку «пропущено»
        info = self._dhcp_devices.get(norm)
        if info is not None and info.get("notified"):
            info["notified"] = False
            self._update_skip_status()

    def _update_skip_status(self):
        """Индикатор «Пропуск»: зелёный — нет, жёлтый — есть пропущенные."""
        skipped = any(info.get("notified") for info in self._dhcp_devices.values())
        if skipped:
            St.status_level(self.skip_status_label, "yellow")
            self.skip_status_label.setToolTip("Есть пропущенные устройства")
        else:
            St.status_level(self.skip_status_label, "green")
            self.skip_status_label.setToolTip("Нет пропущенных устройств")

    def _on_flash_mac_confirmed(self, mac):
        """MAC подтверждён по DHCP — можно писать в БД и таблицу."""
        if self._closing:
            return
        if not self._await_dhcp_confirm:
            return
        self._await_dhcp_confirm = False
        self._dhcp_confirm_timer.stop()
        self._expected_flash_mac = None
        self._pre_flash_mac = None
        self.log_signal(f"[OK] MAC подтверждён по DHCP: {mac} — записываем в БД и таблицу")
        self._set_field_color("sn", "#66cc66")
        self._set_field_color("bn", "#66cc66")
        self.update_finish_status(True)
        # B/N мог быть отсканирован во время ожидания подтверждения
        if self.pending_bn:
            pb = self.pending_bn
            self.serial_number = pb.get("sn", self.serial_number)
            self.board_number = pb.get("bn", self.board_number)
            self.lanMac = pb.get("lan", self.lanMac)
            self.wanMac = pb.get("wan", self.wanMac)
            self.pending_bn = None
            self.log_signal(f"[INFO] Применён отложенный B/N: {self.serial_number} | {self.board_number}")
        self._set_workflow("Этап: MAC подтверждён, запись в БД")
        board = (self.board_number or "").strip()
        if board:
            self.ready_for_bn = False
            if self.options.get("run_sheets", False):
                self.pending_sheets = (self.serial_number, board)
            started = self.on_postcheck_clicked()
            if not started:
                self.pending_sheets = None
                self.log_signal("[WARN] Postcheck не запущен — проверьте S/N и B/N")
                if self.options.get("auto_reset", True):
                    safe_single_shot(2000, lambda: self.reset_fields(reset_timer=False))
        else:
            # B/N ещё не отсканирован — сброс не делаем, ждём скан
            self.ready_for_bn = True

    def stop_current_operation(self):
        if not self.busy:
            self.log_signal("[INFO] Нет активного процесса для остановки")
            return
        try:
            if self.current_stop_event is not None:
                self.current_stop_event.set()
            self.log_signal("[STOP] Запрошена остановка процесса...")
            self._set_workflow("Этап: Остановка процесса")
        finally:
            self.busy = False
            self._stop_flash_watchdog()
            try:
                self.btn_stop.setEnabled(False)
            except Exception:
                pass

    def admin_login_via_pkexec(self):
        helper = self._admin_helper_path()
        if not os.path.isfile(helper):
            QMessageBox.warning(self, "Ошибка", f"Не найден helper:\n{helper}")
            return
        if not os.access(helper, os.X_OK):
            QMessageBox.warning(self, "Ошибка", f"Helper не исполняемый:\n{helper}\nВыполните: chmod +x")
            return
        try:
            result = subprocess.run(["pkexec", helper, "--authorize"], capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            QMessageBox.critical(self, "Ошибка", "Команда pkexec не найдена в системе.")
            return
        except subprocess.TimeoutExpired:
            QMessageBox.warning(self, "Таймаут", "Проверка прав администратора не завершилась вовремя.")
            return
        if result.returncode == 0:
            current_minutes = max(1, self.admin_session_seconds // 60)
            minutes, ok = QInputDialog.getInt(
                self,
                "Время админ-сессии",
                "Укажите время сессии администратора (в минутах):",
                value=current_minutes,
                minValue=1,
                maxValue=480,
                step=1
            )
            if not ok:
                self.log_signal("[ADMIN] Вход в режим администратора отменён пользователем.")
                return
            self.admin_session_seconds = minutes * 60
            self.options["admin_session_seconds"] = self.admin_session_seconds
            self.save_options()
            self._set_admin_state(True)
            QMessageBox.information(self, "Успешно", f"Доступ администратора подтверждён (сессия на {minutes} мин).")
            return
        err = (result.stderr or "").strip() or "Авторизация отменена или отклонена."
        self.log_signal(f"[ADMIN][ERR] {err}")
        QMessageBox.warning(self, "Доступ отклонён", err)

    def admin_logout(self):
        self._set_admin_state(False)

    def manual_input_dialog(self):
        if not self.is_admin:
            QMessageBox.warning(self, "Доступ запрещён", "Ручной ввод доступен только администратору.")
            return
        serial, ok = QInputDialog.getText(self, "Ручной ввод", "Введите S/N:")
        if not ok:
            return
        serial = serial.strip()
        if not serial:
            QMessageBox.warning(self, "Ошибка", "S/N не может быть пустым.")
            return
        board, ok = QInputDialog.getText(self, "Ручной ввод", "Введите B/N:")
        if not ok:
            return
        board = board.strip()
        if not board:
            QMessageBox.warning(self, "Ошибка", "B/N не может быть пустым.")
            return
        self.serial_number = serial
        self.board_number = board
        self._set_field_text("sn", serial)
        self._set_field_text("bn", board)
        self.update_sn_info(serial, False)
        self.log_signal(f"[ADMIN] Ручной ввод применён: {serial} | {board}")
        if self.options.get("auto_check", True):
            self.update_finish_status(False)
            self._run_check_serial(serial)

    def _show_manual_field_menu(self, target: str, global_pos):
        menu = QMenu(self)
        act_input = QAction("Ввести", self)
        act_input.triggered.connect(lambda: self._manual_input_to_field(target))
        menu.addAction(act_input)
        # Тестовый ввод MAC-адреса для запуска прошивки (только для S/N поля)
        if target == "sn":
            act_test_mac = QAction("Тест: ввести MAC-адрес для прошивки", self)
            act_test_mac.triggered.connect(lambda: self._test_input_mac_for_flash())
            menu.addAction(act_test_mac)
        # Тестовая отправка в Google Sheets — только для админа
        if self.is_admin and self.options.get("run_sheets", False):
            act_test = QAction("Тестовая отправка в Google Sheets", self)
            act_test.triggered.connect(lambda: self._test_send_to_sheets())
            menu.addAction(act_test)
            # Удалить тестовую запись из таблицы
            act_delete = QAction("Удалить тестовую запись из таблицы", self)
            act_delete.triggered.connect(lambda: self._test_delete_from_sheets())
            menu.addAction(act_delete)
        menu.exec(global_pos)

    def _test_input_mac_for_flash(self):
        """Ввести MAC-адрес вручную и запустить прошивку (режим тестирования)."""
        if self._closing:
            return
        text, ok = QInputDialog.getText(self, "Тест: ввод MAC-адреса", "Введите MAC-адрес (например: 00:11:22:33:44:55):")
        if not ok:
            return
        # Принимаем любой формат: 32b768cd0458, 32:b7:68:cd:04:58, 32-B7-68-CD-04-58
        norm = _norm_mac(text)
        if len(norm) != 12:
            QMessageBox.warning(self, "Неверный MAC", "MAC-адрес должен содержать 12 hex-символов (например: 32:b7:68:cd:04:58 или 32b768cd0458)")
            return
        mac = ":".join(norm[i:i+2] for i in range(0, 12, 2)).upper()
        # Устанавливаем MAC как WAN (и LAN для одноинтерфейсных)
        self.wanMac = mac
        self.lanMac = mac
        # Если нет серийника — ставим тестовый
        if not self.serial_number:
            self.serial_number = "TEST-SN-MAC"
            self._set_field_text("sn", self.serial_number)
            self.update_sn_info(self.serial_number, False)
        self.log_signal(f"[TEST] Введён MAC-адрес: {mac} (WAN={self.wanMac}, LAN={self.lanMac})")
        self.log_signal("[TEST] Режим тестирования: проверки БД и отправка в таблицу отключены")
        self._test_mode = True
        self.log_signal("[TEST] Запуск механизма прошивки...")
        self._set_workflow("Этап: Тестовая прошивка (MAC введён вручную)")
        # Запускаем прошивку (генерация EEPROM внутри eeprom_flash)
        self.eeprom_flash_async()
        # _test_mode сбросится в _on_finished после завершения прошивки

    def _test_delete_from_sheets(self):
        """Удаляет тестовую запись из Google Sheets по текущим значениям полей."""
        if not self.is_admin:
            QMessageBox.warning(self, "Доступ запрещён", "Удаление доступно только администратору.")
            return
        if not self.options.get("run_sheets", False):
            self.log_signal("[INFO] Google Sheets отключён настройками")
            return
        serial = (self.sn_field.text() or "").strip()
        board = (self.bn_field.text() or "").strip()
        # Если поля пустые — генерируем тестовые значения (как при отправке)
        if not serial and not board:
            serial = "TEST-SN-001"
            board = "TEST-BN-001"
        elif not serial:
            serial = "TEST-SN-001"
        elif not board:
            board = "TEST-BN-001"
        # Учитываем настройки send_board_number
        if not self.options.get("send_board_number", True):
            board = datetime.now().strftime("%d:%m:%y_%H:%M:%S")
        # Проверяем, выбран ли лист
        if not self._ensure_sheet_selected():
            return
        # Удаляем через gs_worker
        if hasattr(self, "gs_worker") and self.gs_worker and self.gs_thread and self.gs_thread.isRunning():
            self.log_signal(f"[TEST] Удаляю тестовую запись: S/N={serial} | B/N={board}")
            self.request_delete_record.emit(serial, board)
        else:
            self.log_signal("[WARN] Google Sheets не инициализирован — удаление пропущено")

    def _test_send_to_sheets(self):
        """Тестовая отправка текущих значений полей в Google Sheets."""
        if not self.is_admin:
            QMessageBox.warning(self, "Доступ запрещён", "Тестовая отправка доступна только администратору.")
            return
        if not self.options.get("run_sheets", False):
            self.log_signal("[INFO] Google Sheets отключён настройками")
            return
        # Берём текущие значения из полей
        serial = (self.sn_field.text() or "").strip()
        board = (self.bn_field.text() or "").strip()
        # Если поля пустые — генерируем тестовые значения
        if not serial and not board:
            serial = "TEST-SN-001"
            board = "TEST-BN-001"
        elif not serial:
            serial = "TEST-SN-001"
        elif not board:
            board = "TEST-BN-001"
        # Учитываем настройки send_board_number
        if not self.options.get("send_board_number", True):
            board = datetime.now().strftime("%d:%m:%y_%H:%M:%S")
        # Проверяем, выбран ли лист
        if not self._ensure_sheet_selected():
            return
        # Отправляем через gs_worker
        if hasattr(self, "gs_worker") and self.gs_worker and self.gs_thread and self.gs_thread.isRunning():
            self.log_signal(f"[TEST] Отправляю тестовую запись: S/N={serial} | B/N={board}")
            self.request_add_record.emit(serial, board)
        else:
            self.log_signal("[WARN] Google Sheets не инициализирован — тестовая отправка пропущена")

    def _manual_input_to_field(self, target: str):
        title = "Ввод Serial" if target == "sn" else "Ввод Board"
        prompt = "Введите Serial (S/N):" if target == "sn" else "Введите Board (B/N):"
        text, ok = QInputDialog.getText(self, title, prompt)
        if not ok:
            return
        text = (text or "").strip()
        if not text:
            return
        if target == "sn":
            fixed = self._dedupe_scan(text)
            self.process_scanned(text, fixed)
        else:
            fixed = self._dedupe_scan(text)
            self.process_scanned(text, fixed)

    def on_gs_label_click(self):
        if getattr(self, "gs_worker", None):
            self.request_open_sheet.emit()
        else:
            self.log_signal("[INFO] Google Sheets не инициализирован — открыть нечего")

    def update_author(self, text):
        self.author = text.strip()
        if getattr(self, "gs_manager", None):
            try:
                self.gs_manager.author = self.author
            except Exception as e:
                self.log_signal(f"[WARN] Не удалось обновить gs_manager.author: {e}")

    def _update_author_option(self, text):
        self.options["author"] = text.strip()
        self.save_options()

    def update_device_status(self, online: bool):
        St.status(self.device_status_label, online)

    def update_finish_status(self, check: bool):
        St.finish(self.finish_status, check)
        St.sn_info_collor(self.sn_info, check)

    def update_sheet_status(self, online: bool):
        if self._closing:
            return
        St.status(self.gs, online)

    def set_current_sheet_label(self, title: str):
        if self._closing:
            return
        title = (title or "").strip()
        if title:
            self.current_sheet_label.setText(f"Лист: {title}")
            self.menu_sheets.set_current_sheet(title)
        else:
            self.current_sheet_label.setText("Лист: не выбран")
            self.menu_sheets.set_current_sheet("")

    def update_sn_info(self, text: str, send: bool):
        self.sn_info.setText(text)
        St.sn_info_collor(self.sn_info, send)

    def _set_workflow(self, text: str):
        self.workflow_label.setText(text)

    def update_ui(self):
        """Обновляет UI и режим работы."""
        mode = self.options.get("flash_mode", "eeprom_spi")
        try:
            if getattr(self, "device_watcher", None):
                self.device_watcher.set_mode(mode)
        except Exception:
            pass
        if mode == "eeprom_spi":
            self.device_status_label.setToolTip("Connected: устройство доступно по сети")
            self.log_debug("MODE: EEPROM SPI")
            self._set_workflow("Этап: Ожидание сканирования S/N")
        else:
            self.device_status_label.setToolTip("Connected: устройство доступно по сети")
            self.log_debug(f"MODE: {mode}")
            self._set_workflow(f"Этап: Режим {mode}, ожидаем S/N")

    # ------------------------------------------------------------------ #
    # DHCP-сервер
    # ------------------------------------------------------------------ #
    def _start_dhcp_server(self):
        """Запустить DHCP-сервер в отдельном daemon-потоке (threading.Thread, не QThread).
        При любой ошибке — просто логирует и продолжает работу без сервера."""
        try:
            # Уже запущен — не запускаем повторно
            if self.dhcp_worker is not None and self.dhcp_worker.running:
                return

            # Сначала останавливаем старый сервер, если есть
            if self.dhcp_worker is not None:
                self._stop_dhcp_server()

            iface = (self.options.get("dhcp_interface") or "").strip()
            if not iface:
                if self.options.get("debug", False):
                    self.log_signal("[DHCP] Интерфейс не выбран — сервер не запущен (работа без DHCP)")
                self._update_dhcp_status(False)
                return

            server_ip = self.options.get("dhcp_server_ip", "15.15.15.1")
            assigned_ip = self.options.get("dhcp_assigned_ip", "15.15.15.15")
            mask = self.options.get("dhcp_subnet_mask", "255.255.255.0")
            lease = self.options.get("dhcp_lease_time", 60)

            self.dhcp_worker = DhcpServer(
                interface=iface,
                server_ip=server_ip,
                assigned_ip=assigned_ip,
                subnet_mask=mask,
                lease_time=lease,
                log_callback=self._on_dhcp_log,
                lease_callback=self._on_dhcp_lease,
                debug=self.options.get("debug", False),
            )
            self.dhcp_worker.start()
            self._update_dhcp_status(True)
            self.log_signal(f"[DHCP] Сервер запущен на {iface} -> {assigned_ip}")
        except Exception as e:
            self._update_dhcp_status(False)
            self.log_signal(f"[DHCP] Ошибка запуска сервера: {e} (работа без DHCP)")
            self.dhcp_worker = None

    def _stop_dhcp_server(self):
        """Остановить DHCP-сервер. Просто вызываем stop() — daemon-поток завершится сам."""
        if self.dhcp_worker is None:
            return
        try:
            self.dhcp_worker.stop()
        except Exception:
            pass
        self.dhcp_worker = None
        self._update_dhcp_status(False)
        self.log_signal("[DHCP] Сервер остановлен")

    def _on_dhcp_log(self, text, tag):
        # Вызывается из sniff-потока (не GUI) — сигнал Qt с QueuedConnection доставит в GUI поток
        if self._closing:
            return
        if tag == "error" or tag == "info" or self.options.get("debug", False):
            self.log_signal(f"[DHCP] {text}")

    def _on_dhcp_lease(self, mac, ip, event):
        """Событие аренды DHCP: запомнить последний хост и MAC для прошивки.

        Вызывается из sniff-потока (не GUI) — не трогаем виджеты,
        только обновляем внутренние переменные и пишем в лог через сигнал.
        """
        if self._closing:
            return
        self.dhcp_host = ip
        self.dhcp_mac = mac
        if self.options.get("debug", False):
            self.log_signal(f"[DHCP] Аренда {event}: MAC={mac} IP={ip}")
        if event == "ack":
            self._last_dhcp_ack_time = time.time()
            norm = _norm_mac(mac)
            now = time.time()
            if norm:
                info = self._dhcp_devices.get(norm)
                if info is None:
                    self._dhcp_devices[norm] = {"first_seen": now, "last_seen": now, "ip": ip, "notified": False}
                else:
                    info["last_seen"] = now
                    info["ip"] = ip
            # Смена MAC на том же IP = предыдущее устройство отключилось
            last = getattr(self, "_last_dhcp_ack", None)
            if last is not None:
                prev_mac, prev_ip = last
                prev_norm = _norm_mac(prev_mac)
                if prev_ip == ip and prev_norm and prev_norm != norm:
                    if prev_norm not in self._claimed_macs:
                        self._notify_skipped_device(prev_norm, "disconnected")
            if last != (mac, ip):
                self._last_dhcp_ack = (mac, ip)
                self.log_signal(f"[DHCP] Целевой хост для прошивки установлен: {ip} (MAC: {mac})")
            # Прошивка ждёт свежую аренду — сигналим в GUI-поток
            if getattr(self, "_await_dhcp_start", False):
                self.dhcp_start_ready.emit()

        # Сравниваем MAC после перезагрузки с тем, который прошивали
        expected = getattr(self, "_expected_flash_mac", None)
        if expected and event == "ack":
            if _norm_mac(mac) == _norm_mac(expected):
                self.log_signal(f"[OK] MAC после перезагрузки совпадает: {mac}")
                # Очищаем ожидаемый MAC, чтобы не логировать при каждом renew
                self._expected_flash_mac = None
                self._pre_flash_mac = None
                if getattr(self, "_await_dhcp_confirm", False):
                    # Сигнал потокобезопасен: слот выполнится в GUI-потоке
                    self.flash_mac_confirmed.emit(mac)
            else:
                norm_mac = _norm_mac(mac)
                pre = getattr(self, "_pre_flash_mac", None)
                if pre and norm_mac == pre:
                    # Это старый MAC устройства до прошивки (stale renew) — не предупреждаем
                    if self.options.get("debug", False):
                        self.log_signal(f"[DHCP] ACK от до-прошивочного MAC {mac} — пропускаем (stale)")
                else:
                    last_mismatch = getattr(self, "_last_mac_mismatch", None)
                    if last_mismatch != norm_mac:
                        self._last_mac_mismatch = norm_mac
                        self.log_signal(f"[WARN] MAC после перезагрузки не совпадает: ожидался {expected}, получен {mac}")

    def _update_dhcp_status(self, active: bool):
        if self._closing:
            return
        color = "#66cc66" if active else "#cc3333"
        self.dhcp_status_label.setStyleSheet(f"""color: {color}; font-weight: bold; font-size: 14px;""")

    def _default_instructions(self):
        return instruction()

    def _load_instructions(self):
        defaults = self._default_instructions()
        os.makedirs(os.path.dirname(self.instructions_path), exist_ok=True)
        if not os.path.exists(self.instructions_path):
            with open(self.instructions_path, "w", encoding="utf-8") as f:
                json.dump(defaults, f, indent=4, ensure_ascii=False)
            return defaults
        try:
            with open(self.instructions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                defaults.update(data)
            return defaults
        except Exception as e:
            self.log_signal(f"[WARN] Не удалось прочитать инструкции: {e}")
            return defaults

    def show_mode_instruction(self):
        mode = self.options.get("flash_mode", "eeprom_spi")
        mapping = self._load_instructions()
        item = mapping.get(mode) or mapping.get("eeprom_spi") or {}
        title = item.get("title", f"Инструкция: {mode}")
        text = item.get("text", "Инструкция для текущего режима не найдена.")
        QMessageBox.information(self, title, text)

    def update_gs(self):
        run_sheets = self.options.get("run_sheets", False)
        if run_sheets:
            self.log_signal("[INFO] Инициализация Google Sheets...")
            self._teardown_google_sheets()
            self._init_google_sheets()
        else:
            self._teardown_google_sheets()

    def _teardown_google_sheets(self):
        """Останавливает и очищает Google Sheets worker/thread/manager."""
        self._stop_gs_timers()
        if not getattr(self, "gs_manager", None) and not getattr(self, "gs_worker", None) and not getattr(self, "gs_thread", None):
            return
        if getattr(self, "gs_worker", None):
            self.log_signal("[INFO] Google Sheets отключён — закрываю соединение")
            # disconnect requests -> worker
            self._try_shutdown(self.request_open_sheet.disconnect, self.gs_worker.open_sheet_in_browser)
            self._try_shutdown(self.request_select_sheet.disconnect, self.gs_worker.select_sheet)
            self._try_shutdown(self.request_fetch_sheets.disconnect, self.gs_worker.fetch_sheets)
            self._try_shutdown(self.request_check_gs_auth.disconnect, self.gs_worker.check_auth)
            self._try_shutdown(self.request_add_record.disconnect, self.gs_worker.add_record)
            self._try_shutdown(self.request_delete_record.disconnect, self.gs_worker.delete_record)
            self._try_shutdown(self.request_stop_worker.disconnect, self.gs_worker.stop)
            # disconnect worker -> gui
            self._try_shutdown(self.gs_worker.log.disconnect, self._signals.log.emit)
            self._try_shutdown(self.gs_worker.result.disconnect, self.handle_google_result)
            self._try_shutdown(self.gs_worker.sheet_list_ready.disconnect, self._populate_sheet_menu)
            self._try_shutdown(self.gs_worker.current_sheet_changed.disconnect, self.set_current_sheet_label)
            self._try_shutdown(self.gs_worker.sheet_status.disconnect, self.update_sheet_status)
            # disconnect menu -> requests
            self._try_shutdown(self.menu_sheets.sheet_selected.disconnect, self.request_select_sheet.emit)
            self._try_shutdown(self.menu_sheets.aboutToShow.disconnect, self.request_fetch_sheets.emit)
            self._try_shutdown(self.request_stop_worker.emit)
        self.update_sheet_status(False)
        self.set_current_sheet_label("")
        if getattr(self, "gs_thread", None) and self.gs_thread.isRunning():
            self.gs_thread.quit()
            # Ждём реального завершения, не более 10 секунд суммарно
            t0 = time.time()
            while self.gs_thread.isRunning() and (time.time() - t0) < 10.0:
                self.gs_thread.wait(200)
            if self.gs_thread.isRunning():
                self.log_signal("[WARN] Поток GoogleWorker не завершился за 10с")
            self.log_debug("Поток GoogleWorker остановлен")
        if getattr(self, "gs_manager", None):
            self._try_shutdown(self.gs_manager.close)
        _w, _t = getattr(self, "gs_worker", None), getattr(self, "gs_thread", None)
        self.gs_manager = None
        self.gs_worker = None
        self.gs_thread = None
        # Не вызываем deleteLater() на worker/thread: поток уже остановлен,
        # его event loop не обработает событие удаления — это приводит к double free.
        # Python GC сам соберёт объекты после обнуления ссылок.
        self.menu_sheets.populate(None)

    def _start_gs_sheet_refresh_timer(self):
        if not self.options.get("run_sheets", False):
            return
        if not getattr(self, "gs_worker", None):
            return
        try:
            interval_ms = max(5, int(self.options.get("gs_sheet_refresh_seconds", 30))) * 1000
        except (TypeError, ValueError):
            interval_ms = 30000
        if self.gs_sheet_refresh_timer is None:
            self.gs_sheet_refresh_timer = QTimer(self)
            self.gs_sheet_refresh_timer.timeout.connect(self.request_fetch_sheets.emit)
        self.gs_sheet_refresh_timer.start(interval_ms)
        self.log_debug(f"Автообновление списка листов: {interval_ms // 1000} сек")

    def _start_gs_auth_timer(self):
        if not self.options.get("run_sheets", False):
            return
        if not getattr(self, "gs_worker", None):
            return
        if self.gs_auth_timer is None:
            self.gs_auth_timer = QTimer(self)
            self.gs_auth_timer.timeout.connect(self.request_check_gs_auth.emit)
        self.gs_auth_timer.start(15000)

    def _stop_gs_sheet_refresh_timer(self):
        if self.gs_sheet_refresh_timer is None:
            return
        try:
            self.gs_sheet_refresh_timer.stop()
            self.gs_sheet_refresh_timer.timeout.disconnect(self.request_fetch_sheets.emit)
        except Exception:
            pass
        try:
            self.gs_sheet_refresh_timer.deleteLater()
        except Exception:
            pass
        self.gs_sheet_refresh_timer = None

    def _stop_gs_auth_timer(self):
        if self.gs_auth_timer is None:
            return
        try:
            self.gs_auth_timer.stop()
            self.gs_auth_timer.timeout.disconnect(self.request_check_gs_auth.emit)
        except Exception:
            pass
        try:
            self.gs_auth_timer.deleteLater()
        except Exception:
            pass
        self.gs_auth_timer = None

    def _stop_gs_timers(self):
        self._stop_gs_sheet_refresh_timer()
        self._stop_gs_auth_timer()

    def keyPressEvent(self, event):
        if self._closing:
            return
        ch = event.text()
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            raw = getattr(self, "buffer", "").strip()
            self.log_debug(f"keyPressEvent: Enter, raw='{raw}'")
            if raw:
                fixed = fix_layout(raw)
                self.log_debug(f"keyPressEvent: fixed='{fixed}'")
                self.process_scanned(raw, fixed)
            self.buffer = ""
            return
        if not ch: return
        self.buffer = getattr(self, "buffer", "") + ch

    def process_scanned(self, raw_text: str, fixed_text: str):
        if self._closing:
            return
        fixed_text = self._dedupe_scan(fixed_text)
        self.log_debug(f"process_scanned: raw='{raw_text}', fixed='{fixed_text}', busy={self.busy}")
        if self.options.get("serial_prefix", "CUINF") in fixed_text:
            if self.pending_bn and (self.pending_bn.get("sn") != fixed_text):
                self.pending_bn = None
                self.log_debug("Отложенный B/N сброшен: новый S/N")
            self.serial_number = fixed_text
            self._set_field_text("sn", fixed_text)
            self._mark_claimed(self.dhcp_mac)
            self._dismiss_idle_toasts()
            self.update_sn_info(fixed_text, False)
            self._set_workflow("Этап: S/N получен, запускаем проверку")
            self.start_operation_timer()
            if self.options.get("auto_check", True):
                self.update_finish_status(False)
                self._run_check_serial(fixed_text)
            return
        elif fixed_text.startswith("Q_ITL") or fixed_text.startswith("QRINF"):
            self.stop_blink_bn()
            self.board_number = fixed_text
            self._set_field_text("bn", fixed_text)
            self._mark_claimed(self.dhcp_mac)
            self._dismiss_idle_toasts()
            self._set_workflow("Этап: B/N получен")
            if self.busy:
                self.pending_bn = {
                    "sn": self.serial_number, "bn": self.board_number,
                    "lan": (getattr(self, "lanMac", "") or "").strip(),
                    "wan": (getattr(self, "wanMac", "") or "").strip(),
                }
                self.log_debug(f"Отложено: B/N будет отправлен после прошивки: {self.serial_number} | {self.board_number}")
                self._set_workflow("Этап: B/N принят, ждём завершения прошивки")
                return
            if not getattr(self, "wanMac", None):
                if getattr(self, "serial_number", ""):
                    self.pending_bn = {"sn": self.serial_number, "bn": self.board_number}
                    self.log_signal("[INFO] B/N принят заранее — применим после завершения прошивки")
                    self._set_workflow("Этап: B/N принят, ожидаем завершения прошивки")
                else:
                    self.log_debug("Отложено: Пропуск update_postcheck — S/N ещё не получен")
                return
            if getattr(self, "_await_dhcp_confirm", False):
                # Ждём подтверждения MAC по DHCP — B/N отложим до подтверждения
                self.pending_bn = {
                    "sn": self.serial_number, "bn": self.board_number,
                    "lan": (getattr(self, "lanMac", "") or "").strip(),
                    "wan": (getattr(self, "wanMac", "") or "").strip(),
                }
                self.log_signal("[INFO] B/N принят — ждём подтверждения MAC по DHCP")
                self._set_workflow("Этап: B/N принят, ждём подтверждения MAC")
                return
            if getattr(self, "ready_for_bn", False):
                try:
                    started = self.on_postcheck_clicked()
                except Exception as e:
                    self.log_debug(f"Отложено: Ошибка при update_postcheck: {e}")
                    started = False
                if started and self.options.get("run_sheets", False):
                    self.pending_sheets = (self.serial_number, self.board_number)
                self.ready_for_bn = False
            else:
                self.pending_bn = {
                    "sn": self.serial_number, "bn": self.board_number,
                    "lan": (getattr(self, "lanMac", "") or "").strip(),
                    "wan": (getattr(self, "wanMac", "") or "").strip(),
                }
                self.log_signal("[INFO] B/N принят заранее — применим после завершения прошивки")
                self._set_workflow("Этап: B/N принят, ожидаем завершения прошивки")
        else:
            self.log_signal("[!] Не удалось определить тип кода")

    def _dedupe_scan(self, text: str) -> str:
        s = (text or "").strip()
        if len(s) >= 2 and len(s) % 2 == 0:
            half = len(s) // 2
            if s[:half] == s[half:]:
                return s[:half]
        return s

    def _send_to_sheets(self, serial=None, board=None):
        if self._closing:
            return
        if self.options.get("recovery_mod", False):
            self.log_signal("[INFO] [Recovery Mode] Отправка в Google Sheets пропущена")
            return
        if not self.options.get("run_sheets", False):
            self.log_signal("[INFO] Отправка в Google Sheets отключена настройками")
            return
        serial = serial or self.serial_number
        board = board or self.board_number
        if not self.options.get("send_board_number", True):
            board = datetime.now().strftime("%d:%m:%y_%H:%M:%S")
        if self.options.get("require_board_number", True) and not (board or "").strip():
            self.log_signal("[INFO] B/N обязателен — отправка в Google Sheets пропущена")
            return
        if hasattr(self, "gs_worker") and self.gs_worker and self.gs_thread and self.gs_thread.isRunning():
            self.request_add_record.emit(serial, board)
        else:
            self.log_signal("[WARN] Google Sheets не инициализирован")

    def _ensure_sheet_selected(self) -> bool:
        if not self.options.get("run_sheets", False) or self.options.get("recovery_mod", False):
            return True
        label_text = (self.current_sheet_label.text() or "").strip().lower()
        if "не выбран" not in label_text:
            return True
        sheets = list(getattr(self.menu_sheets, "_all_sheets", []) or [])
        if not sheets:
            # Пробуем обновить список перед показом меню
            if getattr(self, "gs_worker", None):
                self.request_fetch_sheets.emit()
            # Ждём briefly, если gs_worker есть
            if getattr(self, "gs_worker", None) and getattr(self.gs_worker, "gs", None):
                sheets = self.gs_worker.gs.get_sheet_list()
                self.menu_sheets.populate(sheets)
            else:
                sheets = list(getattr(self.menu_sheets, "_all_sheets", []) or [])
            if not sheets:
                QMessageBox.warning(self, "Google Sheets", "Лист не выбран и список листов пока недоступен.")
                self.log_signal("[WARN] Лист Google Sheets не выбран")
                return False
        titles = [s.get("title", "Без названия") for s in sheets]
        current_title = ""
        label_text2 = (self.current_sheet_label.text() or "").strip()
        if label_text2.startswith("Лист: "):
            current_title = label_text2[len("Лист: "):].strip()
        dlg = SheetSelectDialog(self, titles, current_title=current_title)
        if dlg.exec() != QDialog.Accepted:
            self.log_signal("[INFO] Прошивка приостановлена: лист Google Sheets не выбран")
            return False
        title = dlg.selected_title()
        try:
            dlg.deleteLater()
        except Exception:
            pass
        if not title:
            self.log_signal("[INFO] Прошивка приостановлена: лист Google Sheets не выбран")
            return False
        # Используем select_sheet (асинхронный), но проверяем результат
        if not self.select_sheet(title):
            return False
        # select_sheet уже обновляет current_sheet_label при успехе
        self.log_signal(f"[INFO] Выбран лист Google Sheets: {title}")
        return True

    def blink_bn_field(self, interval=400):
        self._blink_state = False
        if hasattr(self, "_blink_timer") and self._blink_timer.isActive():
            self._blink_timer.stop()
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._do_blink_bn)
        self._blink_timer.start(interval)

    def _do_blink_bn(self):
        self._blink_state = not self._blink_state
        color = "#ffcc33" if self._blink_state else "#333333"
        self._set_field_color("bn", color)

    def stop_blink_bn(self):
        if hasattr(self, "_blink_timer") and self._blink_timer.isActive():
            self._blink_timer.stop()
        self._set_field_color("bn", "#333333")

    def _set_field_color(self, key: str, color_hex: str):
        fld = self.sn_field if key == "sn" else self.bn_field
        fld.setStyleSheet(f"background:{color_hex}; color:#fff; border-radius:4px; padding:6px;")

    def _set_field_text(self, key: str, text: str):
        if key == "sn":
            self.sn_field.setText(text)
        elif key == "bn":
            self.bn_field.setText(text)

    def log_signal(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        text = f"[{ts}] {text}"
        try:
            self._signals.log.emit(text)
        except Exception:
            self.log_append(text)

    def log_debug(self, text: str):
        if self.options.get("debug", False):
            self.log_signal(f"[DEBUG] {text}")

    def select_sheet(self, name):
        if not getattr(self, "gs_worker", None) or not getattr(self, "gs_thread", None) or not self.gs_thread.isRunning():
            self.log_signal("[WARN] Google Worker/GS отсутствует — пропускаю select_sheet")
            return False
        # Проверяем, что виджет ещё жив (не удалён)
        try:
            if not self.isVisible():
                return False
        except RuntimeError:
            return False
        # Выбор листа выполняется в потоке worker (не блокирует GUI)
        self.request_select_sheet.emit(name)
        # Ждём подтверждения из worker-потока (макс 10 сек)
        from PySide6.QtCore import QEventLoop
        loop = QEventLoop()
        result = {"ok": False}
        finished = [False]
        def _on_result(data):
            if finished[0]:
                return
            finished[0] = True
            if data.get("ok"):
                result["ok"] = True
            loop.quit()
        self.gs_worker.result.connect(_on_result, type=Qt.ConnectionType.QueuedConnection)
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        try:
            self.gs_worker.result.disconnect(_on_result)
        except Exception:
            pass
        return result["ok"]
        
    def handle_google_result(self, data: dict):
        if self._closing:
            return
        msg = data.get("msg", "")
        if data.get("ok"): self._signals.log.emit(f"[GS][OK] {msg}")
        else: self._signals.log.emit(f"[GS][ERR] {msg}")

    def log_append(self, text: str):
        if self._closing:
            return
        self.log.append_log(text)

    def _task_signals(self, finished_handler=None):
        s = WorkerSignals()
        s.log.connect(self._signals.log.emit)
        if not hasattr(self, "_active_signals"): # Держим ссылки на signals, чтобы они не были собраны GC до emit.
            self._active_signals = []
        self._active_signals.append(s)
        def _cleanup(payload):
            try:
                if finished_handler:
                    finished_handler(payload)
            finally:
                try:
                    self._active_signals.remove(s)
                except Exception:
                    pass
        s.finished.connect(_cleanup)
        return s

    def _run_check_serial(self, serial: str):
        if self._closing:
            return
        if check_serial is None:
            self.log_signal("[-] check_serial недоступна")
            return
        signals = self._task_signals(self._on_check_serial_result)
        run_in_thread(check_serial, args=(serial,), kwargs={"debug": self.options.get("debug", False)}, signals=signals)

    def _on_check_serial_result(self, payload):
        if self._closing:
            return
        if not payload.get("ok"):
            self.log_signal("[ERR] Проверка серийного номера не удалась")
            self.log_signal(payload.get("trace", str(payload.get("exc"))))
            self._set_field_color("sn", "#888888")
            self._set_workflow("Этап: Ошибка проверки S/N")
            self.reset_operation_timer()
            return
        res = payload["result"]
        status = res.get("status", "UNKNOWN")
        if status == "SERIALNO_IS_FREE":
            self._set_field_color("sn", "#66cc66")
            self.log_signal("[WARN] Серийный номер свободен, но MAC-адрес не найден — прошивка невозможна")
            self._set_workflow("Этап: MAC не найден, операция остановлена")
            self.reset_operation_timer()
            return
        elif status == "SERIALNO_IS_NOT_FINISHED":
            macs = res.get("macs", [])
            self.lanMac, self.wanMac = (
                (macs[0], macs[1]) if len(macs) > 1 else ("", macs[0] if macs else ""))
            self._set_field_color("sn", "#ffcc33")
            self.log_signal("[OK] Серийный номер свободен, найден MAC-адрес — продолжаем прошивку")
            self._set_workflow("Этап: MAC найден, подготовка к прошивке")
            if self.options.get("auto_board", False):
                generated_bn = peek_next_board_id(self.options.get("board_prefix"))
                self.board_number = generated_bn
                self._set_field_text("bn", generated_bn)
                self.log_signal(f"[AUTO] Сгенерирован номер платы: {generated_bn}")
            if not self._ensure_sheet_selected():
                return
            self.eeprom_flash_async()
        elif status == "SERIALNO_IS_BUSY":
            if self.options.get("recovery_mod", False):
                macs = res.get("macs", [])
                self.lanMac, self.wanMac = (
                    (macs[0], macs[1]) if len(macs) > 1 else ("", macs[0] if macs else ""))
                if self.wanMac:
                    self._set_field_color("sn", "#ffcc33")
                    self.log_signal(f"[INFO] [Recovery Mode] Запуск повторной прошивки для зарегистрированного S/N: {res.get('serial')}")
                    self._set_workflow("Этап: [Recovery Mode] MAC найден, подготовка к прошивке")
                    if self.options.get("auto_board", False):
                        generated_bn = peek_next_board_id(self.options.get("board_prefix"))
                        self.board_number = generated_bn
                        self._set_field_text("bn", generated_bn)
                        self.log_signal(f"[AUTO] Сгенерирован номер платы: {generated_bn}")
                    if not self._ensure_sheet_selected():
                        return
                    self.eeprom_flash_async()
                else:
                    self._set_field_color("sn", "#ff6666")
                    self.log_signal("[WARN] [Recovery Mode] S/N зарегистрирован, но MAC-адрес не найден")
                    self._set_workflow("Этап: MAC не найден, операция остановлена")
                    self.reset_operation_timer()
            else:
                self._set_field_color("sn", "#ff6666")
                self.log_signal("[ERR] Серийный номер уже используется другим устройством")
                self._set_workflow("Этап: Ошибка, серийный номер занят")
                self.reset_operation_timer()
        elif status == "NO_LATIN":
            self._set_field_color("sn", "#ff6666")
            self.log_signal("[ERR] Серийный номер содержит недопустимые символы (только латиница)")
            self._set_workflow("Этап: Ошибка формата S/N")
            self.reset_operation_timer()
        else:
            self._set_field_color("sn", "#999999")
            self.log_signal(f"[INFO] Неизвестный статус проверки: {status}")
            self._set_workflow("Этап: Неизвестный статус проверки")
            self.reset_operation_timer()

    def on_postcheck_clicked(self, serial=None, board=None, silent_missing_board=False, skip_db_sheets=False):
        if self._closing:
            return True
        if self.options.get("recovery_mod", False):
            self.log_signal("[INFO] [Recovery Mode] Обновление базы данных пропущено")
            self._set_field_color("sn", "#66cc66")
            self._set_field_color("bn", "#66cc66")
            self._set_workflow("Этап: Готово (Режим восстановления)")
            self.stop_operation_timer()
            self.ready_for_bn = False
            if self.options.get("auto_reset", True):
                safe_single_shot(2000, lambda: self.reset_fields(reset_timer=False))
            return True
        serial = (serial or self.serial_number or "").strip()
        board = (board or self.board_number or "").strip()
        require_board = self.options.get("require_board_number", True)
        if not serial and not board:
            self.log_signal("[ERR] Отсутствуют и S/N, и B/N")
            return False
        if not serial:
            self.log_signal("[ERR] Отсутствует серийный номер (но есть board)")
            return False
        if not board and require_board:
            if not silent_missing_board:
                self.log_signal("[ERR] Отсутствует номер платы (B/N обязателен)")
            return False
        if not board and not require_board:
            if not self.options.get("send_board_number", True):
                self.log_signal("[INFO] B/N отключён админом — в БД передаётся пусто, в таблицу — дата/время")
            else:
                self.log_signal("[INFO] B/N отключён админом — обновление БД и Google Sheets пропущено")
                return False
        if not (self.wanMac or "").strip():
            self.log_signal("[ERR] MAC-адрес ещё не сгенерирован или не найден")
            return False
        author = self.author or "operator"
        if not self.options.get("send_board_number", True):
            board = None
        else:
            board = board or None
        mac_type = "dual" if (getattr(self, "lanMac", "") or "").strip() else "single"
        
        if skip_db_sheets:
            self.log_signal(f"[TEST] Пропуск записи в БД и таблицы (тестовый режим)")
            self._set_field_color("sn", "#66cc66")
            self._set_field_color("bn", "#66cc66")
            self._set_workflow("Этап: Готово (тест)")
            self.stop_operation_timer()
            self.ready_for_bn = False
            self.pending_sheets = None
            self.update_finish_status(True)
            self.safe_remove_eeprom(self.eeprom_path)
            if self.options.get("auto_reset", True):
                safe_single_shot(2000, lambda: self.reset_fields(reset_timer=False))
            return True

        self.log_signal(f"[WARN] Обновление БД для {serial} | {author} | {board}")
        func = update_mac_dual if mac_type == "dual" else update_mac_single
        if mac_type == "dual":
            args = (serial,                       # str: serial number
                    getattr(self, "lanMac", ""),  # str: lanMac
                    self.wanMac,                  # str: wanMac
                    author,                       # str: operator name
                    board,                        # str: board number
                    True,                         # bool: postcheck
                    self.options.get("debug"))    # bool: debug
        else:
            args = (serial,                       # str: serial number 
                    self.wanMac,                  # str: wanMac
                    author,                       # str: operator
                    board,                        # str: board number
                    True,                         # bool: postcheck 
                    self.options.get("debug"))    # bool: debug
        signals = self._task_signals(self._on_postcheck_result)
        run_in_thread(func, args=args, signals=signals)
        return True

    def _on_postcheck_result(self, payload): 
        if not payload.get("ok"): 
            self.log_signal(payload.get("trace", str(payload.get("exc")))) 
            self._set_field_color("sn", "#ff6666") 
            self.stop_operation_timer()
            return 
        res = payload["result"] 
        status = res.get("status", "UNKNOWN") 
        dev_type = res.get("type", "?") 
        self.log_signal(f"[WARN] Запись в БД обновлена: {status} ({dev_type})") 
        if status == "OK": 
            try:
                self.stop_blink_bn() 
            except Exception:
                pass
            if self.options.get("auto_board"):
                commit_board_id(board_id=self.board_number, prefix=self.options.get("board_prefix", "Board_"))
            self._set_field_color("sn", "#66cc66") 
            self._set_field_color("bn", "#66cc66") 
            self._set_workflow("Этап: Готово")
            self.stop_operation_timer()
            self._mark_claimed(self.wanMac)
            self.ready_for_bn = False 
            if self.pending_sheets:
                serial, board = self.pending_sheets
                self.pending_sheets = None
                self._send_to_sheets(serial, board)
            safe_single_shot(2000, lambda: self.reset_fields(reset_timer=False))
        else:
            self.stop_operation_timer()
            self._set_field_color("sn", "#ff6666")
            self._set_field_color("bn", "#ff6666")
            self.log_signal(f"[ERR] Postcheck завершился со статусом {status}")
            self._set_workflow("Этап: Ошибка Postcheck")
            self.pending_sheets = None

    def _on_finished(self, payload):
        if self._closing:
            return
        result = payload.get("result", payload)
        self.busy = False
        self.current_stop_event = None
        self.flash_timed_out = False
        self._stop_flash_watchdog()
        try:
            self.btn_stop.setEnabled(False)
        except Exception:
            pass
        self.log_debug(f"flash: finished ok={result.get('ok')} result={result}")
        self.last_result_ok = result.get("ok", False)
        if result.get("ok"):
            was_test = self._test_mode
            self.log_signal(f"[OK] {result.get('msg')}")
            self.safe_remove_eeprom(self.eeprom_path)
            self._test_mode = False
            # Применяем B/N, если он был отсканирован во время прошивки
            if self.pending_bn:
                pb = self.pending_bn
                self.serial_number = pb.get("sn", self.serial_number)
                self.board_number = pb.get("bn", self.board_number)
                self.lanMac = pb.get("lan", self.lanMac)
                self.wanMac = pb.get("wan", self.wanMac)
                self.pending_bn = None
                self.log_signal(f"[INFO] Применён отложенный B/N: {self.serial_number} | {self.board_number}")
            if was_test:
                self.stop_operation_timer()
                self._set_field_color("sn", "#66cc66")
                self._set_field_color("bn", "#66cc66")
                self.update_finish_status(True)
                self._set_workflow("Этап: Готово (тест)")
                if self.options.get("auto_reset", True):
                    safe_single_shot(2000, lambda: self.reset_fields(reset_timer=False))
                return
            # Не пишем в БД/таблицу, пока устройство не вернётся с новым MAC.
            # Подтверждение приходит из DHCP (_on_dhcp_lease -> _on_flash_mac_confirmed).
            board = (self.board_number or "").strip()
            if board and self.dhcp_worker is not None:
                self.ready_for_bn = False
                # Сравниваем с целевым MAC (wanMac), а не с _expected_flash_mac:
                # DHCP ACK мог прийти до завершения прошивки и сбросить флаг.
                expected = _norm_mac(self.wanMac)
                if expected and _norm_mac(self.dhcp_mac) == expected:
                    # MAC уже подтверждён до завершения прошивки
                    self._await_dhcp_confirm = True
                    self._on_flash_mac_confirmed(self.dhcp_mac)
                else:
                    self._start_dhcp_confirm_wait()
            elif board:
                # DHCP не запущен — подтвердить нельзя, пишем сразу
                self._await_dhcp_confirm = True
                self._on_flash_mac_confirmed(self.dhcp_mac or self.wanMac)
            else:
                # B/N ещё не получен — ждём скан, postcheck только после DHCP
                self._start_dhcp_confirm_wait()
                self.ready_for_bn = True
            return
        # Прошивка не удалась
        self.log_signal(f"[ERR] {result.get('msg')}")
        self._set_workflow("Этап: Ошибка прошивки")
        self.reset_operation_timer()
        self.pending_bn = None
        self._test_mode = False
        self._expected_flash_mac = None
        self._pre_flash_mac = None

    def eeprom_flash_async(self):
        if self._closing:
            return
        if not (self.lanMac or "").strip() and not (self.wanMac or "").strip():
            self.log_signal("[ERR] Mac-адреса отсутствуют")
            return
        # Гейт: прошивать только когда DHCP-сервер выдал свежую аренду.
        # Иначе устройство ещё не в сети и SSH-подключение обречено.
        if self.dhcp_worker is None:
            self.log_signal("[ERR] DHCP-сервер не запущен — прошивка невозможна")
            self._set_workflow("Этап: DHCP-сервер не запущен")
            self.reset_operation_timer()
            return
        lease_time = int(self.options.get("dhcp_lease_time", 60) or 60)
        fresh_window = max(10, lease_time // 2)
        age = time.time() - getattr(self, "_last_dhcp_ack_time", 0.0)
        assigned_ip = self.options.get("dhcp_assigned_ip", "15.15.15.15")
        # Если DHCP-аранды нет/протухла, но assigned_ip пингуется — устройство уже в сети.
        # Это бывает, если у устройства статический IP или аренда давно получена и renew не шлёт.
        if (not self.dhcp_host or age > fresh_window):
            # Пробуем пропинговать assigned_ip
            try:
                import subprocess
                result = subprocess.run(["ping", "-c", "1", "-W", "1", assigned_ip],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                if result.returncode == 0:
                    self.log_signal(f"[DHCP] Устройство доступно на {assigned_ip} (пинг OK), продолжаем без свежей аренды")
                    self.dhcp_host = assigned_ip
                    self._last_dhcp_ack_time = time.time()
                else:
                    raise Exception("no ping")
            except Exception:
                self._start_dhcp_start_wait()
                return
        flash_mode = self.options.get("flash_mode")
        # Целевой MAC для подтверждения по DHCP
        self._expected_flash_mac = _norm_mac(self.wanMac)
        # MAC устройства до прошивки: его DHCP-пакеты (renew/разовое продление)
        # могут прийти уже после reboot и НЕ являются несовпадением.
        self._pre_flash_mac = _norm_mac(self.dhcp_mac)
        # Помечаем устройство как «в работе» (и текущий, и целевой MAC)
        self._mark_claimed(self.dhcp_mac)
        self._mark_claimed(self.wanMac)
        if not self.busy:
            self.busy = True
            self._start_flash_watchdog()
            self.log_signal("[BUSY] Начата прошивка, ввод заблокирован")
        self.current_stop_event = threading.Event()
        try:
            self.btn_stop.setEnabled(True)
        except Exception:
            pass
        signals = self._task_signals(self._on_finished)
        if flash_mode == "eeprom_spi":
            self.log_debug(f"flash: start mode=eeprom_spi lan='{self.lanMac}' wan='{self.wanMac}'")
            self._set_workflow("Этап: Прошивка EEPROM")
            run_in_thread(eeprom_flash, kwargs=dict(
                    lan_mac=self.lanMac, wan_mac=self.wanMac,
                    disable_shell=self.options.get("disable_shell_on_flash", False), install_script=self.options.get("install_script", "install.sh"),
                    stop_event=self.current_stop_event, log=signals.log.emit, host=self.dhcp_host), signals=signals)
        elif flash_mode == "u-boot_environment":
            self.log_debug("flash: start mode=u-boot_environment")
            self._set_workflow("Этап: Прошивка устройства (U-Boot)")
            run_in_thread(flash_mac_direct, kwargs=dict(
                    wan_mac=self.wanMac, disable_shell=self.options.get("disable_shell_on_flash", False),
                    install_script=self.options.get("install_script", "install.sh"), stop_event=self.current_stop_event, log=signals.log.emit, host=self.dhcp_host
                ), signals=signals,)

    def _init_google_sheets(self):
        if getattr(self, "gs_thread", None) and self.gs_thread.isRunning():
            self.log_debug("Google Sheets уже инициализирован")
            return
        gs_credentials_path = None
        gs_spreadsheet_name = None
        gs_style_rules_path = None
        gs_sheet_title = (self.options.get("gs_sheet_title", "") or "").strip() or None
        gs_sheet_rows = self.options.get("gs_sheet_rows", 500)
        gs_sheet_cols = self.options.get("gs_sheet_cols", 2)
        gs_row_template = ((self.options.get("gs_row_template", "") or "").strip() or "{board:<50} |    {serial}    |")
        gs_header_template = ((self.options.get("gs_header_template", "") or "").strip() or "{board:<50} | Всего: {count} | {serial}")
        # Путь к ключу и имя таблицы берутся из опций всегда (не только при кастомном конфиге)
        raw_path = (self.options.get("gs_credentials_path", "") or "").strip()
        if raw_path:
            gs_credentials_path = raw_path if os.path.isabs(raw_path) else os.path.join(self.base_path, raw_path)
        gs_spreadsheet_name = (self.options.get("gs_spreadsheet_name", "") or "").strip() or None
        raw_style_path = (self.options.get("gs_style_rules_path", "") or "").strip()
        if raw_style_path:
            gs_style_rules_path = raw_style_path if os.path.isabs(raw_style_path) else os.path.join(self.base_path, raw_style_path)
        self.gs_manager = GoogleSheetsManager(debug=self.options.get("debug"), author=self.author,
                                              base_path=self.base_path, credentials_path=gs_credentials_path,
                                              spreadsheet_name=gs_spreadsheet_name, style_rules_path=gs_style_rules_path,
                                              row_template=gs_row_template, sheet_title=gs_sheet_title,
                                              sheet_rows=gs_sheet_rows, sheet_cols=gs_sheet_cols,
                                              header_template=gs_header_template)
        self.gs_thread = QThread()
        self.gs_worker = GoogleWorker(self.gs_manager)
        self.gs_worker.moveToThread(self.gs_thread)
        self.gs_worker.log.connect(self._signals.log.emit)
        self.gs_worker.result.connect(self.handle_google_result)
        self.gs_worker.sheet_list_ready.connect(self._populate_sheet_menu)
        self.gs_worker.current_sheet_changed.connect(self.set_current_sheet_label)
        self.request_open_sheet.connect(self.gs_worker.open_sheet_in_browser)
        self.request_stop_worker.connect(self.gs_worker.stop)
        self.request_add_record.connect(self.gs_worker.add_record)
        self.request_delete_record.connect(self.gs_worker.delete_record)
        self.request_fetch_sheets.connect(self.gs_worker.fetch_sheets)
        self.request_check_gs_auth.connect(self.gs_worker.check_auth)
        self.gs_thread.started.connect(self.gs_worker.start)
        self.gs_thread.start()
        self.request_select_sheet.connect(self.gs_worker.select_sheet)
        self.menu_sheets.sheet_selected.connect(self.request_select_sheet.emit)
        self.menu_sheets.aboutToShow.connect(self.request_fetch_sheets.emit)
        self.gs_worker.sheet_status.connect(self.update_sheet_status)
        self._start_gs_auth_timer()
        self._start_gs_sheet_refresh_timer()

    def _populate_sheet_menu(self, sheets):
        if self._closing:
            return
        self.menu_sheets.populate(sheets)
        
    def load_options(self):
        defaults = {
            "debug": False,
            "recovery_mod": False,
            "get_server": False,
            "run_sheets": False,
            "auto_check": True,
            "auto_reset": True,
            "require_board_number": True,
            "send_board_number": True,
            "admin_session_seconds": 600,
            "flash_mode": "eeprom_spi",  # стандартный режим
            "author": "",
            "auto_board": False,
            "board_prefix": "Board",
            "serial_prefix": "CUINF",
            "ssh_user": "",
            "ssh_password": "",
            "phone_path": "",
            "mtd0": "",
            "nand_command": "",
            "flash_erase": "",
            "db_user": "",
            "db_password": "",
            "db_host": "",
            "db_port": 5432,
            "db_name": "",
            "counter_file": "Data/board_counter.txt",
            "disable_shell_on_flash": False,
            "install_script": "install.sh",
            "flash_watchdog_ms": 420000,
            "gs_custom_config": False,
            "gs_credentials_path": "",
            "gs_spreadsheet_name": "Database_board_serial",
            "gs_sheet_title": "",
            "gs_sheet_rows": 500,
            "gs_sheet_cols": 2,
            "gs_sheet_refresh_seconds": 30,
            "gs_style_rules_path": "Data/conditional_format.json",
            "gs_row_template": "{board:<50} |    {serial}    |",
            "gs_header_template": "{board:<50} | Всего: {count} | {serial}",
            "board_check": False,
            "dhcp_interface": "",
            "dhcp_server_ip": "15.15.15.1",
            "dhcp_assigned_ip": "15.15.15.15",
            "dhcp_subnet_mask": "255.255.255.0",
            "dhcp_lease_time": 60,
        }
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                defaults.update(data)
            except Exception as e:
                self.log_signal(f"[ERROR] Не удалось прочитать {self.config_path}: {e}")
        flash_mode = defaults.get("flash_mode")
        if isinstance(flash_mode, bool):
            defaults["flash_mode"] = "eeprom_spi" if flash_mode else "u-boot_environment"
        return defaults

    def save_options(self):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.options, f, indent=4, ensure_ascii=False)
            self.log_signal(f"[OK] Опции сохранены в {self.config_path}")
        except Exception as e:
            self.log_signal(f"[ERROR] Не удалось сохранить {self.config_path}: {e}")

    def open_style_editor(self):
        if self._closing:
            return
        rel = self.options.get("gs_style_rules_path", "Data/conditional_format.json")
        path = rel if os.path.isabs(rel) else os.path.join(self.base_path, rel)
        try:
            dlg = StyleEditorDialog(path, opts=self.options, opts_path=self.config_path, parent=self)
        except Exception as e:
            import traceback
            self.log_signal(f"[ERROR] Не удалось открыть редактор стиля: {e}")
            self.log_signal(traceback.format_exc())
            return
        try:
            if dlg.exec() == QDialog.Accepted:
                self.options = self.load_options()
                # Обновляем параметры активного менеджера листов без пересоздания
                mgr = getattr(self, "gs_manager", None)
                if mgr is not None:
                    mgr.header_template = (self.options.get("gs_header_template", "") or "").strip() or "{board:<50} | Всего: {count} | {serial}"
                    mgr.row_template = (self.options.get("gs_row_template", "") or "").strip() or "{board:<50} |    {serial}    |"
                    mgr.sheet_rows = self.options.get("gs_sheet_rows", 500)
                    mgr.sheet_cols = self.options.get("gs_sheet_cols", 2)
                self.log_signal(f"[OK] Стиль листа сохранён: {path}")
        except Exception as e:
            import traceback
            self.log_signal(f"[ERROR] Ошибка в редакторе стиля: {e}")
            self.log_signal(traceback.format_exc())
        try:
            dlg.deleteLater()
        except Exception:
            pass

    def open_options(self):
        if self._closing:
            return
        try:
            dlg = OptionsDialog(self, opts=self.options)
        except Exception as e:
            import traceback
            self.log_signal(f"[ERROR] Не удалось открыть диалог настроек: {e}")
            self.log_signal(traceback.format_exc())
            return
        try:
            accepted = dlg.exec() == QDialog.Accepted
        except Exception as e:
            import traceback
            self.log_signal(f"[ERROR] Ошибка в диалоге настроек: {e}")
            self.log_signal(traceback.format_exc())
            return

        new_opts = None
        old_opts = dict(self.options)
        if accepted:
            try:
                dlg.save_options(self.config_path)
            except Exception as e:
                self.log_signal(f"[ERROR] Ошибка сохранения настроек в файл: {e}")
                return
            # Перечитываем опции из файла (диалог уже сохранил их туда)
            self.options = self.load_options()
            new_opts = self.options

        # Явно удаляем диалог, чтобы shiboken не пытался удалить C++ объект
        # из неверного контекста при выходе dlg из области видимости.
        try:
            dlg.deleteLater()
        except Exception:
            pass
        dlg = None

        if new_opts is None:
            return

        # Находим только реально изменённые ключи
        changed_keys = [k for k in new_opts if old_opts.get(k) != new_opts[k]]
        if not changed_keys:
            self.log_debug("SETTINGS: Изменений нет — ничего не обновляем")
            return

        dhcp_keys = {"dhcp_interface", "dhcp_server_ip", "dhcp_assigned_ip",
                     "dhcp_subnet_mask", "dhcp_lease_time"}
        gs_keys = {"run_sheets", "gs_custom_config", "gs_credentials_path",
                   "gs_spreadsheet_name", "gs_sheet_title", "gs_sheet_rows",
                   "gs_sheet_cols", "gs_sheet_refresh_seconds",
                   "gs_style_rules_path", "gs_row_template"}
        ui_keys = {"debug", "recovery_mod", "board_check", "auto_check",
                   "auto_reset", "auto_board", "disable_shell_on_flash",
                   "install_script", "board_prefix", "flash_mode",
                   "serial_prefix"}
        ssh_keys = {"ssh_user", "ssh_password", "phone_path",
                    "mtd0", "nand_command", "flash_erase"}
        db_keys = {"db_user", "db_password", "db_host", "db_port",
                   "db_name", "counter_file"}
        managed_keys = dhcp_keys | gs_keys | ui_keys | ssh_keys | db_keys

        # Ключи, которыми управляет диалог настроек (остальные — например
        # admin_session_seconds, author — просто применяются к self.options
        # без действий в GUI и не выводятся в лог как изменения).
        changed_managed = [k for k in changed_keys if k in managed_keys]
        if not changed_managed:
            self.log_debug("SETTINGS: Изменены только служебные ключи, действий в GUI не требуется")
            return

        changed_dhcp = [k for k in changed_managed if k in dhcp_keys]
        changed_gs = [k for k in changed_managed if k in gs_keys]
        changed_ui = [k for k in changed_managed if k in ui_keys]
        self.log_debug(f"SETTINGS: Изменённые ключи: {changed_managed}")
        self.log_signal(f"[SETTINGS] Применены изменения: {', '.join(changed_managed)}")

        # Точечное обновление — каждый компонент обновляется только если
        # изменились именно его ключи, а не всё приложение разом.
        if changed_dhcp:
            try:
                self._stop_dhcp_server()
                self._start_dhcp_server()
                self.log_signal(f"[DHCP] Перезапущен (ключи: {', '.join(changed_dhcp)})")
            except Exception as e:
                self.log_signal(f"[ERROR] Перезапуск DHCP: {e}")

        if changed_gs:
            try:
                self.update_gs()
                self.log_signal(f"[GS] Переинициализирован (ключи: {', '.join(changed_gs)})")
            except Exception as e:
                self.log_signal(f"[ERROR] update_gs: {e}")

        for key in changed_ui:
            try:
                if key == "flash_mode":
                    self.update_ui()
                elif key == "debug":
                    # уровень логирования уже применяется через logging.debug
                    pass
                # recovery_mod, board_check, auto_check, auto_reset,
                # auto_board, disable_shell_on_flash, install_script,
                # board_prefix — не требуют действий в GUI при изменении
            except Exception as e:
                self.log_signal(f"[ERROR] Применение настройки '{key}': {e}")

    def reset_fields(self, reset_timer=True):
        self.sn_field.clear(); self.bn_field.clear()
        self._set_field_color("sn", "#333"); self._set_field_color("bn", "#333")
        self.serial_number = ""; self.board_number = ""
        self.lanMac = ""; self.wanMac = ""
        self.eeprom_path = ""
        self.pending_bn = None
        self.pending_sheets = None
        self.dhcp_host = None
        self.dhcp_mac = None
        self.buffer = ""
        self.busy = False
        self.flash_timed_out = False
        self._await_dhcp_confirm = False
        self._dhcp_confirm_timer.stop()
        self._await_dhcp_start = False
        self._dhcp_start_timer.stop()
        self.ready_for_bn = False
        self.last_result_ok = None
        try:
            self.stop_blink_bn()
        except Exception:
            pass
        self._stop_flash_watchdog()
        if reset_timer:
            self.reset_operation_timer()
        try:
            self.btn_stop.setEnabled(False)
        except Exception:
            pass
        self._set_workflow("Этап: Ожидание сканирования S/N")

    def safe_remove_eeprom(self, eeprom_path):
        try:
            if os.path.exists(eeprom_path):
                if os.path.isfile(eeprom_path): os.remove(eeprom_path); self.status_del = False
        except Exception as e:
            self.log_debug(f"Ошибка при удалении {eeprom_path}: {e}")

    def _try_shutdown(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except Exception as e:
            self.log_debug(f"WARN: Ошибка при завершении: {e}")

    def closeEvent(self, event):
        self._closing = True
        try:
            print("EXIT: Завершаем фоновые процессы...")
            # 1. Сначала отключаем ВСЕ сигналы от воркеров — чтобы больше не приходило в GUI
            if getattr(self, "gs_worker", None):
                try:
                    self.gs_worker.log.disconnect()
                except Exception:
                    pass
                try:
                    self.gs_worker.result.disconnect()
                except Exception:
                    pass
                try:
                    self.gs_worker.sheet_list_ready.disconnect()
                except Exception:
                    pass
                try:
                    self.gs_worker.current_sheet_changed.disconnect()
                except Exception:
                    pass
                try:
                    self.gs_worker.sheet_status.disconnect()
                except Exception:
                    pass
            if getattr(self, "dhcp_worker", None):
                try:
                    self.dhcp_worker.finished.disconnect()
                except Exception:
                    pass
            if getattr(self, "menu_sheets", None):
                try:
                    self.menu_sheets.sheet_selected.disconnect()
                except Exception:
                    pass
                try:
                    self.menu_sheets.aboutToShow.disconnect()
                except Exception:
                    pass
            # 2. Останавливаем все таймеры
            for timer in self.findChildren(QTimer):
                self._try_shutdown(timer.stop)
            # 3. Останавливаем Google Sheets (ждёт завершения потока)
            if getattr(self, "gs_worker", None) and getattr(self, "gs_thread", None):
                try:
                    self._stop_gs_timers()
                    self._try_shutdown(self.request_stop_worker.emit)
                    if self.gs_thread.isRunning():
                        self._try_shutdown(self.gs_thread.quit)
                        t0 = time.time()
                        while self.gs_thread.isRunning() and (time.time() - t0) < 10.0:
                            self.gs_thread.wait(200)
                        if self.gs_thread.isRunning():
                            print("WARN: Поток GoogleWorker не завершился за 10с")
                    print("OK: Поток GoogleWorker остановлен")
                except Exception as e:
                    print(f"WARN: Ошибка при остановке GoogleWorker: {e}")
                self.gs_worker = None
                self.gs_thread = None
                print("OK: Ссылки GoogleWorker обнулены")
            # 4. Останавливаем DHCP-сервер (ждёт завершения потока)
            self._stop_dhcp_server()
            # 5. Останавливаем DeviceWatcher (ждёт завершения потока)
            if getattr(self, "device_watcher", None):
                stop_dw = getattr(self.device_watcher, "stop", None)
                if callable(stop_dw):
                    self._try_shutdown(stop_dw)
            if getattr(self, "device_watcher_thread", None) and self.device_watcher_thread.isRunning():
                self._try_shutdown(self.device_watcher_thread.quit)
                t0 = time.time()
                while self.device_watcher_thread.isRunning() and (time.time() - t0) < 10.0:
                    self.device_watcher_thread.wait(200)
                if self.device_watcher_thread.isRunning():
                    print("WARN: Поток DeviceWatcher не завершился за 10с")
                print("OK: Поток DeviceWatcher остановлен")
        except Exception as e:
            print(f"ERR: Ошибка при завершении потоков: {e}")
        event.accept()

    def start_operation_timer(self):
        self.op_start_time = time.time()
        if not hasattr(self, "op_timer"):
            self.op_timer = QTimer(self)
            self.op_timer.timeout.connect(self._update_op_timer)
        self.timer_label.setText("Время: 00:00")
        self.timer_label.setStyleSheet("color: #ffcc33; font-weight: bold; margin: 0 10px;")
        self.op_timer.start(1000)

    def stop_operation_timer(self):
        if hasattr(self, "op_timer") and self.op_timer.isActive():
            self.op_timer.stop()
            self.timer_label.setStyleSheet("color: #66cc66; font-weight: bold; margin: 0 10px;")

    def reset_operation_timer(self):
        if hasattr(self, "op_timer") and self.op_timer.isActive():
            self.op_timer.stop()
        self.timer_label.setText("Время: 00:00")
        self.timer_label.setStyleSheet("color: #ffcc33; font-weight: bold; margin: 0 10px;")

    def _update_op_timer(self):
        elapsed = int(time.time() - self.op_start_time)
        mins, secs = divmod(elapsed, 60)
        self.timer_label.setText(f"Время: {mins:02d}:{secs:02d}")
