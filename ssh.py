import os, time
import traceback, threading
import json
import paramiko
from paramiko import transport as paramiko_transport
import subprocess
import socket
import re, io
from scp import SCPClient
from db import make_eeprom_bytes

# Алгоритмы, совместимые с dropbear (устройства часто используют старые версии)
_DROPBEAR_KEX = (
    "curve25519-sha256@libssh.org",
    "ecdh-sha2-nistp256",
    "diffie-hellman-group14-sha256",
    "diffie-hellman-group14-sha1",
)
_DROPBEAR_CIPHERS = (
    "aes128-ctr",
    "aes256-ctr",
    "aes128-cbc",
    "aes256-cbc",
)
_DROPBEAR_MACS = (
    "hmac-sha2-256",
    "hmac-sha2-512",
    "hmac-sha1",
)


def _make_ssh_client():
    """Создаёт SSHClient с настройками для dropbear."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # Сохраняем оригинальные предпочтения и ставим свои на время коннекта
    orig_pref_kex = paramiko_transport.Transport._preferred_kex
    orig_pref_ciphers = paramiko_transport.Transport._preferred_ciphers
    orig_pref_macs = paramiko_transport.Transport._preferred_macs
    paramiko_transport.Transport._preferred_kex = _DROPBEAR_KEX
    paramiko_transport.Transport._preferred_ciphers = _DROPBEAR_CIPHERS
    paramiko_transport.Transport._preferred_macs = _DROPBEAR_MACS
    # Хак: оборачиваем connect, чтобы восстановить предпочтения после создания транспорта
    orig_connect = ssh.connect
    def wrapped_connect(*args, **kwargs):
        try:
            return orig_connect(*args, **kwargs)
        finally:
            paramiko_transport.Transport._preferred_kex = orig_pref_kex
            paramiko_transport.Transport._preferred_ciphers = orig_pref_ciphers
            paramiko_transport.Transport._preferred_macs = orig_pref_macs
    ssh.connect = wrapped_connect
    return ssh


_ACTIVE_SSH_LOCK = threading.Lock()
_ACTIVE_SSH_CLIENTS = set()

_SSH_KEYS = ("ssh_user", "ssh_password", "phone_path", "mtd0", "nand_command", "flash_erase")


def load_ssh_config():
    """Читает SSH/команды прошивки из Data/options.json."""
    cfg = {}
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Data", "options.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in _SSH_KEYS:
            v = data.get(k)
            if v not in (None, ""):
                cfg[k] = v
    except Exception:
        pass
    return cfg


def _resolve_cfg(user, password, phone_path=None, nand_command=None, flash_erase=None, mtd0=None):
    """Подставляет недостающие SSH/команды из конфига."""
    cfg = load_ssh_config()
    return (
        user or cfg.get("ssh_user"),
        password or cfg.get("ssh_password"),
        phone_path or cfg.get("phone_path"),
        nand_command or cfg.get("nand_command"),
        flash_erase or cfg.get("flash_erase"),
        mtd0 or cfg.get("mtd0"),
    )

def request_abort_active_ssh():
    """Принудительно закрывает активные SSH-сессии (best effort)."""
    with _ACTIVE_SSH_LOCK:
        clients = list(_ACTIVE_SSH_CLIENTS)
        _ACTIVE_SSH_CLIENTS.clear()
    for client in clients:
        try:
            client.close()
        except Exception:
            pass

def exec_ssh(ssh, cmd, timeout=60):
    """Выполняет SSH-команду и возвращает (code, stdout, stderr)"""
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="ignore")
    err = stderr.read().decode(errors="ignore")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def exec_ssh_stdin(ssh, cmd, data, timeout=60):
    """Выполняет SSH-команду, передавая data в stdin. Возвращает (code, stdout, stderr)."""
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    try:
        stdin.write(data)
        stdin.flush()
    except Exception:
        pass
    stdin.channel.shutdown_write()
    out = stdout.read().decode(errors="ignore")
    err = stderr.read().decode(errors="ignore")
    code = stdout.channel.recv_exit_status()
    return code, out, err

REBOOT_CMD = "echo b > /proc/sysrq-trigger"

def reboot_device(ssh, log=print, timeout=5):
    """Единая перезагрузка устройства через sysrq (без clean shutdown).

    Соединение почти всегда обрывается — это ожидаемо.
    """
    try:
        exec_ssh(ssh, REBOOT_CMD, timeout=timeout)
    except Exception as e:
        log(f"[REBOOT] Соединение прервано (ожидаемо): {e}")

def read_remote_file_bytes(ssh, remote_path, size=None):
    try:
        with ssh.open_sftp() as sftp:
            with sftp.open(remote_path, "rb") as remote_file:
                return remote_file.read(size) if size is not None else remote_file.read()
    except Exception as sftp_err:
        cmd = f"od -v -An -tx1 {remote_path}"
        if size is not None:
            cmd = f"dd if={remote_path} bs=1 count={size} 2>/dev/null | od -v -An -tx1"
        code, out, err = exec_ssh(ssh, cmd, timeout=15)
        if code != 0:
            raise Exception(f"SFTP failed ({sftp_err}) and SSH fallback command failed with code {code}: {err.strip()}")
        hex_data = "".join(out.split())
        if not hex_data:
            raise Exception(f"SFTP failed ({sftp_err}) and SSH fallback command returned empty output")
        try:
            return bytes.fromhex(hex_data)
        except ValueError as e:
            raise Exception(f"SFTP failed ({sftp_err}) and SSH fallback output was not valid hex: {e}")

def format_mac_bytes(raw):
    return ":".join(f"{byte:02x}" for byte in raw)

def parse_fw_printenv_value(output, key):
    """Достает значение key из вывода fw_printenv key."""
    prefix = f"{key}="
    for line in (output or "").splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""

def fw_printenv_needs_restore(output):
    return ("Cannot read environment" in (output or "") or "Cannot read default environment" in (output or ""))

def restore_uboot_env(ssh, log=print, label="ENV", timeout=60):
    log(f"[{label}] Восстанавливаю U-Boot environment...")
    restore_cmd = ("mkenvimage -s 0x4000 -o /tmp/uboot-env.bin /etc/u-boot/u-boot-imx-initial-env-sd && "
        "dd if=/tmp/uboot-env.bin of=/dev/mmcblk2 bs=512 seek=$((0x400000 / 512))")
    code, out, err = exec_ssh(ssh, restore_cmd, timeout=timeout)
    if code != 0:
        return False, f"Ошибка восстановления U-Boot env: {err.strip() or out.strip()}"
    return True, "restored"

def ensure_uboot_env(ssh, log=print, label="ENV", timeout=20):
    code, out, err = exec_ssh(ssh, "fw_printenv 2>&1", timeout=timeout)
    if code != 0 and fw_printenv_needs_restore(out):
        return restore_uboot_env(ssh, log=log, label=label, timeout=60)
    return True, None

def read_eth1addr(ssh, timeout=10):
    code, out, err = exec_ssh(ssh, "fw_printenv eth1addr", timeout=timeout)
    if code != 0:
        return False, "", err.strip() or out.strip()
    return True, parse_fw_printenv_value(out, "eth1addr").lower(), None

def write_eth1addr(ssh, desired, log=print, label="MACSET", timeout=20):
    log(f"[{label}] Устанавливаю eth1addr = {desired}")
    code, out, err = exec_ssh(ssh, f"fw_setenv eth1addr {desired}", timeout=timeout)
    if code != 0:
        log(f"[{label}] fw_setenv вернул {code}, пробую fallback...")
        code, out, err = exec_ssh(ssh,
            f"echo eth1addr={desired} > /tmp/tmpenv.txt && fw_setenv -s /tmp/tmpenv.txt",
            timeout=timeout)
        if code != 0:
            return False, f"Не удалось установить eth1addr: {err.strip() or out.strip()}"
    exec_ssh(ssh, "sync", timeout=5)
    ok, new_val, msg = read_eth1addr(ssh, timeout=timeout)
    if not ok:
        return False, f"Ошибка чтения eth1addr после установки: {msg}"
    if new_val != desired:
        return False, f"После установки получено {new_val}, ожидалось {desired}"
    log(f"[{label}] MAC успешно установлен: {new_val}")
    return True, new_val

def wait_for_ssh_port(host, timeout=30, delay=2, log=print, stop_event=None):
    """Ждём, когда откроется порт SSH (22)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if stop_event is not None and stop_event.is_set():
            log("[STOP] Операция остановлена оператором")
            return False
        try:
            with socket.create_connection((host, 22), timeout=2):
                return True
        except Exception:
            time.sleep(delay)
    log("[ERR] SSH порт не открылся вовремя")
    return False
