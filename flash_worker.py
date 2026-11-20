#!/usr/bin/env python3
"""
Flash worker for ProcessPoolExecutor.
Runs in separate process — no GUI imports, only primitives/JSON.
Logs go to multiprocessing.Queue.
"""

import os
import sys
import json
import traceback
import logging
from datetime import datetime
from multiprocessing import Queue

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssh import (
    _connect_ssh_with_retry,
    exec_ssh,
    exec_ssh_stdin,
    read_remote_file_bytes,
    flash_mac_from_spi_asyncssh,
    run_install_script_step,
    find_new_host,
    wait_for_ssh_port,
)
from db import make_eeprom_bytes


def setup_logging(log_queue: Queue):
    """Настройка логирования в очередь для главного процесса."""
    class QueueHandler(logging.Handler):
        def emit(self, record):
            try:
                log_queue.put(self.format(record))
            except Exception:
                pass
    
    handler = QueueHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]


def log(msg: str):
    """Лог в очередь + консоль."""
    logging.info(msg)
    print(msg, flush=True)


def flash_worker(params: dict, log_queue: Queue) -> dict:
    """
    Основная функция прошивки для запуска в отдельном процессе.
    
    params: {
        "lan_mac": str,
        "wan_mac": str,
        "host": str or None,
        "user": str,
        "password": str,
        "disable_shell": bool,
        "install_script": str,
        "nand_command": str,
        "flash_erase": str,
        "mtd0": str,
        "stop_flag": multiprocessing.Event (optional)
    }
    
    Возвращает: {"ok": bool, "msg": str, "data": dict}
    """
    setup_logging(log_queue)
    
    lan_mac = params.get("lan_mac", "")
    wan_mac = params.get("wan_mac", "")
    host = params.get("host")
    user = params.get("user", "root")
    password = params.get("password", "infinityroot")
    disable_shell = params.get("disable_shell", False)
    install_script = params.get("install_script", "install.sh")
    nand_command = params.get("nand_command", "")
    flash_erase = params.get("flash_erase", "")
    mtd0 = params.get("mtd0", "")
    stop_flag = params.get("stop_flag")  # multiprocessing.Event
    
    def check_stop():
        if stop_flag and stop_flag.is_set():
            raise KeyboardInterrupt("Stopped by user")
    
    try:
        log(f"[FLASH] Начинаю прошивку EEPROM: LAN={lan_mac} WAN={wan_mac}")
        check_stop()
        
        eeprom_data = make_eeprom_bytes(lan_mac, wan_mac)
        lan_clean = (lan_mac or "").replace(":", "").lower()
        wan_clean = (wan_mac or "").replace(":", "").lower()
        expected = bytes.fromhex((lan_clean + wan_clean) if lan_clean else (wan_clean + wan_clean))
        log(f"[FLASH] EEPROM сформирован в памяти ({len(eeprom_data)} байт)")
        
        if host:
            log(f"[FLASH] Используем заданный хост: {host}")
        else:
            host = find_new_host(log=log)
        if not host:
            return {"ok": False, "msg": "Устройство не найдено"}
        
        check_stop()
        
        wrapper = _connect_ssh_with_retry(host, user, password, log=log, stop_event=stop_flag)
        log(f"[SSH] Подключено к {host}")
        
        project_root = os.path.dirname(os.path.abspath(__file__))
        if disable_shell:
            log("[STEP] Применяем install.sh...")
            ok, msg = run_install_script_step(wrapper, install_script=install_script, project_root=project_root, log=log)
            if not ok:
                return {"ok": False, "msg": msg}
            check_stop()
        
        write_cmd = nand_command if nand_command.strip().endswith("-") else (nand_command.rstrip() + " -")
        steps = [
            ("Подготовка памяти", flash_erase, None),
            ("Запись EEPROM", write_cmd, eeprom_data),
        ]
        for desc, cmd, data in steps:
            check_stop()
            log(f"[STEP] {desc}")
            if data is None:
                code, stdout, stderr = exec_ssh(wrapper, cmd, timeout=120)
            else:
                code, stdout, stderr = exec_ssh_stdin(wrapper, cmd, data, timeout=120)
            if stdout:
                for ln in stdout.strip().splitlines():
                    log(ln)
            if stderr:
                for ln in stderr.strip().splitlines():
                    log(f"[ERR]: {ln}")
            if code != 0 and "reboot" not in cmd:
                return {"ok": False, "msg": f"Ошибка шага: {desc} (code {code})"}
        
        check_stop()
        
        mtd_dev = mtd0.split()[0] if mtd0 else ""
        if not mtd_dev:
            return {"ok": False, "msg": "Не задан mtd0 в настройках"}
        vcode, vout, verr = exec_ssh(wrapper, f"dd if={mtd_dev} bs=1 count=12 2>/dev/null | od -v -An -tx1", timeout=60)
        readback = (vout or "").split()
        if vcode == 0 and len(readback) >= 12:
            actual = bytes.fromhex("".join(readback[:12]))
            if actual != expected[:12]:
                return {"ok": False, "msg": f"EEPROM mismatch: {actual.hex()} != {expected[:12].hex()}"}
            log("[OK] EEPROM совпадает с ожидаемым MAC")
        else:
            log("[WARN] Не удалось прочитать EEPROM для проверки")
        
        check_stop()
        
        ok, msg = flash_mac_from_spi_asyncssh(wrapper, host, log, mtd0=mtd0)
        if ok and msg is None:
            log("[INFO] MAC уже совпадает (ребут не требуется)")
            return {"ok": True, "msg": "EEPROM прошита (MAC уже совпадает), ожидаем устройство через DHCP"}
        if not ok:
            return {"ok": False, "msg": msg}
        log(f"[OK] {msg}")
        log("[INFO] Пост-проверка через DHCP-сервер (устройство вернётся с новым MAC)")
        return {"ok": True, "msg": "EEPROM прошита, ожидаем устройство через DHCP"}
    
    except KeyboardInterrupt:
        log("[STOP] Прошивка остановлена пользователем")
        return {"ok": False, "msg": "Остановлено пользователем"}
    except Exception as e:
        trace = traceback.format_exc()
        log(f"[EXC] {e}\n{trace}")
        return {"ok": False, "msg": str(e)}
    finally:
        try:
            wrapper.close()
        except Exception:
            pass


# Импортируем SSH функции здесь чтобы не тянуть их в глобальную область при импорте модуля
from ssh import (
    _connect_ssh_with_retry,
    exec_ssh,
    exec_ssh_stdin,
    read_remote_file_bytes,
    flash_mac_from_spi_asyncssh,
    run_install_script_step,
    find_new_host,
    wait_for_ssh_port,
)

if __name__ == "__main__":
    # Тестовый запуск
    import multiprocessing
    q = multiprocessing.Queue()
    params = {
        "lan_mac": "32:b7:68:cd:08:3e",
        "wan_mac": "32:b7:68:cd:08:3e",
        "host": "15.15.15.15",
        "disable_shell": True,
    }
    result = flash_worker(params, q)
    print("Result:", result)