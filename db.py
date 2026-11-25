import re
import sys
import os
import json
import psycopg2
from psycopg2 import Error
from datetime import datetime

if getattr(sys, 'frozen', False):
    script_dir = sys._MEIPASS
else:
    script_dir = os.path.dirname(os.path.abspath(__file__))

_COUNTER_DEFAULT = os.path.join(script_dir, "Data", "board_counter.txt")


def _load_options():
    path = os.path.join(script_dir, "Data", "options.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_db_config():
    """Параметры подключения к PostgreSQL из Data/options.json."""
    opts = _load_options()
    return {
        "user": opts.get("db_user", ""),
        "password": opts.get("db_password", ""),
        "host": opts.get("db_host", ""),
        "port": int(opts.get("db_port") or 5432),
        "database": opts.get("db_name", ""),
    }


def get_counter_file():
    """Путь к журналу board-номеров из Data/options.json."""
    v = _load_options().get("counter_file")
    if v in (None, ""):
        v = _COUNTER_DEFAULT
    if not os.path.isabs(v):
        v = os.path.join(script_dir, v)
    return v


def make_eeprom_bytes(lan_mac: str, wan_mac: str) -> bytes:
    """Собирает MAC (lan+wan или wan+wan) в байтах, без файлов.
    Всегда 12 байт: если LAN пуст — дублируем WAN.
    """
    lan_clean = (lan_mac or "").replace(":", "").lower()
    wan_clean = (wan_mac or "").replace(":", "").lower()
    return bytes.fromhex((lan_clean + wan_clean) if lan_clean else (wan_clean + wan_clean))


def check_serial(param: str, debug: bool = False):
    """Проверяет S/N и возвращает статус, MAC-адреса и тип устройства (single/dual)"""
    result = {"status": None, "serial": param, "macs": [], "type": None}
    param = re.sub('S/N:', '', param)
    param = re.sub(r'\s+', '', param)
    if re.findall('[^a-zA-Z0-9_]', param):
        result["status"] = "NO_LATIN"
        return result
    try:
        connection = psycopg2.connect(**get_db_config())
        cursor = connection.cursor()
        query = """
            SELECT mac, serial, "postChecked"
            FROM public.mac
            WHERE serial = %s
            ORDER BY mac ASC;
        """
        cursor.execute(query, (param,))
        records = cursor.fetchall()
        if debug:
            print(f"[DEBUG] Records for {param}: {records}")
        if not records:
            result["status"] = "SERIALNO_IS_FREE"
        else:
            result["macs"] = [r[0] for r in records]
            mac_count = len(result["macs"])
            result["type"] = "dual" if mac_count == 2 else "single"
            if not records[0][2]:
                result["status"] = "SERIALNO_IS_NOT_FINISHED"
            else:
                result["status"] = "SERIALNO_IS_BUSY"
    except (Exception, Error) as error:
        result["status"] = "DB_ERROR"
        result["error"] = str(error)
    finally:
        if 'connection' in locals() and connection:
            cursor.close()
            connection.close()
    return result

def update_mac_single(serial: str, wanMac: str, author: str,
                      board: str = None, postcheck: bool = False, debug: bool = False):
    """Обновление для новых устройств (одна строка, без device)"""
    serial = re.sub('S/N:', '', serial).replace(' ', '')
    now = datetime.now()
    date_str, time_str = now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")
    try:
        conn = psycopg2.connect(**get_db_config())
        cur = conn.cursor()
        cur.execute("""
            UPDATE public.mac
            SET mac = %s,
                author = %s,
                date = %s,
                time = %s,
                device = 'WAN',
                board = COALESCE(%s, board)
            WHERE serial = %s;
        """, (wanMac, author, date_str, time_str, board, serial))
        conn.commit()
        if postcheck:
            cur.execute("""
                UPDATE public.mac
                SET "postChecked" = TRUE
                WHERE serial = %s;
            """, (serial,))
            conn.commit()
        if debug:
            print(f"[DEBUG] Single MAC updated for {serial}: {wanMac}, board={board}")
        return {"status": "OK", "type": "single", "serial": serial}
    except (Exception, psycopg2.Error) as error:
        return {"status": "DB_ERROR", "error": str(error)}
    finally:
        if 'conn' in locals():
            cur.close()
            conn.close()

def update_mac_dual(serial: str, lanMac: str, wanMac: str, author: str,
                    board: str = None, postcheck: bool = False, debug: bool = False):
    """Обновление для старых устройств (две строки: LAN и WAN)."""
    serial = re.sub(r'S/N:', '', serial).replace(' ', '')
    now = datetime.now()
    date_str, time_str = now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")
    try:
        conn = psycopg2.connect(**get_db_config())
        cur = conn.cursor()
        updates = []
        if lanMac:
            updates.append(("LAN", lanMac))
        if wanMac:
            updates.append(("WAN", wanMac))
        if not updates:
            if debug:
                print(f"[DEBUG] Пропуск обновления — нет MAC для {serial}")
            return {"status": "SKIP", "reason": "no macs"}
        mac_list = tuple(mac for _, mac in updates)
        cur.execute("SELECT mac FROM public.mac WHERE mac IN %s;", (mac_list,))
        existing = {row[0] for row in cur.fetchall()}
        case_parts = " ".join("WHEN %s THEN %s" for _ in updates)
        sql_update = f"""
            UPDATE public.mac
            SET date = %s,
                time = %s,
                author = %s,
                board = COALESCE(%s, board),
                device = CASE mac {case_parts} ELSE device END
            WHERE mac IN %s;
        """
        params = [date_str, time_str, author, board]
        for iface, mac in updates:
            params.extend([mac, iface])
        params.append(mac_list)
        cur.execute(sql_update, tuple(params))
        updated_rows = cur.rowcount
        for device, mac in updates:
            if mac not in existing:
                cur.execute("""
                    INSERT INTO public.mac (serial, mac, device, author, date, time, board)
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                """, (serial, mac, device, author, date_str, time_str, board))
        if postcheck:
            cur.execute("""
                UPDATE public.mac
                SET "postChecked" = TRUE
                WHERE serial = %s;
            """, (serial,))
        conn.commit()
        if debug:
            print(f"[DEBUG] update_mac_dual({serial}) → обновлено {updated_rows}, board={board}")
        return {"status": "OK", "type": "dual", "serial": serial}
    except Exception as error:
        if debug:
            print(f"[DB_ERROR] {error}")
        return {"status": "DB_ERROR", "error": str(error)}
    finally:
        if 'conn' in locals():
            cur.close()
            conn.close()

def update_postcheck(serial: str, author: str):
    """Обновляет postChecked для указанного серийника"""
    serial = re.sub('S/N:', '', serial).replace(' ', '')
    current_date = datetime.now().strftime("%Y-%m-%d")
    current_time = datetime.now().strftime("%H:%M:%S")
    try:
        connection = psycopg2.connect(**get_db_config())
        cursor = connection.cursor()
        query = """UPDATE mac SET (date, time, author, "postChecked") = (%s, %s, %s, %s) WHERE serial = %s"""
        cursor.execute(query, (current_date, current_time, author, True, serial))
        connection.commit()
        return {"status": "POSTCHECKED", "serial": serial, "author": author, "datetime": f"{current_date} {current_time}"}
    except (Exception, Error) as error:
        return {"status": "DB_ERROR", "error": str(error)}
    finally:
        if 'connection' in locals() and connection:
            cursor.close()
            connection.close()

def add_board_column(debug: bool = False):
    conn = psycopg2.connect(**get_db_config())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'mac'
                      AND column_name = 'board';
                """)
                exists = cur.fetchone()
                if exists:
                    if debug:
                        print("✅ Поле 'board' уже существует.")
                else:
                    cur.execute("ALTER TABLE public.mac ADD COLUMN board text;")
                    if debug:
                        print("🆕 Добавлено новое поле 'board' (тип text).")
    finally:
        conn.close()

def _generate_board_id(prefix="Board_"):
    clean_prefix = (prefix or "Board_").strip()
    sep = "" if clean_prefix.endswith(("_", "-")) else "_"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{clean_prefix}{sep}{timestamp}"

def peek_next_board_id(prefix="Board_"):
    """Возвращает следующий B/N с префиксом и штампом даты/времени, не изменяя журнал."""
    return _generate_board_id(prefix)

def commit_board_id(prefix="Board_", board_id=None):
    """Записывает B/N в журнал после успешной операции."""
    if not board_id:
        board_id = _generate_board_id(prefix)
    try:
        counter_file = get_counter_file()
        os.makedirs(os.path.dirname(counter_file), exist_ok=True)
        with open(counter_file, "a", encoding="utf-8") as f:
            f.write(f"{board_id}\n")
    except Exception as e:
        print(f"[ERR] Не удалось записать board_id в файл журнала: {e}")
    return board_id