def _ssh_handshake_ok(host, user, password, timeout=6):
    """Реальная проверка SSH: полный хендшейк+аутентификация, без баннера-гонки."""
    ssh = None
    try:
        ssh = _make_ssh_client()
        ssh.connect(host, username=user, password=password, timeout=timeout,
                    banner_timeout=timeout, auth_timeout=timeout,
                    look_for_keys=False, allow_agent=False)
        return True
    except Exception:
        return False
    finally:
        if ssh is not None:
            try:
                ssh.close()
            except Exception:
                pass


def wait_for_ssh_stable(host, user, password, timeout=90, delay=2, required_ok=3, log=print, stop_event=None):
    """Ждём устойчивого SSH: несколько успешных полных хендшейков подряд."""
    log("[WAIT] Ожидаем стабильный SSH...")
    t0 = time.time()
    ok_count = 0
    while time.time() - t0 < timeout:
        if stop_event is not None and stop_event.is_set():
            log("[STOP] Операция остановлена оператором")
            return False
        if _ssh_handshake_ok(host, user, password):
            ok_count += 1
            if ok_count >= required_ok:
                log("[OK] SSH стабилизировался")
                return True
        else:
            ok_count = 0
        time.sleep(delay)
    log("[ERR] SSH так и не стал стабильным")
    return False


