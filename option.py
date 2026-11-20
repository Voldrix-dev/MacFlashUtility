import os
import qtawesome as qta
from PySide6.QtWidgets import (
    QVBoxLayout, QDialog, QLabel, QDialogButtonBox,
    QWidget, QHBoxLayout, QLineEdit, QGroupBox, QPushButton, QButtonGroup, QTabWidget, QFileDialog, QComboBox, QSpinBox, QScrollArea, QGridLayout
)
from PySide6.QtCore import Qt, Property, QPropertyAnimation
from PySide6.QtGui import QPainter, QColor, QBrush
from i18n import tr, get_current_locale, set_locale
from style import opstion_style
from dhcp_server import DhcpServer


class ToggleSwitch(QWidget):
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._offset = 1 if not checked else 14
        self.setFixedSize(30, 16)
        self.animation = QPropertyAnimation(self, b"offset", self)
        self.animation.setDuration(120)

    def mousePressEvent(self, event):
        self._checked = not self._checked
        self.animation.stop()
        self.animation.setStartValue(self._offset)
        self.animation.setEndValue(1 if not self._checked else 14)
        self.animation.start()
        self.update()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bg_color = QColor("#007acc") if self._checked else QColor("#555")
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, 30, 16, 8, 8)
        painter.setBrush(QBrush(QColor("#fff")))
        painter.drawEllipse(self._offset, 1, 14, 14)

    def getOffset(self):
        return self._offset

    def setOffset(self, value):
        self._offset = value
        self.update()

    offset = Property(int, fget=getOffset, fset=setOffset)

    def isChecked(self):
        return self._checked

