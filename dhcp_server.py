#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DHCP-сервер для MacFlashUtility.

Использует scapy для перехвата и ответа на DHCP-пакеты,
и pyroute2 для настройки сетевых интерфейсов (без вызова ip-команд).

Работает в отдельном threading.Thread (не QThread) — нет зависимостей от Qt,
поэтому не вызывает segfault при уничтожении объектов.
"""

import time
import threading
import ipaddress
try:
    from pyroute2 import IPRoute
except ImportError:
    IPRoute = None

try:
    from scapy.all import sendp, get_if_hwaddr, get_if_list
    from scapy.sendrecv import AsyncSniffer
    from scapy.layers.l2 import Ether
    from scapy.layers.inet import IP, UDP
    from scapy.layers.dhcp import DHCP, BOOTP
except ImportError:
    sendp = get_if_hwaddr = get_if_list = AsyncSniffer = None
    Ether = IP = UDP = DHCP = BOOTP = None


class DhcpServer:
    """
    Мини-DHCP сервер. Отвечает на DHCPDISCOVER (offer) и DHCPREQUEST (ack/nak),
    выдавая клиенту фиксированный assigned_ip.

    Параметры:
        interface      - сетевой интерфейс для прослушивания
        server_ip      - IP адрес сервера (настраивается на интерфейсе)
        assigned_ip    - IP который выдаётся клиенту
        subnet_mask    - маска подсети
        lease_time     - время аренды в секундах
        log_callback   - callable(text, tag) для логов
        lease_callback - callable(mac, ip, event) при событиях аренды
    """

    # Глобальный потокобезопасный реестр аренд для доступа из DPC и GUI
    _lock = threading.Lock()
    _leases = {}          # {ip: mac}
    _lease_history = []   # [{"mac": mac, "ip": ip, "event": event, "time": float}]
    _running_instances = set()

    def __init__(self, interface, server_ip, assigned_ip, subnet_mask, lease_time,
                 log_callback=None, lease_callback=None, debug: bool = False):
        self.interface = interface
        self.server_ip = server_ip
        self.assigned_ip = assigned_ip
        self.subnet_mask = subnet_mask
        self.lease_time = int(lease_time)
        self.log_callback = log_callback or (lambda *_: None)
        self.lease_callback = lease_callback or (lambda *_: None)
        self.debug = debug
        self.running = False
        # Вычисляем broadcast_address и prefixlen при инициализации
        import ipaddress
        network = ipaddress.IPv4Network(f"{server_ip}/{subnet_mask}", strict=False)
        self.broadcast_address = str(network.broadcast_address)
        self._prefixlen_val = network.prefixlen
        self._ipr = None
        self._thread = None
        self._stop_event = threading.Event()
        self._sniffer = None

    def _prefixlen(self):
        return self._prefixlen_val

    # ------------------------------------------------------------------ #
    # Публичный API
    # ------------------------------------------------------------------ #
    def start(self):
        """Запустить сервер в отдельном daemon-потоке."""
        if self.running:
            return True
        self.running = True
        self._stop_event.clear()
        with DhcpServer._lock:
            DhcpServer._running_instances.add(self)
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"DHCP-{self.interface}")
        self._thread.start()
        return True

    def stop(self):
        """Остановить сервер. Поток завершится сам по daemon-флагу при выходе процесса."""
        self.running = False
        self._stop_event.set()
        if self._sniffer is not None:
            try:
                self._sniffer.stop()
            except Exception:
                pass
        # Не ждём поток — он daemon, умрёт с процессом. Ждать может привести к deadlock.

    # ------------------------------------------------------------------ #
    # Методы запроса данных аренды (для DPC и GUI)
    # ------------------------------------------------------------------ #
    @classmethod
    def is_server_running(cls) -> bool:
        """Проверить, работает ли хотя бы один экземпляр DHCP-сервера."""
        with cls._lock:
            return len(cls._running_instances) > 0

    @classmethod
    def get_mac_for_ip(cls, ip: str) -> str | None:
        """Получить актуальный MAC-адрес по IP из таблицы аренд DHCP."""
        with cls._lock:
            return cls._leases.get(ip)

    @classmethod
    def get_last_mac(cls) -> str | None:
        """Получить последний зафиксированный DHCP-сервером MAC-адрес."""
        with cls._lock:
            if cls._lease_history:
                return cls._lease_history[-1]["mac"]
            return None

    @classmethod
    def get_last_lease(cls) -> dict | None:
        """Получить полную запись о последнем событии аренды."""
        with cls._lock:
            if cls._lease_history:
                return dict(cls._lease_history[-1])
            return None

    @classmethod
    def get_lease_since(cls, since_timestamp: float, target_ip: str = None) -> dict | None:
        """
        Возвращает самое свежее событие аренды, произошедшее после since_timestamp.
        Если указан target_ip, фильтрует только по нему.
        """
        with cls._lock:
            for entry in reversed(cls._lease_history):
                if entry["time"] >= since_timestamp:
                    if target_ip is None or entry["ip"] == target_ip:
                        return dict(entry)
            return None

    @classmethod
    def wait_for_lease(cls, target_ip: str = None, since_timestamp: float = None, timeout: float = 60.0) -> dict | None:
        """
        Блокирующее ожидание события аренды DHCP (например, при ребуте устройства).
        Возвращает запись аренды {"mac": ..., "ip": ..., "event": ..., "time": ...} или None по таймауту.
        """
        start_wait = time.time()
        cutoff = since_timestamp if since_timestamp is not None else start_wait

        with cls._lock:
            while True:
                match = cls.get_lease_since(cutoff, target_ip=target_ip)
                if match:
                    return match
                elapsed = time.time() - start_wait
                remaining = timeout - elapsed
                if remaining <= 0:
                    return None
                cls._lock.release()
                time.sleep(min(0.5, remaining))
                cls._lock.acquire()

    @classmethod
    def clear_leases(cls):
        """Очистить историю и кэш аренд."""
        with cls._lock:
            cls._leases.clear()
            cls._lease_history.clear()

    def cleanup_interface(self):
        """Сбросить настройки интерфейса при завершении."""
        try:
            if self._debug_enabled():
                self._log(f"Очистка настроек интерфейса {self.interface}...", "info")
            if self._ipr is None:
                self._ipr = IPRoute()
            idxs = self._ipr.link_lookup(ifname=self.interface)
            if idxs:
                idx = idxs[0]
                try:
                    self._ipr.addr("flush", index=idx)
                except Exception:
                    pass
                try:
                    self._ipr.link("set", index=idx, state="down")
                except Exception:
                    pass
                if self._debug_enabled():
                    self._log("Интерфейс очищен и отключен.", "info")
        except Exception as e:
            self._log(f"Ошибка при очистке интерфейса: {e}", "error")
        finally:
            if self._ipr is not None:
                try:
                    self._ipr.close()
                except Exception:
                    pass
                self._ipr = None

    # ------------------------------------------------------------------ #
    # Внутренняя логика
    # ------------------------------------------------------------------ #
    def _debug_enabled(self):
        return getattr(self, "debug", False)

    def _run(self):
        """Основной цикл сервера (выполняется в daemon-потоке)."""
        self._log(f"Мини-DHCP сервер запущен на {self.interface} -> выдаёт {self.assigned_ip}", "info")
        # Интерфейс должен быть настроен через sudo (ip link set up + ip addr add) перед запуском
        try:
            if self._ipr is None:
                self._ipr = IPRoute()
            idxs = self._ipr.link_lookup(ifname=self.interface)
            if idxs:
                idx = idxs[0]
                try:
                    self._ipr.link("set", index=idx, state="up")
                except Exception:
                    pass
                try:
                    self._ipr.addr("add", index=idx, address=self.server_ip, prefixlen=self._prefixlen())
                except Exception:
                    pass
        except Exception as e:
            self._log(f"Не удалось настроить интерфейс {self.interface}: {e}", "warn")
        try:
            self._sniffer = AsyncSniffer(
                    filter="udp and (port 67 or 68)",
                    iface=self.interface,
                    prn=self.handle_dhcp,
                    store=0,
                )
            self._sniffer.start()
            if self._debug_enabled():
                self._log(f"Sniffer запущен на {self.interface}, жду DHCP-пакеты...", "info")
            # Ждём stop() с таймаутом
            while self.running:
                self._stop_event.wait(timeout=0.5)
        except Exception as e:
            self._log(f"Ошибка запуска sniffer на {self.interface}: {e}", "error")
        finally:
            if self._sniffer is not None:
                try:
                    self._sniffer.stop()
                except Exception:
                    pass
            with DhcpServer._lock:
                DhcpServer._running_instances.discard(self)
        return True

    def _setup_interface(self):
        self._log(f"Настройка интерфейса {self.interface}...", "info")
        self._ipr = IPRoute()
        self._interface_configured = False

        idxs = self._ipr.link_lookup(ifname=self.interface)
        if not idxs:
            raise RuntimeError(f"Интерфейс {self.interface} не найден")
        idx = idxs[0]

        network = ipaddress.IPv4Network(f"{self.server_ip}/{self.subnet_mask}", strict=False)
        cidr = network.prefixlen
        self.broadcast_address = str(network.broadcast_address)

        try:
            self._ipr.addr("flush", index=idx)
        except Exception:
            pass

        try:
            self._ipr.addr("add", index=idx, address=self.server_ip, prefixlen=cidr)
            self._log(f"IP {self.server_ip}/{cidr} добавлен на {self.interface}", "info")
        except Exception as e:
            if "File exists" in str(e) or "файл существует" in str(e).lower():
                self._log(f"IP {self.server_ip}/{cidr} уже на интерфейсе {self.interface}", "info")
                self._interface_configured = True
            elif "Operation not permitted" in str(e) or "операция не разрешена" in str(e).lower():
                self._log(f"Нет прав на добавление IP {self.server_ip}/{cidr} на {self.interface}. Настройте вручную: sudo ip addr add {self.server_ip}/{cidr} dev {self.interface}", "error")
                self._interface_configured = False
            else:
                self._log(f"Не удалось добавить IP {self.server_ip}/{cidr}: {e}", "error")
                self._interface_configured = False

        try:
            self._ipr.link("set", index=idx, state="up")
            self._log(f"Интерфейс {self.interface} поднят", "info")
            self._interface_configured = True
        except Exception as e:
            if "Operation not permitted" in str(e) or "операция не разрешена" in str(e).lower():
                self._log(f"Нет прав на поднятие интерфейса {self.interface}. Настройте вручную: sudo ip link set {self.interface} up", "error")
            else:
                self._log(f"Не удалось поднять интерфейс {self.interface}: {e}", "error")

        if self._interface_configured:
            self._log(f"Интерфейс {self.interface} готов. IP: {self.server_ip}/{cidr}", "info")

    def _log(self, text, tag="info"):
        if self.log_callback:
            self.log_callback(text, tag)

    def _emit_lease(self, mac, ip, event):
        if mac:
            mac_clean = str(mac).strip().lower()
            with DhcpServer._lock:
                DhcpServer._leases[ip] = mac_clean
                record = {
                    "mac": mac_clean,
                    "ip": ip,
                    "event": event,
                    "time": time.time(),
                }
                DhcpServer._lease_history.append(record)
                if len(DhcpServer._lease_history) > 200:
                    DhcpServer._lease_history.pop(0)

        if getattr(self, "lease_callback", None):
            try:
                self.lease_callback(mac, ip, event)
            except Exception as e:
                self._log(f"Ошибка в lease_callback: {e}", "error")

    # ------------------------------------------------------------------ #
    # DHCP option helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_options(dhcp_options):
        """Парсит DHCP options в словарь."""
        result = {}
        for opt in dhcp_options:
            if opt == "end":
                break
            if isinstance(opt, tuple):
                result[opt[0]] = opt[1]
        return result

    @staticmethod
    def _get_message_type(options):
        value = options.get("message-type")
        if isinstance(value, bytes):
            value = value[0]
        return value

    # ------------------------------------------------------------------ #
    # Packet handler
    # ------------------------------------------------------------------ #
    def handle_dhcp(self, pkt):
        if DHCP not in pkt or BOOTP not in pkt or Ether not in pkt:
            return

        ether = pkt[Ether]

        # Игнорируем собственные исходящие пакеты
        try:
            my_mac = get_if_hwaddr(self.interface)
        except Exception:
            my_mac = "00:00:00:00:00:00"

        if ether.src.lower() == my_mac.lower():
            return

        bootp = pkt[BOOTP]
        options = self._parse_options(pkt[DHCP].options)

        msg_type = self._get_message_type(options)
        if msg_type is None:
            return

        # Логирование входящего пакета
        msg_names = {1: "DISCOVER", 2: "OFFER", 3: "REQUEST", 4: "DECLINE", 5: "ACK", 6: "NAK", 7: "RELEASE", 8: "INFORM"}
        msg_name = msg_names.get(msg_type, f"TYPE-{msg_type}")

        requested = options.get("requested_addr")
        hostname = options.get("hostname")
        server_id = options.get("server_id")
        ciaddr = str(bootp.ciaddr)
        mac = ether.src
        xid = bootp.xid

        if self._debug_enabled():
            extra = []
            if requested:
                extra.append(f"requested={requested}")
            if hostname:
                extra.append(f"host={hostname}")
            if server_id:
                extra.append(f"server_id={server_id}")
            self._log(f"RX {msg_name:8s} | MAC={mac:17s} | XID={xid:08x} | ciaddr={ciaddr:15s}" + (" | " + " | ".join(extra) if extra else ""), "debug")

        # Не отвечаем на REQUEST, адресованный другому DHCP-серверу
        if msg_type == 3 and server_id and server_id != self.server_ip:
            if self._debug_enabled():
                self._log(f"    [SKIP] REQUEST предназначен серверу {server_id}", "debug")
            return

        # Формируем ответ
        chaddr = bootp.chaddr
        yiaddr = "0.0.0.0"
        reply_type = None
        dhcp_options_list = None

        if msg_type == 1:  # DHCPDISCOVER
            reply_type = 2  # OFFER
            yiaddr = self.assigned_ip
            dhcp_options_list = [
                ("message-type", "offer"),
                ("server_id", self.server_ip),
                ("lease_time", self.lease_time),
                ("subnet_mask", self.subnet_mask),
                ("router", self.server_ip),
                ("broadcast_address", self.broadcast_address),
                ("renewal_time", self.lease_time // 2),
                ("rebinding_time", int(self.lease_time * 0.875)),
                ("name_server", "8.8.8.8"),
                "end",
            ]
            self._emit_lease(mac, self.assigned_ip, "discover")

        elif msg_type == 3:  # DHCPREQUEST
            if requested and requested != self.assigned_ip:
                reply_type = 6  # NAK
                yiaddr = "0.0.0.0"
                dhcp_options_list = [
                    ("message-type", "nak"),
                    ("server_id", self.server_ip),
                    "end",
                ]
                self._emit_lease(mac, requested, "nak")
            elif not requested and ciaddr != "0.0.0.0" and ciaddr != self.assigned_ip:
                reply_type = 6  # NAK
                yiaddr = "0.0.0.0"
                dhcp_options_list = [
                    ("message-type", "nak"),
                    ("server_id", self.server_ip),
                    "end",
                ]
                self._emit_lease(mac, ciaddr, "nak")
            else:
                reply_type = 5  # ACK
                yiaddr = self.assigned_ip
                dhcp_options_list = [
                    ("message-type", "ack"),
                    ("server_id", self.server_ip),
                    ("lease_time", self.lease_time),
                    ("subnet_mask", self.subnet_mask),
                    ("router", self.server_ip),
                    ("broadcast_address", self.broadcast_address),
                    ("renewal_time", self.lease_time // 2),
                    ("rebinding_time", int(self.lease_time * 0.875)),
                    ("name_server", "8.8.8.8"),
                    "end",
                ]
                self._emit_lease(mac, self.assigned_ip, "ack")

        elif msg_type == 8:  # DHCPINFORM
            reply_type = 5  # ACK
            yiaddr = "0.0.0.0"
            dhcp_options_list = [
                ("message-type", "ack"),
                ("server_id", self.server_ip),
                ("subnet_mask", self.subnet_mask),
                ("router", self.server_ip),
                ("broadcast_address", self.broadcast_address),
                ("name_server", "8.8.8.8"),
                "end",
            ]
            # INFORM не создаёт аренду

        else:
            return

        # Отправляем ответ (broadcast)
        reply = (
            Ether(src=my_mac, dst="ff:ff:ff:ff:ff:ff")
            / IP(src=self.server_ip, dst="255.255.255.255")
            / UDP(sport=67, dport=68)
            / BOOTP(
                op=2,
                htype=bootp.htype,
                hlen=bootp.hlen,
                hops=bootp.hops,
                xid=bootp.xid,
                secs=bootp.secs,
                flags=bootp.flags,
                ciaddr=bootp.ciaddr,
                yiaddr=yiaddr,
                siaddr=self.server_ip,
                giaddr=bootp.giaddr,
                chaddr=chaddr
            )
            / DHCP(options=dhcp_options_list)
        )

        try:
            sendp(reply, iface=self.interface, verbose=False)

            if self._debug_enabled():
                reply_name = msg_names.get(reply_type, f"TYPE-{reply_type}")
                extra = []
                extra.append(f"ip={yiaddr}")
                extra.append(f"dst=broadcast")
                self._log(f"TX {reply_name:8s} | MAC={mac:17s} | XID={xid:08x} | ciaddr={ciaddr:15s} | " + " | ".join(extra), "debug")

        except Exception as exc:
            self._log(f"[ERROR] DHCP send failed: {exc}", "error")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def list_interfaces():
        """Вернуть список сетевых интерфейсов."""
        # 1. Через scapy
        if get_if_list is not None:
            try:
                return [i for i in get_if_list() if i and i.strip()]
            except Exception:
                pass
        # 2. Fallback: /proc/net/dev
        try:
            ifaces = []
            with open("/proc/net/dev") as fh:
                for line in fh:
                    line = line.strip()
                    if ":" in line:
                        iface = line.split(":")[0].strip()
                        if iface:
                            ifaces.append(iface)
            return ifaces
        except Exception:
            return []

    @staticmethod
    def is_system_interface(iface):
        """Проверка системного интерфейса (не подходит для DHCP-сервера).

        Отсекаем только: loopback, WiFi, bridge, docker/veth, libvirt.
        Обычные Ethernet-интерфейсы (en*, eth*) доступны для выбора,
        даже если на них уже висит IP — DHCP-сервер поднимет свой.
        """
        if not iface:
            return True
        if iface == "lo":
            return True
        if iface.startswith("wl"):
            return True
        if iface.startswith("br"):
            return True
        if iface.startswith("docker"):
            return True
        if iface.startswith("veth"):
            return True
        if iface.startswith("virbr"):
            return True
        return False