def _connect_ssh_with_retry(host, user, password, log=print, attempts=12, delay=3, stop_event=None):
    """Подключение SSH с ожиданием порта и перезапуском сессии на каждой попытке."""
    last_err = None
    if stop_event is not None and stop_event.is_set():
        raise Exception("Операция остановлена оператором")
    # Сначала ждём, пока откроется TCP-порт 22 (устройство может загружаться дольше, чем отвечает на пинг)
    log("[WAIT] Ожидаем открытие SSH-порта...")
    if not wait_for_ssh_port(host, timeout=60, delay=2, log=lambda *_: None, stop_event=stop_event):
        raise Exception("SSH порт не открылся вовремя")
    # Затем ждём стабильный хендшейк (dropbear может резетить при частых коннектах)
    if not wait_for_ssh_stable(host, user, password, timeout=120, delay=5, required_ok=1, log=log, stop_event=stop_event):
        raise Exception("SSH порт не стабилизировался")
    for attempt in range(1, attempts + 1):
        if stop_event is not None and stop_event.is_set():
            raise Exception("Операция остановлена оператором")
        try:
            ssh = _make_ssh_client()
            ssh.connect(host, username=user, password=password, timeout=10, banner_timeout=12, auth_timeout=12, look_for_keys=False, allow_agent=False)
            with _ACTIVE_SSH_LOCK:
                _ACTIVE_SSH_CLIENTS.add(ssh)
            return ssh
        except Exception as e:
            last_err = e
            msg = str(e)
            log(f"[SSH] Попытка {attempt} не удалась: {msg}")
            if "Error reading SSH protocol banner" in msg or "No existing session" in msg:
                log("[SSH] Похоже, SSH занят или ещё не поднялся. Ожидаем...")
            try:
                ssh.close()
            except Exception:
                pass
            wait_for_ssh_port(host, timeout=30, delay=2, log=lambda *_: None, stop_event=stop_event)
            time.sleep(delay if attempt < 4 else delay + 2)
    raise Exception(last_err)