class OptionsDialog(QDialog):
    def __init__(self, parent=None, opts=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Options"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self.opts = opts or {}
        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data")
        self.setObjectName("OptionsDialog")
        opstion_style(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QLabel(tr("Utility parameters"))
        header.setStyleSheet("font-size: 12pt; font-weight: 700; color: #eef5ff;")
        layout.addWidget(header)

        # Language selector
        lang_layout = QHBoxLayout()
        lang_label = QLabel(tr("Language:"))
        lang_label.setStyleSheet("font-size: 10pt; color: #aaccff;")
        self.cb_language = QComboBox()
        self.cb_language.addItem(tr("Russian"), "ru")
        self.cb_language.addItem(tr("English"), "en")
        current_locale = get_current_locale()
        idx = self.cb_language.findData(current_locale)
        if idx >= 0:
            self.cb_language.setCurrentIndex(idx)
        self.cb_language.currentIndexChanged.connect(self._on_language_changed)
        lang_layout.addWidget(lang_label)
        lang_layout.addWidget(self.cb_language)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        tabs = QTabWidget()
        tab_general = QWidget()
        tab_general_outer = QVBoxLayout(tab_general)
        tab_general_outer.setContentsMargins(0, 0, 0, 0)
        tab_general_outer.setSpacing(0)
        general_scroll = QScrollArea()
        general_scroll.setWidgetResizable(True)
        general_scroll.setFrameShape(QScrollArea.NoFrame)
        general_scroll_body = QWidget()
        tab_general_layout = QVBoxLayout(general_scroll_body)
        tab_general_layout.setContentsMargins(8, 8, 8, 8)
        tab_general_layout.setSpacing(10)
        general_scroll.setWidget(general_scroll_body)
        tab_general_outer.addWidget(general_scroll)

        tab_device = QWidget()
        tab_device_outer = QVBoxLayout(tab_device)
        tab_device_outer.setContentsMargins(0, 0, 0, 0)
        tab_device_outer.setSpacing(0)
        device_scroll = QScrollArea()
        device_scroll.setWidgetResizable(True)
        device_scroll.setFrameShape(QScrollArea.NoFrame)
        device_scroll_body = QWidget()
        tab_device_layout = QVBoxLayout(device_scroll_body)
        tab_device_layout.setContentsMargins(8, 8, 8, 8)
        tab_device_layout.setSpacing(10)
        device_scroll.setWidget(device_scroll_body)
        tab_device_outer.addWidget(device_scroll)

        group_main = QGroupBox("Основные параметры")
        group_main_layout = QVBoxLayout(group_main)
        group_main_layout.setSpacing(8)

        self.sl_debug = self._make_switch("Режим отладки", self.opts.get("debug", False), "Вывод подробной информации в лог.")
        self.sl_recovery = self._make_switch("Режим восстановления", self.opts.get("recovery_mod", False), "Режим восстановления MAC-адреса на устройстве.")
        self.sl_bord_check = self._make_switch("Проверка board", self.opts.get("board_check", False), "Проверка наличия поля board в базе данных.")
        self.sl_sheets = self._make_switch("Работа с Google Sheets", self.opts.get("run_sheets", False), "Автоматическая запись в Google Sheets.")
        self.sl_auto_check = self._make_switch("Авто проверка S/N", self.opts.get("auto_check", True), "Проверять S/N автоматически.")
        self.sl_auto_reset = self._make_switch("Автосброс полей", self.opts.get("auto_reset", True), "Очищать поля после завершения операции.")
        self.sl_auto_board = self._make_switch("Автогенерация номера платы", self.opts.get("auto_board", True), "Если включено — номер board_XX генерируется автоматически.")
        self.sl_auto_db_update = self._make_switch("Обновлять БД после прошивки", self.opts.get("auto_db_update", True), "Если выключено — БД и Google Sheets не обновляются (для тестов).")
        self.sl_disable_shell = self._make_switch("Применять sh скрипты при прошивке", self.opts.get("disable_shell_on_flash", False),
                                                  "Перед прошивкой применяет Data/*.sh (без reboot), затем продолжает SPI/MAC и штатную перезагрузку.")
        group_main_layout.addWidget(self.sl_debug)
        group_main_layout.addWidget(self.sl_recovery)
        group_main_layout.addWidget(self.sl_bord_check)
        group_main_layout.addWidget(self.sl_sheets)
        group_main_layout.addWidget(self.sl_auto_check)
        group_main_layout.addWidget(self.sl_auto_reset)
        group_main_layout.addWidget(self.sl_auto_board)
        group_main_layout.addWidget(self.sl_auto_db_update)
        group_main_layout.addWidget(self.sl_disable_shell)
        tab_general_layout.addWidget(group_main)

        group_flash = QGroupBox("Параметры прошивки")
        group_flash_layout = QVBoxLayout(group_flash)
        group_flash_layout.setSpacing(8)

        prefix_label = QLabel("Префикс платы:")
        prefix_label.setToolTip("Например: board, brd, pb, pcb...")

        self.ed_board_prefix = QLineEdit()
        self.ed_board_prefix.setText(self.opts.get("board_prefix", "board"))
        self.ed_board_prefix.setFocusPolicy(Qt.ClickFocus)

        group_flash_layout.addWidget(prefix_label)
        group_flash_layout.addWidget(self.ed_board_prefix)

        serial_prefix_label = QLabel("Префикс S/N:")
        serial_prefix_label.setToolTip("Префикс серийного номера для автоопределения типа кода (например: CUINF, QRINF, Q_ITL...)")

        self.ed_serial_prefix = QLineEdit()
        self.ed_serial_prefix.setText(self.opts.get("serial_prefix", "CUINF"))
        self.ed_serial_prefix.setFocusPolicy(Qt.ClickFocus)

        group_flash_layout.addWidget(serial_prefix_label)
        group_flash_layout.addWidget(self.ed_serial_prefix)

        install_script_label = QLabel("SH скрипт перед прошивкой:")
        install_script_label.setToolTip("Скрипт из Data/*.sh, который будет выполнен перед EEPROM/SPI.")
        
        self.cb_install_script = QComboBox()
        self.cb_install_script.setEditable(True)
        self.cb_install_script.setFocusPolicy(Qt.ClickFocus)

        self._populate_install_scripts()

        self.btn_install_script_browse = QPushButton()
        self.btn_install_script_browse.setIcon(qta.icon("fa5s.folder-open", color="#11111b"))
        self.btn_install_script_browse.setToolTip("Выбрать SH-скрипт")
        self.btn_install_script_browse.setFixedWidth(40)
        self.btn_install_script_browse.clicked.connect(self._browse_install_script_file)

        install_script_row = QHBoxLayout()
        install_script_row.setSpacing(8)
        install_script_row.addWidget(self.cb_install_script)
        install_script_row.addWidget(self.btn_install_script_browse)

        group_flash_layout.addWidget(install_script_label)
        group_flash_layout.addLayout(install_script_row)

        flash_mode_label = QLabel("Режим прошивки:")
        flash_mode_label.setToolTip("Выберите метод прошивки:\n• eeprom_spi — запись EEPROM через SPI.\n• u-boot_environment — установка MAC в U-Boot.")

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.btn_mode_eeprom = QPushButton("eeprom_spi")
        self.btn_mode_uboot = QPushButton("u-boot_environment")
        for btn in (self.btn_mode_eeprom, self.btn_mode_uboot):
            btn.setCheckable(True)
            btn.setObjectName("ModeBtn")
            self.mode_group.addButton(btn)
            mode_row.addWidget(btn)

        current_mode = self.opts.get("flash_mode", "eeprom_spi")
        if current_mode == "u-boot_environment":
            self.btn_mode_uboot.setChecked(True)
        else:
            self.btn_mode_eeprom.setChecked(True)

        group_flash_layout.addWidget(flash_mode_label)
        group_flash_layout.addLayout(mode_row)
        tab_general_layout.addWidget(group_flash)

        group_ssh = QGroupBox("SSH / команды прошивки устройства")
        group_ssh_layout = QGridLayout(group_ssh)
        group_ssh_layout.setHorizontalSpacing(10)
        group_ssh_layout.setVerticalSpacing(4)

        def _add_ssh_field(title, tooltip, key, default, row, col):
            group_ssh_layout.addWidget(QLabel(title), row, col)
            edit = QLineEdit()
            edit.setText(self.opts.get(key, default))
            edit.setToolTip(tooltip)
            edit.setFocusPolicy(Qt.ClickFocus)
            group_ssh_layout.addWidget(edit, row, col + 1)
            return edit

        self.ed_ssh_user = _add_ssh_field("User:", "Пользователь для подключения к устройству.", "ssh_user", "", 0, 0)
        self.ed_ssh_secret = _add_ssh_field("Password:", "Пароль SSH-пользователя устройства.", "ssh_password", "", 0, 2)
        self.ed_ssh_secret.setEchoMode(QLineEdit.Password)
        self.ed_ssh_phone_path = _add_ssh_field("Device Path:", "Путь для передачи EEPROM на устройство.", "phone_path", "", 1, 0)
        self.ed_ssh_mtd0 = _add_ssh_field("Flash:", "Устройство MTD для EEPROM.", "mtd0", "", 1, 2)
        self.ed_ssh_nand_command = _add_ssh_field("Write Cmd:", "Команда записи EEPROM в mtd.", "nand_command", "", 2, 0)
        self.ed_ssh_flash_erase = _add_ssh_field("Erase Cmd:", "Команда очистки mtd перед записью.", "flash_erase", "", 2, 2)

        tab_device_layout.addWidget(group_ssh)

        group_db = QGroupBox("База данных / журнал board")
        group_db_layout = QGridLayout(group_db)
        group_db_layout.setHorizontalSpacing(10)
        group_db_layout.setVerticalSpacing(4)

        def _add_db_field(title, tooltip, key, default, row, col):
            group_db_layout.addWidget(QLabel(title), row, col)
            edit = QLineEdit()
            edit.setText(self.opts.get(key, default))
            edit.setToolTip(tooltip)
            edit.setFocusPolicy(Qt.ClickFocus)
            group_db_layout.addWidget(edit, row, col + 1)
            return edit

        self.ed_db_user = _add_db_field("User:", "Пользователь PostgreSQL.", "db_user", "", 0, 0)
        self.ed_db_password = _add_db_field("Password:", "Пароль PostgreSQL.", "db_password", "", 0, 2)
        self.ed_db_password.setEchoMode(QLineEdit.Password)
        self.ed_db_host = _add_db_field("Host:", "Адрес сервера PostgreSQL.", "db_host", "", 1, 0)

        group_db_layout.addWidget(QLabel("Port:"), 1, 2)
        self.sp_db_port = QSpinBox()
        self.sp_db_port.setRange(1, 65535)
        self.sp_db_port.setValue(int(self.opts.get("db_port") or 5432))
        group_db_layout.addWidget(self.sp_db_port, 1, 3)

        self.ed_db_name = _add_db_field("Name:", "Имя базы данных.", "db_name", "", 2, 0)
        self.ed_counter_file = _add_db_field("Log (B/N):", "Путь к файлу журнала номеров плат.", "counter_file", "Data/board_counter.txt", 2, 2)

        tab_device_layout.addWidget(group_db)

        tab_gs = QWidget()
        tab_gs_layout = QVBoxLayout(tab_gs)
        tab_gs_layout.setContentsMargins(0, 0, 0, 0)
        tab_gs_layout.setSpacing(10)

        group_gs_main = QGroupBox("Основные параметры листа")

        group_gs_main_layout = QVBoxLayout(group_gs_main)
        group_gs_main_layout.setSpacing(8)

        group_gs_custom = QGroupBox("Кастомные параметры подключения")
        group_gs_custom_layout = QVBoxLayout(group_gs_custom)
        group_gs_custom_layout.setSpacing(8)
        self.sl_gs_custom = self._make_switch("Кастомные GS-параметры", self.opts.get("gs_custom_config", False), "Включить ручной ввод пути к ключу, имени таблицы и файла стиля.")

        gs_key_label = QLabel("Путь к JSON-ключу:")

        self.ed_gs_key_path = QLineEdit()
        self.ed_gs_key_path.setText(self.opts.get("gs_credentials_path", ""))
        self.ed_gs_key_path.setFocusPolicy(Qt.ClickFocus)

        self.btn_gs_key_browse = QPushButton()
        self.btn_gs_key_browse.setIcon(qta.icon("fa5s.folder-open", color="#11111b"))
        self.btn_gs_key_browse.setToolTip("Выбрать JSON-ключ Google")
        self.btn_gs_key_browse.setFixedWidth(40)
        self.btn_gs_key_browse.clicked.connect(self._browse_gs_key_file)

        gs_key_row = QHBoxLayout()
        gs_key_row.setSpacing(8)
        gs_key_row.addWidget(self.ed_gs_key_path)
        gs_key_row.addWidget(self.btn_gs_key_browse)

        gs_table_label = QLabel("Имя таблицы:")

        self.ed_gs_spreadsheet = QLineEdit()
        self.ed_gs_spreadsheet.setText(self.opts.get("gs_spreadsheet_name", "Database_board_serial"))
        self.ed_gs_spreadsheet.setFocusPolicy(Qt.ClickFocus)

        gs_sheet_title_label = QLabel("Название нового листа:")
        gs_sheet_title_label.setToolTip("Пусто — дата и оператор: {date}_{author}.\nДоступны шаблоны {date}, {author}, {year}, {month}, {day}.")

        self.cb_gs_sheet_title = QComboBox()
        self.cb_gs_sheet_title.setEditable(True)
        self.cb_gs_sheet_title.addItems(["{date}_{author}", "{author}_{date}", "{date}", "{year}-{month}-{day}_{author}", "{author}"])

        current_sheet_title = (self.opts.get("gs_sheet_title", "") or "").strip()
        if current_sheet_title:
            idx = self.cb_gs_sheet_title.findText(current_sheet_title)
            if idx >= 0:
                self.cb_gs_sheet_title.setCurrentIndex(idx)
            else:
                self.cb_gs_sheet_title.setEditText(current_sheet_title)
        else:
            self.cb_gs_sheet_title.setEditText("")
        self.cb_gs_sheet_title.lineEdit().setPlaceholderText("{date}_{author}")
        self.cb_gs_sheet_title.setFocusPolicy(Qt.ClickFocus)

        gs_refresh_row = QHBoxLayout()

        gs_refresh_label = QLabel("Обновлять список листов каждые:")

        self.sp_gs_sheet_refresh = QSpinBox()
        self.sp_gs_sheet_refresh.setRange(5, 3600)
        self.sp_gs_sheet_refresh.setSuffix(" сек")
        self.sp_gs_sheet_refresh.setValue(int(self.opts.get("gs_sheet_refresh_seconds", 30)))

        gs_refresh_row.addWidget(gs_refresh_label)
        gs_refresh_row.addWidget(self.sp_gs_sheet_refresh)
        gs_refresh_row.addStretch()

        gs_style_label = QLabel("Файл стиля листа (JSON):")

        self.ed_gs_style_path = QLineEdit()
        self.ed_gs_style_path.setText(self.opts.get("gs_style_rules_path", "Data/conditional_format.json"))
        self.ed_gs_style_path.setFocusPolicy(Qt.ClickFocus)

        self.btn_gs_style_browse = QPushButton()
        self.btn_gs_style_browse.setIcon(qta.icon("fa5s.folder-open", color="#11111b"))
        self.btn_gs_style_browse.setToolTip("Выбрать JSON-стиль листа")
        self.btn_gs_style_browse.setFixedWidth(40)
        self.btn_gs_style_browse.clicked.connect(self._browse_gs_style_file)

        gs_style_row = QHBoxLayout()
        gs_style_row.setSpacing(8)
        gs_style_row.addWidget(self.ed_gs_style_path)
        gs_style_row.addWidget(self.btn_gs_style_browse)

        gs_hint = QLabel("Размер листа, ширины столбцов и шаблоны строки/заголовка редактируются во вкладке «Редактор стиля листа».")

        gs_hint.setStyleSheet("color: #9aa7b8;")

        gs_hint.setWordWrap(True)

        self.sl_gs_custom.switch.mousePressEvent = self._wrap_switch_click(self.sl_gs_custom.switch.mousePressEvent, self._update_gs_fields_enabled)

        group_gs_main_layout.addWidget(gs_sheet_title_label)

        group_gs_main_layout.addWidget(self.cb_gs_sheet_title)

        group_gs_main_layout.addLayout(gs_refresh_row)

        group_gs_main_layout.addWidget(gs_hint)

        group_gs_custom_layout.addWidget(self.sl_gs_custom)
        group_gs_custom_layout.addWidget(gs_key_label)
        group_gs_custom_layout.addLayout(gs_key_row)
        group_gs_custom_layout.addWidget(gs_table_label)
        group_gs_custom_layout.addWidget(self.ed_gs_spreadsheet)
        group_gs_custom_layout.addWidget(gs_style_label)
        group_gs_custom_layout.addLayout(gs_style_row)

        group_gs_main.setLayout(group_gs_main_layout)

        group_gs_custom.setLayout(group_gs_custom_layout)

        tab_gs_layout.addWidget(group_gs_main)
        tab_gs_layout.addWidget(group_gs_custom)

        self._update_gs_fields_enabled()

        tab_general_layout.addStretch()
        tab_device_layout.addStretch()

        tabs.addTab(tab_general, "Основные")
        tabs.addTab(tab_gs, "Google Sheets")
        tabs.addTab(tab_device, "Устройство / БД")

        # -------------------- DHCP tab --------------------
        tab_dhcp = QWidget()
        tab_dhcp_layout = QVBoxLayout(tab_dhcp)
        tab_dhcp_layout.setContentsMargins(0, 0, 0, 0)
        tab_dhcp_layout.setSpacing(10)

        group_dhcp_main = QGroupBox("Настройки DHCP-сервера (всегда включён)")
        group_dhcp_main_layout = QVBoxLayout(group_dhcp_main)
        group_dhcp_main_layout.setSpacing(8)

        # Interface row
        iface_row = QHBoxLayout()
        iface_row.setSpacing(8)
        iface_label = QLabel("Сетевой интерфейс:")
        self.cb_dhcp_iface = QComboBox()
        self.cb_dhcp_iface.setEditable(False)
        self.cb_dhcp_iface.setFocusPolicy(Qt.ClickFocus)
        self.btn_dhcp_refresh = QPushButton()
        self.btn_dhcp_refresh.setIcon(qta.icon("fa5s.sync-alt", color="#11111b"))
        self.btn_dhcp_refresh.setToolTip("Обновить список интерфейсов")
        self.btn_dhcp_refresh.setFixedWidth(40)
        self.btn_dhcp_refresh.clicked.connect(self._refresh_dhcp_interfaces)
        iface_row.addWidget(iface_label)
        iface_row.addWidget(self.cb_dhcp_iface, 1)
        iface_row.addWidget(self.btn_dhcp_refresh)
        group_dhcp_main_layout.addLayout(iface_row)

        def _make_dhcp_field(title, default_value):
            box = QGroupBox(title, self)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(8, 14, 8, 6)
            box_layout.setSpacing(0)
            edit = QLineEdit(default_value, box)
            box_layout.addWidget(edit)
            return box, edit

        gb_server_ip, self.ed_dhcp_server_ip = _make_dhcp_field(
            "IP сервера", self.opts.get("dhcp_server_ip", "15.15.15.1"))
        gb_assigned_ip, self.ed_dhcp_assigned_ip = _make_dhcp_field(
            "IP клиенту", self.opts.get("dhcp_assigned_ip", "15.15.15.15"))
        gb_mask, self.ed_dhcp_mask = _make_dhcp_field(
            "Маска подсети", self.opts.get("dhcp_subnet_mask", "255.255.255.0"))
        gb_lease, self.ed_dhcp_lease = _make_dhcp_field(
            "Аренда (сек)", str(self.opts.get("dhcp_lease_time", 60)))

        fields_row = QHBoxLayout()
        fields_row.setSpacing(12)
        fields_row.addWidget(gb_server_ip)
        fields_row.addWidget(gb_assigned_ip)
        fields_row.addWidget(gb_mask)
        fields_row.addWidget(gb_lease)
        group_dhcp_main_layout.addLayout(fields_row)

        group_dhcp_main.setLayout(group_dhcp_main_layout)
        tab_dhcp_layout.addWidget(group_dhcp_main)
        tab_dhcp_layout.addStretch()
        tabs.addTab(tab_dhcp, "DHCP-сервер")

        # Initial fill of interfaces
        self._refresh_dhcp_interfaces()

        # -------------------- end DHCP tab --------------------

        layout.addWidget(tabs)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _make_switch(self, text, checked=False, tooltip=""):
        container = QWidget()
        hlayout = QHBoxLayout(container)
        hlayout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(text)
        label.setToolTip(tooltip)
        switch = ToggleSwitch(checked)
        switch.setToolTip(tooltip)
        hlayout.addWidget(label)
        hlayout.addStretch()
        hlayout.addWidget(switch)
        container.switch = switch
        return container



    def _wrap_switch_click(self, original_handler, callback):
        def _handler(event):
            original_handler(event)
            callback()
        return _handler

    def _populate_install_scripts(self):
        scripts = []
        try:
            scripts = sorted(name for name in os.listdir(self.data_dir) if name.endswith(".sh") and os.path.isfile(os.path.join(self.data_dir, name)))
        except Exception:
            scripts = []
        current = (self.opts.get("install_script", "install.sh") or "install.sh").strip()
        if current and current not in scripts:
            scripts.insert(0, current)
        self.cb_install_script.addItems(scripts or ["install.sh"])
        idx = self.cb_install_script.findText(current)
        if idx >= 0:
            self.cb_install_script.setCurrentIndex(idx)
        else:
            self.cb_install_script.setEditText(current)

    def _update_gs_fields_enabled(self):
        enabled = self.sl_gs_custom.switch.isChecked()
        self.ed_gs_key_path.setEnabled(enabled)
        self.btn_gs_key_browse.setEnabled(enabled)
        self.ed_gs_spreadsheet.setEnabled(enabled)
        self.ed_gs_style_path.setEnabled(enabled)
        self.btn_gs_style_browse.setEnabled(enabled)

    def _browse_gs_key_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите JSON-ключ Google", "", "JSON files (*.json);;All files (*)")
        if path:
            self.ed_gs_key_path.setText(path)

    def _browse_gs_style_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите JSON-стиль листа", "", "JSON files (*.json);;All files (*)")
        if path:
            self.ed_gs_style_path.setText(path)

    def _browse_install_script_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите SH скрипт", self.data_dir, "Shell scripts (*.sh);;All files (*)")
        if path:
            try:
                if os.path.dirname(os.path.abspath(path)) == os.path.abspath(self.data_dir):
                    path = os.path.basename(path)
            except Exception:
                pass
            self.cb_install_script.setEditText(path)

    def _refresh_dhcp_interfaces(self):
        """Заполнить список интерфейсов для DHCP, исключая системные."""
        self.cb_dhcp_iface.clear()
        try:
            ifaces = DhcpServer.list_interfaces()
        except Exception:
            ifaces = []
        valid = [i for i in ifaces if i and not DhcpServer.is_system_interface(i)]
        self.cb_dhcp_iface.addItems(valid)
        if valid:
            saved = self.opts.get("dhcp_interface", "")
            if saved and saved in valid:
                self.cb_dhcp_iface.setCurrentText(saved)
            else:
                self.cb_dhcp_iface.setCurrentIndex(0)

    def _on_language_changed(self, index):
        locale = self.cb_language.itemData(index)
        if locale:
            set_locale(locale)
            # Update window title immediately
            self.setWindowTitle(tr("Options"))

    def get_options(self):
        return {
            "debug": self.sl_debug.switch.isChecked(),
            "recovery_mod": self.sl_recovery.switch.isChecked(),
            "board_check": self.sl_bord_check.switch.isChecked(),
            "run_sheets": self.sl_sheets.switch.isChecked(),
            "auto_check": self.sl_auto_check.switch.isChecked(),
            "auto_reset": self.sl_auto_reset.switch.isChecked(),
            "auto_board": self.sl_auto_board.switch.isChecked(),
            "auto_db_update": self.sl_auto_db_update.switch.isChecked(),
            "disable_shell_on_flash": self.sl_disable_shell.switch.isChecked(),
            "install_script": self.cb_install_script.currentText().strip() or "install.sh",
            "board_prefix": self.ed_board_prefix.text(),
            "serial_prefix": self.ed_serial_prefix.text(),
            "ssh_user": self.ed_ssh_user.text().strip(),
            "ssh_password": self.ed_ssh_secret.text(),
            "phone_path": self.ed_ssh_phone_path.text().strip(),
            "mtd0": self.ed_ssh_mtd0.text().strip(),
            "nand_command": self.ed_ssh_nand_command.text().strip(),
            "flash_erase": self.ed_ssh_flash_erase.text().strip(),
            "db_user": self.ed_db_user.text().strip(),
            "db_password": self.ed_db_password.text(),
            "db_host": self.ed_db_host.text().strip(),
            "db_port": self.sp_db_port.value(),
            "db_name": self.ed_db_name.text().strip(),
            "counter_file": self.ed_counter_file.text().strip() or "Data/board_counter.txt",
            "gs_custom_config": self.sl_gs_custom.switch.isChecked(),
            "gs_credentials_path": self.ed_gs_key_path.text(),
            "gs_spreadsheet_name": self.ed_gs_spreadsheet.text(),
            "gs_sheet_title": self.cb_gs_sheet_title.currentText().strip(),
            "gs_sheet_rows": int(self.opts.get("gs_sheet_rows", 500)),
            "gs_sheet_cols": int(self.opts.get("gs_sheet_cols", 2)),
            "gs_sheet_refresh_seconds": self.sp_gs_sheet_refresh.value(),
            "gs_style_rules_path": self.ed_gs_style_path.text(),
            "gs_row_template": self.opts.get("gs_row_template", "{board:<50} |    {serial}    |"),
            "gs_header_template": self.opts.get("gs_header_template", "{board:<50} | Всего: {count} | {serial}"),
            "flash_mode": "u-boot_environment" if self.btn_mode_uboot.isChecked() else "eeprom_spi",
            "dhcp_interface": self.cb_dhcp_iface.currentText().strip(),
            "dhcp_server_ip": self.ed_dhcp_server_ip.text().strip(),
            "dhcp_assigned_ip": self.ed_dhcp_assigned_ip.text().strip(),
            "dhcp_subnet_mask": self.ed_dhcp_mask.text().strip(),
            "dhcp_lease_time": int(self.ed_dhcp_lease.text().strip() or 60) if self.ed_dhcp_lease.text().strip().isdigit() else 60,
        }

    def save_options(self, path: str):
        """Сохранить настройки диалога в JSON, не затирая ключи, которыми он не управляет."""
        import json
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data.update(self.get_options())
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
