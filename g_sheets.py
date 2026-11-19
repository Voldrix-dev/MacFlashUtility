import os
import json
import re
import gspread
import webbrowser
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from PySide6.QtCore import QObject, Signal, Slot

from templates import prepare_template, safe_format


class GoogleWorker(QObject):
    log = Signal(str)
    result = Signal(dict)
    sheet_list_ready = Signal(list)
    sheet_status = Signal(bool)
    current_sheet_changed = Signal(str)

    def __init__(self, gs_manager):
        super().__init__()
        self.gs = gs_manager
        self._running = True
        self._last_sheet_titles = None

    def _debug(self, msg: str):
        if getattr(self.gs, "debug", False):
            self.log.emit(f"[DEBUG] {msg}")

    @Slot()
    def start(self):
        if not self._running:
            return
        self._debug("Запуск GoogleWorker")
        if self.gs.authorize():
            self.log.emit("[OK] Авторизация прошла успешно")
            self.sheet_status.emit(True)
            self.fetch_sheets()
        else:
            self.log.emit("[ERR] Ошибка авторизации Google API")
            self.sheet_status.emit(False)

    @Slot()
    def stop(self):
        self._running = False
        self._debug("GoogleWorker остановлен")

    @Slot()
    def check_auth(self):
        if not self._running:
            return
        try:
            # Проверяем, живо ли текущее соединение
            if not self.gs.spreadsheet:
                ok = self.gs.authorize()
                self.sheet_status.emit(ok)
                if ok:
                    self.fetch_sheets()
                return
            # Проверяем, что spreadsheet жив — пробуем запросить метаданные
            try:
                _ = self.gs.spreadsheet.fetch_sheet_metadata(params={"fields": "spreadsheetId"})
                self.sheet_status.emit(True)
            except (Exception,):
                # Соединение мёртво — переавторизуем
                self._debug("Соединение упало, пробую переавторизоваться")
                self.gs.spreadsheet = None
                self.gs.client = None
                ok = self.gs.authorize()
                self.sheet_status.emit(ok)
                if ok:
                    self.fetch_sheets()
        except Exception as e:
            self._debug(f"Проверка подключения неудачна: {e}")
            self.sheet_status.emit(False)

    @Slot(str, str)
    def add_record(self, serial: str = None, board: str = None):
        if not self.gs.current_sheet:
            self.result.emit({"ok": False, "msg": "[ERR] Лист не выбран"})
            return
        if self.gs.is_duplicate(serial, board):
            msg = f"[WARN] Дубликат записи:   {board:<60} |    {serial}    |"
            self.result.emit({"ok": False, "msg": msg})
            return
        ok = self.gs.add_record(serial, board)
        if ok:
            msg = f"[OK] Добавлена запись в GS:   {board:<60} |    {serial}    |"
            self.result.emit({"ok": True, "msg": msg})
        else:
            msg = f"[ERR] Не удалось добавить запись:   {board:<60} |    {serial}    |"
            self.result.emit({"ok": False, "msg": msg})

    @Slot()
    def fetch_sheets(self):
        try:
            if not self.gs.spreadsheet:
                if not self.gs.authorize():
                    self.sheet_list_ready.emit([])
                    return
            sheets = self.gs.get_sheet_list()
            titles = [s.get("title", "") for s in sheets]
            if titles != self._last_sheet_titles:
                self._last_sheet_titles = titles
                self._debug(f"Найдено листов Google Sheets: {len(sheets)}")
            if self.gs.current_sheet is not None:
                current_title = self.gs.current_sheet.title
                sheet_titles = {s.get("title", "") for s in sheets}
                if current_title and current_title not in sheet_titles:
                    self.gs.current_sheet = None
                    self.current_sheet_changed.emit("")
                    self.log.emit(f"[WARN] Выбранный лист удалён или недоступен: {current_title}")
            self.sheet_list_ready.emit(sheets)
        except Exception as e:
            self._debug(f"Ошибка получения списка листов: {e}, пробую переавторизоваться")
            # Пробуем переавторизоваться и повторить
            self.gs.spreadsheet = None
            self.gs.client = None
            if self.gs.authorize():
                try:
                    sheets = self.gs.get_sheet_list()
                    self.sheet_list_ready.emit(sheets)
                    return
                except Exception as e2:
                    self.log.emit(f"[ERR] Ошибка получения списка листов после переавторизации: {e2}")
            self.log.emit(f"[ERR] Ошибка получения списка листов Google Sheets: {e}")
            self.sheet_list_ready.emit([])

    @Slot(str)
    def select_sheet(self, sheet_str: str):
        try:
            if not self.gs.spreadsheet and not self.gs.authorize():
                self.result.emit({"ok": False, "msg": "[ERR] Не удалось авторизоваться при выборе листа"})
                return
            if sheet_str in ("", "NEW"):
                ok = self.gs.select_or_create_sheet(self.gs.author or "operator", "NEW")
                if ok:
                    title = self.gs.current_sheet.title if self.gs.current_sheet else ""
                    self.current_sheet_changed.emit(title)
                    self.log.emit(f"[OK] Подключен лист #{title}")
                    self.fetch_sheets()
                else:
                    self.log.emit("[ERR] Не удалось создать новый лист")
                return
            # Выбираем по названию листа, а не по индексу
            ok = self.gs.select_or_create_sheet_by_title(self.gs.author or "operator", sheet_str)
            if ok:
                ws = self.gs.current_sheet
                title = ws.title if ws else ""
                self.current_sheet_changed.emit(title)
                self.result.emit({"ok": True, "msg": f"[OK] Подключен лист #{title}"})
            else:
                self.result.emit({"ok": False, "msg": f"[ERR] Не удалось подключить лист #{sheet_str}"})
        except Exception as e:
            self.log.emit(f"[ERR] Ошибка при выборе листа: {e}")
            self.result.emit({"ok": False, "msg": str(e)})

    @Slot(str, str)
    def delete_record(self, serial: str = None, board: str = None):
        if not self.gs.current_sheet:
            self.result.emit({"ok": False, "msg": "[ERR] Лист не выбран"})
            return
        ok = self.gs.delete_record(serial, board)
        if ok:
            msg = f"[OK] Удалена запись:   {board:<60} |    {serial}    |"
            self.result.emit({"ok": True, "msg": msg})
        else:
            msg = f"[ERR] Не удалось удалить запись:   {board:<60} |    {serial}    |"
            self.result.emit({"ok": False, "msg": msg})

    @Slot()
    def open_sheet_in_browser(self):
        try:
            if not self.gs.current_sheet:
                # Пробуем переавторизоваться и получить список листов
                if not self.gs.spreadsheet:
                    if not self.gs.authorize():
                        self.log.emit("[WARN] Не удалось авторизоваться")
                        return
                # Пробуем выбрать первый лист, если есть
                try:
                    worksheets = self.gs.spreadsheet.worksheets()
                    if worksheets:
                        self.gs.current_sheet = worksheets[0]
                    else:
                        self.log.emit("[WARN] Нет доступных листов")
                        return
                except Exception:
                    self.log.emit("[WARN] Сначала выберите или создайте лист")
                    return
            ok = self.gs.open_in_browser()
            if ok: self.log.emit("[OK] Таблица открыта в браузере")
            else: self.log.emit("[ERR] Не удалось открыть таблицу в браузере")
        except Exception as e:
            self.log.emit(f"[ERR] open_sheet_in_browser: {e}")