def flash_mac_from_spi_paramiko(ssh, host, log, mtd0=None):
    """Читает MAC из SPI и устанавливает его в eth1addr"""
    log(f"[SPI] Чтение MAC с {host}")
    remote_mac_path = "/tmp/mac_address"
    mtd_dev = (mtd0 or "").split()[0]
    if not mtd_dev:
        return False, "Не задан mtd0 в настройках"
    code, out, err = exec_ssh(ssh, f"dd if={mtd_dev} of={remote_mac_path} bs=1 count=12", timeout=20)
    if code != 0:
        log(f"[SPI] dd returned {code}, stderr: {err.strip()}")
        return False, f"Ошибка чтения EEPROM (dd): {err.strip() or 'no output'}"
    try:
        raw_mac = read_remote_file_bytes(ssh, remote_mac_path, size=12)
    except Exception as e:
        return False, f"Ошибка чтения {remote_mac_path} по SFTP: {e}"
    if len(raw_mac) < 6:
        return False, f"Некорректный размер MAC-данных: {len(raw_mac)} байт"
    lan_mac = format_mac_bytes(raw_mac[:6])
    wan_mac = format_mac_bytes(raw_mac[6:12]) if len(raw_mac) >= 12 else lan_mac
    log(f"[SPI] LAN MAC: {lan_mac}")
    log(f"[SPI] WAN MAC: {wan_mac}")
    desired_env_mac = wan_mac.lower()
    ok, msg = ensure_uboot_env(ssh, log=log, label="SPI", timeout=20)
    if not ok:
        return False, msg
    ok, current_mac, msg = read_eth1addr(ssh, timeout=10)
    if not ok:
        return False, f"Ошибка чтения eth1addr: {msg}"
    if current_mac == desired_env_mac:
        return True, None
    ok, msg = write_eth1addr(ssh, desired_env_mac, log=log, label="SPI", timeout=20)
    if not ok:
        return False, f"[ERR] Ошибка при установке eth1addr: {msg}"
    reboot_device(ssh, log=log)
    return True, f"[OK] MAC установлен: WAN={wan_mac} LAN={lan_mac}"
    
