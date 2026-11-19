#!/usr/bin/env python3
# Запускать с sudo (или через kdesu / pkexec)!
# Требуются установленные пакеты scapy, PySide6, qtawesome.

import sys, os
import json
import subprocess
import ipaddress
import datetime as dt
import qtawesome as qta

from scapy.all import *
from scapy.layers.l2 import Ether
from scapy.layers.inet import IP, UDP
from scapy.layers.dhcp import DHCP, BOOTP

from PySide6.QtCore import QObject, QThread, Signal, Slot, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLineEdit, QComboBox, QPushButton, QTextEdit, QMessageBox, QGroupBox)


class DhcpWorker(QObject):
    log_signal = Signal(str, str)  # text, tag
    finished_signal = Signal()

    def __init__(self, interface, server_ip, assigned_ip, subnet_mask, lease_time):
        super().__init__()
        self.interface = interface
        self.server_ip = server_ip
        self.assigned_ip = assigned_ip
        self.subnet_mask = subnet_mask
        self.lease_time = int(lease_time)
        self.running = False

    @Slot()
    def run_server(self):
        self.running = True

        try: # Настройка сетевого интерфейса
            self.log_signal.emit(f"Настройка интерфейса {self.interface}...", "info")
            subprocess.run(["ip", "addr", "flush", "dev", self.interface], check=True) # Сброс старых адресов
            # Вычисляем CIDR из маски подсети
            network = ipaddress.IPv4Network(f"{self.server_ip}/{self.subnet_mask}", strict=False)
            cidr = network.prefixlen
            subprocess.run(["ip", "addr", "add", f"{self.server_ip}/{cidr}", "dev", self.interface], check=True) # Добавление нового адреса сервера
            subprocess.run(["ip", "link", "set", self.interface, "up"], check=True) # Активация интерфейса
            subprocess.run(["ip", "neigh", "flush", "dev", self.interface], check=False) # Очистка ARP/neighbor кеша
            self.log_signal.emit(f"Интерфейс {self.interface} готов. IP: {self.server_ip}/{cidr}", "info")
        except Exception as e:
            self.log_signal.emit(f"Ошибка настройки интерфейса {self.interface}: {e}", "error")
            self.running = False
            self.finished_signal.emit()
            return
        
        self.broadcast_address = str(network.broadcast_address)
        
        self.log_signal.emit(f"Мини-DHCP сервер запущен на {self.interface} → выдаёт {self.assigned_ip}", "info")

        while self.running: # Используем таймаут, чтобы периодически проверять флаг self.running
            try:
                sniff(filter="udp and (port 67 or 68)", iface=self.interface, prn=self.handle_dhcp, store=0, timeout=0.5)
            except Exception as e:
                if self.running:
                    self.log_signal.emit(f"Ошибка захвата пакетов: {e}", "error")
                    break

        self.cleanup_interface() # Восстановление интерфейса при завершении
        self.finished_signal.emit()

    def cleanup_interface(self):
        try:
            self.log_signal.emit(f"Очистка настроек интерфейса {self.interface}...", "info")
            subprocess.run(["ip", "addr", "flush", "dev", self.interface], check=False)
            subprocess.run(["ip", "link", "set", self.interface, "down"], check=False)
            self.log_signal.emit("Интерфейс очищен и отключен.", "info")
        except Exception as e:
            self.log_signal.emit(f"Ошибка при очистке интерфейса: {e}", "error")

    def get_dhcp_message_type(self, dhcp_options):
        for opt in dhcp_options:
            if isinstance(opt, tuple) and opt[0] == "message-type":
                return opt[1]
        return None

    def get_dhcp_option(self, dhcp_options, name):
        for opt in dhcp_options:
            if isinstance(opt, tuple) and opt[0] == name:
                return opt[1]
        return None

    def handle_dhcp(self, pkt):
        if DHCP not in pkt or BOOTP not in pkt or Ether not in pkt:
            return
        
        dhcp_options = pkt[DHCP].options
        dhcp_type = self.get_dhcp_message_type(dhcp_options)
        if dhcp_type is None:
            return
        
        requested_ip = self.get_dhcp_option(dhcp_options, "requested_addr")
        mac = pkt[Ether].src
        xid = pkt[BOOTP].xid
        chaddr = pkt[BOOTP].chaddr
        ciaddr = pkt[BOOTP].ciaddr

        try:
            my_mac = get_if_hwaddr(self.interface)
        except Exception:
            my_mac = "00:00:00:00:00:00"

        ether = Ether(dst=mac, src=my_mac)
        ip = IP(src=self.server_ip, dst="255.255.255.255")
        udp = UDP(sport=67, dport=68)
        bootp = BOOTP(op=2, yiaddr=self.assigned_ip, siaddr=self.server_ip, chaddr=chaddr, xid=xid)
        options_common = [
            ("server_id", self.server_ip),
            ("subnet_mask", self.subnet_mask),
            ("router", self.server_ip),
            ("broadcast_address", self.broadcast_address),
            ("lease_time", self.lease_time),
            ("renewal_time", self.lease_time // 2),
            ("rebinding_time", int(self.lease_time * 0.875)),
            ("name_server", "8.8.8.8"),
        ]

        if dhcp_type == 1:  # DHCPDISCOVER
            self.log_signal.emit(f"DISCOVER от {mac} — предлагаем {self.assigned_ip}", "discover")
            dhcp = DHCP(options=[("message-type", "offer")] + options_common + ["end"])
        elif dhcp_type == 3:  # DHCPREQUEST
            if requested_ip and requested_ip != self.assigned_ip:
                self.log_signal.emit(f"REQUEST от {mac} — просит {requested_ip}, принудительно NAK", "nak")
                dhcp = DHCP(options=[("message-type", "nak"), ("server_id", self.server_ip), "end"])
            elif not requested_ip and ciaddr and ciaddr != "0.0.0.0" and ciaddr != self.assigned_ip:
                self.log_signal.emit(f"REQUEST от {mac} — ciaddr {ciaddr}, принудительно NAK", "nak")
                dhcp = DHCP(options=[("message-type", "nak"), ("server_id", self.server_ip), "end"])
            else:
                self.log_signal.emit(f"REQUEST от {mac} — подтверждаем {self.assigned_ip}", "ack")
                dhcp = DHCP(options=[("message-type", "ack")] + options_common + ["end"])
        else:
            return
        
        sendp(ether / ip / udp / bootp / dhcp, iface=self.interface, verbose=0)


class DhcpGuiApp(QMainWindow):
    CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dhcp_config.json")

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Панель управления DHCP-сервером")
        self.resize(700, 650)
        self.setMinimumSize(650, 580)
        self.worker = None
        self.thread = None
        self.setup_ui()
        self.apply_styles()
        self.load_config()
        self.refresh_interfaces()

    def setup_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        settings_group = QGroupBox("Настройки конфигурации", self)
        settings_layout = QGridLayout()
        settings_layout.setContentsMargins(15, 20, 15, 15)
        settings_layout.setSpacing(12)
        settings_group.setLayout(settings_layout)

        iface_group = QGroupBox("Сетевой интерфейс", self)
        iface_layout = QHBoxLayout(iface_group)
        iface_layout.setContentsMargins(8, 14, 8, 8)
        iface_layout.setSpacing(8)

        self.combo_iface = QComboBox(self)
        iface_layout.addWidget(self.combo_iface, 1)

        self.btn_refresh = QPushButton(self)
        self.btn_refresh.setObjectName("SecondaryButton")
        self.btn_refresh.setIcon(qta.icon("fa5s.sync-alt", color="#11111b"))
        self.btn_refresh.setIconSize(QSize(16, 16))
        self.btn_refresh.setToolTip("Обновить список интерфейсов")
        self.btn_refresh.clicked.connect(self.refresh_interfaces)
        iface_layout.addWidget(self.btn_refresh)

        settings_layout.addWidget(iface_group, 0, 0, 1, 4)

        def make_field_box(title, default_value, parent):
            """Создаёт компактный QGroupBox с QLineEdit внутри."""
            box = QGroupBox(title, parent)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(8, 14, 8, 6)
            box_layout.setSpacing(0)
            edit = QLineEdit(default_value, box)
            box_layout.addWidget(edit)
            return box, edit

        gb_server_ip, self.entry_server_ip = make_field_box("IP сервера", "15.15.15.1", self)
        settings_layout.addWidget(gb_server_ip, 1, 0, 1, 2)

        gb_assigned_ip, self.entry_assigned_ip = make_field_box("IP клиенту", "15.15.15.15", self)
        settings_layout.addWidget(gb_assigned_ip, 1, 2, 1, 2)

        gb_mask, self.entry_mask = make_field_box("Маска подсети", "255.255.255.0", self)
        settings_layout.addWidget(gb_mask, 2, 0, 1, 2)

        gb_lease, self.entry_lease = make_field_box("Время аренды (сек)", "60", self)
        settings_layout.addWidget(gb_lease, 2, 2, 1, 2)

        main_layout.addWidget(settings_group)

        control_layout = QHBoxLayout()
        control_layout.setSpacing(15)

        self.btn_start = QPushButton(self)
        self.btn_start.setObjectName("StartButton")
        self.btn_start.setIcon(qta.icon("fa5s.play", color="#11111b"))
        self.btn_start.setIconSize(QSize(16, 16))
        self.btn_start.setToolTip("Запустить DHCP сервер")
        self.btn_start.clicked.connect(self.start_server)
        control_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton(self)
        self.btn_stop.setObjectName("StopButton")
        self.btn_stop.setIcon(qta.icon("fa5s.stop", color="#11111b"))
        self.btn_stop.setIconSize(QSize(16, 16))
        self.btn_stop.setEnabled(False)
        self.btn_stop.setToolTip("Остановить DHCP сервер")
        self.btn_stop.clicked.connect(self.stop_server)
        control_layout.addWidget(self.btn_stop)

        self.btn_clear_log = QPushButton(self)
        self.btn_clear_log.setObjectName("SecondaryButton")
        self.btn_clear_log.setIcon(qta.icon("fa5s.trash-alt", color="#11111b"))
        self.btn_clear_log.setIconSize(QSize(14, 14))
        self.btn_clear_log.setToolTip("Очистить лог")
        self.btn_clear_log.clicked.connect(self.clear_log)

        control_layout.addStretch()
        control_layout.addWidget(self.btn_clear_log)

        main_layout.addLayout(control_layout)

        self.log_edit = QTextEdit(self)
        self.log_edit.setObjectName("LogPanel")
        self.log_edit.setReadOnly(True)
        main_layout.addWidget(self.log_edit)

    def apply_styles(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        qss_path = os.path.join(script_dir, "style.qss")
        if os.path.exists(qss_path):
            try:
                with open(qss_path, "r", encoding="utf-8") as f:
                    self.setStyleSheet(f.read())
            except Exception as e:
                print(f"Ошибка загрузки стилей: {e}")
        else:
            self.setStyleSheet("QMainWindow { background-color: #1e1e2e; }")

    def refresh_interfaces(self):
        try:
            interfaces = get_if_list()
            interfaces = [iface for iface in interfaces if iface.strip()]
            # Фильтруем системные интерфейсы
            interfaces = [iface for iface in interfaces if not self.is_system_interface(iface)]
            self.combo_iface.clear()
            self.combo_iface.addItems(interfaces)
            if interfaces:
                self.combo_iface.setCurrentIndex(0)
            self.append_log("Список интерфейсов обновлен.", "info")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось получить список интерфейсов: {e}")

    def load_config(self):
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                self.entry_server_ip.setText(config.get("server_ip", "15.15.15.1"))
                self.entry_assigned_ip.setText(config.get("assigned_ip", "15.15.15.15"))
                self.entry_mask.setText(config.get("subnet_mask", "255.255.255.0"))
                self.entry_lease.setText(str(config.get("lease_time", 60)))
                iface = config.get("interface")
                if iface:
                    index = self.combo_iface.findText(iface)
                    if index >= 0:
                        self.combo_iface.setCurrentIndex(index)
                self.append_log("Конфигурация загружена.", "info")
        except Exception as e:
            self.append_log(f"Ошибка загрузки конфигурации: {e}", "error")

    def save_config(self):
        try:
            config = {
                "interface": self.combo_iface.currentText(),
                "server_ip": self.entry_server_ip.text().strip(),
                "assigned_ip": self.entry_assigned_ip.text().strip(),
                "subnet_mask": self.entry_mask.text().strip(),
                "lease_time": int(self.entry_lease.text().strip())
            }
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.append_log(f"Ошибка сохранения конфигурации: {e}", "error")

    @Slot(str, str)
    def append_log(self, text, tag):
        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        color_map = {
            "info": "#89b4fa",      # синий
            "discover": "#f9e2af",  # желтый
            "ack": "#a6e3a1",       # зеленый
            "nak": "#f38ba8",       # красный
            "error": "#f38ba8"      # красный
        }
        color = color_map.get(tag, "#cdd6f4")
        bold_style = "font-weight: bold;" if tag in ["error", "nak"] else ""
        html_msg = f"<span style='color: #a6adc8;'>[{timestamp}]</span> <span style='color: {color}; {bold_style}'>{text}</span><br>"
        self.log_edit.insertHtml(html_msg)
        self.log_edit.ensureCursorVisible()

    def clear_log(self):
        self.log_edit.clear()

    def is_system_interface(self, iface):
        """Проверяет, является ли интерфейс системным (имеет default route, IP, входит в бридж, или WiFi)."""
        # WiFi интерфейсы обычно начинаются с wl* (wlan, wlp, wlo, wlx, ...)
        if iface.startswith("wl"):
            return True
        try:
            # Проверяем наличие default route на этом интерфейсе
            result = subprocess.run(["ip", "route", "show", "default", "dev", iface], 
                                    capture_output=True, text=True, check=False)
            if result.stdout.strip():
                return True
            # Проверяем, есть ли на интерфейсе IPv4 адрес (кроме link-local)
            result = subprocess.run(["ip", "-4", "addr", "show", "dev", iface], 
                                    capture_output=True, text=True, check=False)
            for line in result.stdout.splitlines():
                if "inet " in line and not line.strip().startswith("inet 169.254."):
                    # Есть IPv4 адрес - возможно системный интерфейс
                    return True
            # Проверяем, является ли интерфейс частью бриджа (slave или master)
            result = subprocess.run(["ip", "link", "show", "dev", iface], 
                                    capture_output=True, text=True, check=False)
            output = result.stdout
            if "master " in output or output.strip().startswith(f"{iface}:"):
                # Проверяем master (slave бриджа) или сам бридж
                if "master " in output:
                    return True  # слейв бриджа
                # Проверим, не является ли сам интерфейс бриджем
                if "master" not in output and "bridge" in output.lower():
                    return True
        except Exception:
            pass
        return False

    def validate_inputs(self):
        iface = self.combo_iface.currentText()
        if not iface:
            QMessageBox.warning(self, "Ошибка валидации", "Не выбран сетевой интерфейс!")
            return None
        if self.is_system_interface(iface):
            reply = QMessageBox.warning(self, "⚠ Системный интерфейс",
                f"Интерфейс <b>{iface}</b> используется для подключения к интернету/локальной сети.\n"
                f"Запуск DHCP-сервера на нём может прервать ваше сетевое соединение.\n\n"
                f"Продолжить?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return None
        server_ip = self.entry_server_ip.text().strip()
        assigned_ip = self.entry_assigned_ip.text().strip()
        mask = self.entry_mask.text().strip()
        lease_str = self.entry_lease.text().strip()
        try:
            ipaddress.IPv4Address(server_ip)
        except ValueError:
            QMessageBox.warning(self, "Ошибка валидации", f"Некорректный IP-адрес сервера: {server_ip}")
            return None
        try:
            ipaddress.IPv4Address(assigned_ip)
        except ValueError:
            QMessageBox.warning(self, "Ошибка валидации", f"Некорректный IP-адрес клиента: {assigned_ip}")
            return None
        try:
            ipaddress.IPv4Address(mask)
        except ValueError:
            if not (mask.startswith("/") and mask[1:].isdigit() and 0 <= int(mask[1:]) <= 32):
                QMessageBox.warning(self, "Ошибка валидации", f"Некорректная маска сети: {mask}. Используйте формат вида 255.255.255.0")
                return None
        try:
            lease = int(lease_str)
            if lease <= 0:
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Ошибка валидации", "Время аренды должно быть целым положительным числом!")
            return None
        return iface, server_ip, assigned_ip, mask, lease

    def start_server(self):
        inputs = self.validate_inputs()
        if not inputs:
            return
        iface, server_ip, assigned_ip, mask, lease = inputs
        self.save_config()
        self.set_widgets_state(False)
        self.thread = QThread()
        self.worker = DhcpWorker(iface, server_ip, assigned_ip, mask, lease)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run_server)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.on_server_thread_stopped)
        self.thread.start()
        QThread.msleep(300)
        if self.worker and self.worker.running:
            self.btn_stop.setEnabled(True)
        else:
            self.on_server_thread_stopped()

    def stop_server(self):
        if self.worker:
            self.btn_stop.setEnabled(False)
            self.worker.running = False

    def on_server_thread_stopped(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait()
        self.worker = None
        self.thread = None
        self.set_widgets_state(True)
        self.btn_stop.setEnabled(False)

    def set_widgets_state(self, enabled):
        self.btn_start.setEnabled(enabled)
        self.btn_refresh.setEnabled(enabled)
        self.combo_iface.setEnabled(enabled)
        self.entry_server_ip.setEnabled(enabled)
        self.entry_assigned_ip.setEnabled(enabled)
        self.entry_mask.setEnabled(enabled)
        self.entry_lease.setEnabled(enabled)

    def closeEvent(self, event):
        if self.worker:
            self.stop_server()
            if self.thread:
                self.thread.wait(2000)
        event.accept()

def main():
    app = QApplication(sys.argv)
    app.setDesktopFileName("dhcp_server_gui")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    desktop_file = os.path.join(script_dir, "dhcp_server_gui.desktop")
    if not os.path.exists(desktop_file):
        app.setWindowIcon(QIcon(os.path.join(script_dir, "dhcp_2.svg")))
    window = DhcpGuiApp()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