class GoogleSheetsManager:
    def __init__(self, debug: bool = False, author: str = None, base_path: str = None, credentials_path: str = None, spreadsheet_name: str = None, style_rules_path: str = None, row_template: str = None, sheet_title: str = None, sheet_rows: int = 500, sheet_cols: int = 2, header_template: str = None):
        if base_path is None:
            base_path = os.path.dirname(os.path.abspath(__file__))
        self.base_path = base_path
        self.form_rules = style_rules_path or os.path.join(self.base_path, "Data", "conditional_format.json")
        self.credentials_path = credentials_path or ""
        self.spreadsheet_name = spreadsheet_name or "Database_board_serial"
        self.row_template = row_template or "{board:<50} |    {serial}    |"
        self.header_template = header_template or "{board:<50} | Всего: {count} | {serial}"
        self.sheet_title = (sheet_title or "").strip()
        try:
            self.sheet_rows = max(1, int(sheet_rows))
        except (TypeError, ValueError):
            self.sheet_rows = 500
        try:
            self.sheet_cols = max(1, int(sheet_cols))
        except (TypeError, ValueError):
            self.sheet_cols = 2
        self.debug = debug
        self.client = None
        self.spreadsheet = None
        self.current_sheet = None
        self.author = author

    def authorize(self) -> bool:
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name(self.credentials_path, scope)
            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open(self.spreadsheet_name)
            return True
        except Exception as e:
            if self.debug:
                print(f"[GoogleSheetsManager][AUTH ERROR] {e}")
            return False

    def _new_sheet_title(self, author: str) -> str:
        now = datetime.now()
        default_title = now.strftime("%d.%m.%y") + f"_{author}"
        if not self.sheet_title:
            return default_title
        try:
            title = self.sheet_title.format(date=now.strftime("%d.%m.%y"), author=author or "",
                year=now.strftime("%Y"), month=now.strftime("%m"), day=now.strftime("%d"),).strip()
        except (KeyError, IndexError, ValueError):
            title = self.sheet_title.strip()
        return title or default_title

    def select_or_create_sheet(self, author: str, sheet_str: str = "NEW") -> bool:
        if not self.spreadsheet:
            if not self.authorize():
                return False
        self.author = author
        sheet = None
        if sheet_str == "NEW":
            today_title = self._new_sheet_title(author)
            for ws in self.spreadsheet.worksheets():
                if ws.title == today_title:
                    sheet = ws
                    break
            if not sheet:
                sheet = self.spreadsheet.add_worksheet(title=today_title, rows=self.sheet_rows, cols=self.sheet_cols)
                bdn = "Номер платы"
                sern = "Серийный номер"
                tmpl = prepare_template(self.header_template or "{board:<50} | Всего: {count} | {serial}")
                count_match = re.search(r"\{count[^}]*\}", tmpl)
                if count_match:
                    pre = safe_format(tmpl[:count_match.start()], board=bdn, serial=sern)
                    post = safe_format(tmpl[count_match.end():], board=bdn, serial=sern)
                    header_cell = f'="{pre}" & COUNTA(A2:A1000000) & "{post}"'
                    sheet.update(values=[[header_cell]], range_name="A1", raw=False)
                elif self.sheet_cols >= 2:
                    combined = safe_format(tmpl, board=bdn, serial=sern, count="")
                    formula = '= "Всего устройств - " & COUNTA(A2:A1000000)'
                    sheet.update(values=[[combined, formula]], range_name="A1:B1", raw=False)
                else:
                    combined = safe_format(tmpl, board=bdn, serial=sern, count="")
                    sheet.update(values=[[combined]], range_name="A1", raw=False)
                try:
                    with open(self.form_rules, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    def set_sheet_id(obj):
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if k == "sheetId" and (v is None):
                                    obj[k] = sheet.id
                                else:
                                    set_sheet_id(v)
                        elif isinstance(obj, list):
                            for item in obj:
                                set_sheet_id(item)
                    set_sheet_id(data)
                    # frozenRowCount задаём только если он не указан в файле стиля
                    has_frozen = any(
                        "updateSheetProperties" in r
                        and "frozenRowCount" in r["updateSheetProperties"].get("properties", {}).get("gridProperties", {})
                        for r in data.get("requests", [])
                    )
                    if not has_frozen:
                        data.setdefault("requests", []).append({
                            "updateSheetProperties": {
                                "properties": {
                                    "sheetId": sheet.id,
                                    "gridProperties": {
                                        "frozenRowCount": 1
                                    }
                                },
                                "fields": "gridProperties.frozenRowCount"
                            }
                        })
                    self.spreadsheet.batch_update(data)
                except Exception as e:
                    if self.debug:
                        print(f"[DEBUG] Ошибка форматирования листа: {e}")
        else:
            # Выбор по названию листа (а не по индексу!)
            try:
                for ws in self.spreadsheet.worksheets():
                    if ws.title == sheet_str:
                        sheet = ws
                        break
                if sheet is None:
                    if self.debug:
                        print(f"[DEBUG] Лист с названием '{sheet_str}' не найден")
                    return False
            except Exception as e:
                if self.debug:
                    print(f"[DEBUG] Ошибка выбора листа по названию: {e}, sheet_str={repr(sheet_str)}")
                return False
        self.current_sheet = sheet
        return True

    def select_or_create_sheet_by_title(self, author: str, sheet_title: str) -> bool:
        """Выбирает лист по точному названию. Если не находит — возвращает False (не создаёт)."""
        if not self.spreadsheet:
            if not self.authorize():
                return False
        self.author = author
        try:
            for ws in self.spreadsheet.worksheets():
                if ws.title == sheet_title:
                    self.current_sheet = ws
                    return True
            if self.debug:
                print(f"[DEBUG] Лист с названием '{sheet_title}' не найден")
            return False
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] Ошибка select_or_create_sheet_by_title: {e}")
            return False
    
    def get_sheet_list(self) -> list:
        """Возвращает список листов: сначала NEW, потом листы с датой (от новых к старым), потом оставшиеся.
        Возвращает [{title: str}, ...] — используется для меню и выбора по title."""
        if not self.spreadsheet and not self.authorize():
            return []
        try:
            worksheets = self.spreadsheet.worksheets()
            dated_sheets = []
            other_sheets = []
            for ws in worksheets:
                parts = ws.title.split("_", 1)
                if len(parts) > 1:
                    date_str = parts[0]
                    for fmt in ("%Y-%m-%d", "%d.%m.%y", "%d.%m.%Y"):
                        try:
                            dt = datetime.strptime(date_str, fmt)
                            dated_sheets.append((dt, ws.title))
                            break
                        except ValueError:
                            continue
                    else:
                        other_sheets.append(ws.title)
                else:
                    other_sheets.append(ws.title)
            dated_sheets.sort(key=lambda x: x[0], reverse=True)
            sorted_titles = [{"title": "NEW"}] + [{"title": t} for _, t in dated_sheets] + [{"title": t} for t in other_sheets]
            return sorted_titles
        except Exception as e:
            if self.debug:
                print(f"[GoogleSheetsManager] Ошибка при получении списка листов: {e}")
            return []
    
    def add_record(self, serial_number: str, board_number: str) -> bool:
        if not self.current_sheet:
            # Пробуем переавторизоваться
            if not self.spreadsheet:
                if not self.authorize():
                    if self.debug:
                        print("[GoogleSheetsManager] Нет активного листа и не удалось авторизоваться!")
                    return False
            if self.debug:
                print("[GoogleSheetsManager] Нет активного листа!")
            return False
        try:
            try:
                        combined = safe_format(self.row_template, board=board_number, serial=serial_number, count="")
            except Exception:
                combined = f"{board_number:<50} |    {serial_number}    |"
            existing_values = self.current_sheet.col_values(1)
            next_row = len(existing_values) + 1
            self.current_sheet.update([[combined]], f"A{next_row}")
            return True
        except Exception as e:
            # Пробуем переавторизоваться и повторить один раз
            if self.debug:
                print(f"[GoogleSheetsManager] Ошибка добавления строки: {e}, пробую переавторизоваться")
            old_spreadsheet = self.spreadsheet
            self.spreadsheet = None
            self.client = None
            self.current_sheet = None
            if self.authorize():
                try:
                    try:
                        combined = safe_format(self.row_template, board=board_number, serial=serial_number, count="")
                    except Exception:
                        combined = f"{board_number:<50} |    {serial_number}    |"
                    existing_values = self.current_sheet.col_values(1) if self.current_sheet else []
                    next_row = len(existing_values) + 1
                    self.current_sheet.update([[combined]], f"A{next_row}")
                    return True
                except Exception as e2:
                    if self.debug:
                        print(f"[GoogleSheetsManager] Повторная попытка неудачна: {e2}")
            return False

    def is_duplicate(self, serial_number: str, board_number: str) -> bool:
        if not self.current_sheet:
            return False
        try:
            sn = serial_number.strip().upper()
            bdn = board_number.strip().upper()
            for row in self.current_sheet.col_values(1):
                parts = [p.strip().upper() for p in row.split("|") if p.strip()]
                if len(parts) >= 2:
                    existing_bdn, existing_sn = parts[0], parts[1]
                    if existing_bdn == bdn and existing_sn == sn:
                        return True
            return False
        except Exception as e:
            if self.debug:
                print(f"[GoogleSheetsManager] Ошибка проверки дубля: {e}")
            return False

    def delete_record(self, serial_number: str, board_number: str) -> bool:
        """Удаляет запись из текущего листа по совпадению S/N и B/N."""
        if not self.current_sheet:
            if self.debug:
                print("[GoogleSheetsManager] Нет активного листа!")
            return False
        try:
            rows = self.current_sheet.get_all_values()
            for row_idx, row in enumerate(rows, start=1):
                if len(row) < 2:
                    continue
                # Парсим B/N и S/N из формата "B/N | S/N |"
                parts = [p.strip().upper() for p in row[0].split("|") if p.strip()]
                if len(parts) < 2:
                    continue
                existing_bdn, existing_sn = parts[0], parts[1]
                target_bdn = (board_number or "").strip().upper()
                target_sn = (serial_number or "").strip().upper()
                if existing_bdn == target_bdn and existing_sn == target_sn:
                    self.current_sheet.delete_rows(row_idx)
                    return True
            return False
        except Exception as e:
            if self.debug:
                print(f"[GoogleSheetsManager] Ошибка удаления записи: {e}")
            return False

    def open_in_browser(self):
        if not self.current_sheet:
            return False
        try:
            url = f"{self.spreadsheet.url}#gid={self.current_sheet.id}"
            webbrowser.open(url)
            return True
        except Exception as e:
            if self.debug:
                print(f"[GoogleSheetsManager] Ошибка открытия URL: {e}")
            return False

    def close(self):
        """Очищаем ссылки на объекты, чтобы GC мог собрать их"""
        self.client = None
        self.spreadsheet = None
        self.current_sheet = None