def find_new_host(log, retries=60, delay=1):
        fixed_ip = "15.15.15.15"
        for attempt in range(retries):
            ping = subprocess.run(["ping", "-c", "1", "-W", "1", fixed_ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ping.returncode == 0: return fixed_ip
            time.sleep(delay)
        log("Устройство не появилось на ожидаемом IP")
        return False

def resolve_install_script_path(install_script="install.sh", project_root=None):
    project_root = project_root or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(project_root, "scripts", install_script)

def run_install_script(ssh_conn, install_script="install.sh", project_root=None, log=print):
    """Передает и выполняет выбранный Data/*.sh по SSH без команд reboot."""
    install_script_local = resolve_install_script_path(install_script, project_root=project_root)
    install_script_name = os.path.basename(install_script_local)
    if not os.path.isfile(install_script_local):
        return False, f"Не найден скрипт: {install_script_local}"
    remote_script = f"/tmp/{install_script_name}"
    try:
        with open(install_script_local, "r", encoding="utf-8", errors="ignore") as f:
            script = f.read()
        reboot_re = re.compile(r"^\s*(?:systemctl\s+reboot|/sbin/reboot|reboot|echo\s+b\s*>\s*/proc/sysrq-trigger)\b.*$", re.MULTILINE) # Убираем очевидные команды перезагрузки.
        script = reboot_re.sub("", script)
        with SCPClient(ssh_conn.get_transport()) as scp:
            scp.putfo(io.StringIO(script), remote_script)
        cmd = f"chmod +x {remote_script} && bash {remote_script}"
        code, out, err = exec_ssh(ssh_conn, cmd, timeout=240)
        if out:
            for line in out.strip().splitlines():
                log(line)
        if err:
            for line in err.strip().splitlines():
                log(f"[ERR]: {line}")
        if code != 0:
            return False, f"{install_script_name} завершился с кодом {code}"
        return True, f"{install_script_name} выполнен"
    except Exception as e:
        return False, str(e)

def run_install_script_step(ssh_conn, install_script=None, project_root=None, log=print):
    install_script_local = resolve_install_script_path(install_script, project_root=project_root)
    install_script_name = os.path.basename(install_script_local) or "install.sh"
    desc = f"Применение {install_script_name} (без reboot)"
    log(f"[STEP] {desc}")
    ok, msg = run_install_script(ssh_conn, install_script=install_script, project_root=project_root, log=log)
    if not ok:
        return False, f"Ошибка шага: {desc} ({msg})"
    return True, msg

def eeprom_flash(lan_mac, wan_mac, log, user=None, password=None, phone_path=None, nand_command=None, flash_erase=None, mtd0=None, disable_shell=False, install_script=None, stop_event=None, host=None):
    """Прошивка EEPROM: генерация файла, SCP, запись в mtd0, установка MAC.

    Проверка результата выполняется в GUI через DHCP-сервер.
    """
    user, password, phone_path, nand_command, flash_erase, mtd0 = _resolve_cfg(
        user, password, phone_path, nand_command, flash_erase, mtd0)
    log("[FLASH] Начинаю прошивку EEPROM")
    if host:
        log(f"[FLASH] Используем заданный хост: {host}")
    else:
        host = find_new_host(log=log)
    if not host:
        return {"ok": False, "msg": "Устройство не найдено"}

    ssh = None
    try:
        if stop_event is not None and stop_event.is_set():
            return {"ok": False, "msg": "Остановлено оператором"}
        eeprom_data = make_eeprom_bytes(lan_mac, wan_mac)
        lan_clean = (lan_mac or "").replace(":", "").lower()
        wan_clean = (wan_mac or "").replace(":", "").lower()
        expected = bytes.fromhex((lan_clean + wan_clean) if lan_clean else (wan_clean + wan_clean))
        log(f"[FLASH] EEPROM сформирован в памяти ({len(eeprom_data)} байт)")

        ssh = _connect_ssh_with_retry(host, user, password, log=log, attempts=12, delay=3, stop_event=stop_event)
        log(f"[SSH] Подключено к {host}")

        project_root = os.path.dirname(os.path.abspath(__file__))
        if disable_shell:
            ok, msg = run_install_script_step(ssh, install_script=install_script, project_root=project_root, log=log)
            if not ok:
                return {"ok": False, "msg": msg}
            if stop_event is not None and stop_event.is_set():
                return {"ok": False, "msg": "Остановлено оператором"}

        write_cmd = nand_command if nand_command.strip().endswith("-") else (nand_command.rstrip() + " -")
        steps = [
            ("Подготовка памяти", flash_erase, None),
            ("Запись EEPROM", write_cmd, eeprom_data),
        ]
        for desc, cmd, data in steps:
            if stop_event is not None and stop_event.is_set():
                return {"ok": False, "msg": "Остановлено оператором"}
            log(f"[STEP] {desc}")
            if data is None:
                code, stdout, stderr = exec_ssh(ssh, cmd, timeout=120)
            else:
                code, stdout, stderr = exec_ssh_stdin(ssh, cmd, data, timeout=120)
            if stdout:
                for ln in stdout.strip().splitlines():
                    log(ln)
            if stderr:
                for ln in stderr.strip().splitlines():
                    log(f"[ERR]: {ln}")
            if code != 0 and "reboot" not in cmd:
                return {"ok": False, "msg": f"Ошибка шага: {desc} (code {code})"}

        # Проверка: читаем первые 12 байт и сравниваем с ожидаемым MAC.
        mtd_dev = mtd0.split()[0] if mtd0 else ""
        if not mtd_dev:
            return {"ok": False, "msg": "Не задан mtd0 в настройках"}
        vcode, vout, verr = exec_ssh(ssh, f"dd if={mtd_dev} bs=1 count=12 2>/dev/null | od -v -An -tx1", timeout=60)
        readback = (vout or "").split()
        if vcode == 0 and len(readback) >= 12:
            actual = bytes.fromhex("".join(readback[:12]))
            if actual != expected[:12]:
                return {"ok": False, "msg": f"EEPROM mismatch: {actual.hex()} != {expected[:12].hex()}"}
            log("[OK] EEPROM совпадает с ожидаемым MAC")
        else:
            log("[WARN] Не удалось прочитать EEPROM для проверки")

        ok, msg = flash_mac_from_spi_paramiko(ssh, host, log, mtd0=mtd0)
        if ok and msg is None:
            log("[INFO] MAC уже совпадает (ребут не требуется)")
            return {"ok": True, "msg": "EEPROM прошита (MAC уже совпадает), ожидаем устройство через DHCP"}
        if not ok:
            return {"ok": False, "msg": msg}
        log(f"[OK] {msg}")
        log("[INFO] Пост-проверка через DHCP-сервер (устройство вернётся с новым MAC)")
        return {"ok": True, "msg": "EEPROM прошита, ожидаем устройство через DHCP"}
    except Exception as e:
        trace = traceback.format_exc()
        log(f"[EXC] {e}\n{trace}")
        return {"ok": False, "msg": str(e)}
    finally:
        if ssh:
            try:
                ssh.close()
            except Exception:
                pass
            with _ACTIVE_SSH_LOCK:
                _ACTIVE_SSH_CLIENTS.discard(ssh)

def _normalize_mac(mac: str) -> str:
    if not mac:
        return ""
    m = mac.strip().lower()
    m = m.replace("-", ":").replace(".", ":")
    if ":" not in m and len(m) == 12:
        m = ":".join(m[i:i+2] for i in range(0, 12, 2))
    parts = [p for p in m.split(":") if p]
    if len(parts) < 6:
        return m
    return ":".join(p.zfill(2) for p in parts[:6])

def set_eth1addr_via_ssh(ssh, wan_mac: str, reboot: bool = True, log=print, timeout=20):
    """Устанавливает eth1addr в U-Boot environment через SSH. Автоматически восстанавливает окружение"""
    try:
        wan = _normalize_mac(wan_mac)
        if not wan:
            return False, "Некорректный MAC-адрес"
        desired = wan.lower()
        log(f"[MACSET] Проверяю U-Boot environment...")
        ok, msg = ensure_uboot_env(ssh, log=log, label="MACSET", timeout=timeout)
        if not ok:
            return False, msg
        if msg == "restored":
            log("[MACSET] Восстановление env успешно.")
        ok, current, msg = read_eth1addr(ssh, timeout=timeout)
        if not ok:
            return False, f"Ошибка чтения eth1addr: {msg}"
        if current == desired:
            log(f"[MACSET] eth1addr уже установлен: {current}")
            return True, f"MAC уже установлен: {current}"
        ok, new_val = write_eth1addr(ssh, desired, log=log, label="MACSET", timeout=timeout)
        if not ok:
            return False, new_val
        if reboot:
            log("[MACSET] Перезагрузка устройства...")
            reboot_device(ssh, log=log)
        return True, f"[OK] MAC установлен: {new_val}"
    except Exception as e:
        trace = traceback.format_exc()
        log(f"[MACSET][EXC] {e}\n{trace}")
        return False, str(e)

def flash_mac_direct(wan_mac, log=print, user=None, password=None, reboot=True, connect_timeout=10, retry=1, disable_shell=False, install_script=None, stop_event=None, host=None):
    """Подключается по SSH, прошивает MAC, ждёт перезагрузку и проверяет результат."""
    user, password, _pp, _nc, _fe, _mtd = _resolve_cfg(user, password)
    if stop_event is not None and stop_event.is_set():
        return {"ok": False, "msg": "Остановлено оператором"}
    if host:
        log(f"[MAC-DIRECT] Используем заданный хост: {host}")
    else:
        host = find_new_host(log=log)
    if not host:
        log("[ERR] Не удалось определить адрес устройства")
        return {"ok": False, "msg": "устройство не найдено"}
    ssh = None
    try:
        if stop_event is not None and stop_event.is_set():
            return {"ok": False, "msg": "Остановлено оператором"}
        # Ждём, пока устройство полностью загрузится и sshd начнёт отвечать.
        # DHCP-lease появляется раньше, чем поднимается SSH, поэтому одного
        # ssh.connect() недостаточно (после реконнекта/ребута он падает).
        ssh = _connect_ssh_with_retry(host, user, password, log=log, attempts=12, delay=3, stop_event=stop_event)
        log(f"[MAC-DIRECT] Подключение к {host} успешно")
        if disable_shell:
            ok, msg = run_install_script_step(ssh, install_script=install_script, log=log)
            if not ok:
                return {"ok": False, "msg": msg}
            if stop_event is not None and stop_event.is_set():
                return {"ok": False, "msg": "Остановлено оператором"}
        ok, msg = set_eth1addr_via_ssh(ssh, wan_mac=wan_mac, reboot=reboot, log=log)
        if not ok:
            log(f"[ERR] Ошибка установки MAC: {msg}")
            return {"ok": False, "msg": msg}
    except Exception as e:
        trace = traceback.format_exc()
        log(f"[ERR] Ошибка SSH или прошивки: {e}\n{trace}")
        return {"ok": False, "msg": str(e)}
    finally:
        if ssh:
            try:
                ssh.close()
            except Exception as e:
                log(f"[WARN] Ошибка при закрытии SSH: {e}")
            with _ACTIVE_SSH_LOCK:
                _ACTIVE_SSH_CLIENTS.discard(ssh)
    log("[INFO] Пост-проверка через DHCP-сервер (устройство вернётся с новым MAC)")
    return {"ok": True, "msg": f"MAC прошит, ожидаем устройство через DHCP > {wan_mac}"}
