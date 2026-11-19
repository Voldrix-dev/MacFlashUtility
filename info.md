# MacFlashUtility

Desktop-утилита на **PySide6** для прошивки MAC-адресов и учёта серийных номеров устройств. Оператор сканирует S/N и B/N штрих-кодами — утилита проверяет БД, генерирует EEPROM, прошивает устройство по SSH, проверяет результат и фиксирует данные в PostgreSQL и Google Sheets.

---

## Содержание

1. [Точка входа](#точка-входа)
2. [Архитектура](#архитектура)
3. [Основной workflow](#основной-workflow)
4. [Механизмы](#механизмы)
5. [Прошивка](#прошивка)
6. [Данные](#данные)
7. [Админ-режим](#админ-режим)
8. [Конфигурация](#конфигурация)

---

## Точка входа

**Файл:** `run.py`

- Создаёт `QApplication` (PySide6), устанавливает стиль `Fusion`, иконку `Data/run.svg`, имя приложения `Mac-App`.
- При старте вызывает `install_desktop_entry()` — создаёт `.desktop` файл в `~/.local/share/applications/Mac-App.desktop` (если он изменился), делает его исполняемым.
- Запускает главный виджет `MacFlashGUI(base_path)`.

---

## Архитектура

### GUI-слой

| Модуль | Назначение |
|--------|------------|
| `gui.py` | Основной виджет `MacFlashGUI`: обработка сканирования, координация workflow, интеграция с Google Sheets, таймеры, админ-сессия, логика приёма S/N и B/N. |
| `menu.py` | `ScrollableMenu` — выпадающий поисковый список листов Google Sheets с фильтрацией и выделением текущего. |
| `option.py` | `OptionsDialog` — окно настроек с переключателями (debug, recovery, sheets, flash_mode и т.д.), полями ввода, выбором SH-скрипта. |
| `style.py` | Тёмная Qt-стилистика, статус-индикаторы (`Connected`, `Done`), helpers для полей. |

### Данные

| Модуль | Назначение |
|--------|------------|
| `db.py` | PostgreSQL (`psycopg2`): проверка S/N, обновление MAC (single/dual), генерация board ID, миграция схемы (`add_board_column`). |
| `g_sheets.py` | `gspread` + OAuth: авторизация, выбор/создание листов, добавление записей, проверка дублей, условное форматирование. |

### Железо / связь

| Модуль | Назначение |
|--------|------------|
| `ssh.py` | Прошивка по SSH: передача EEPROM (SCP), `flash_erase` + `nandwrite`, установка `eth1addr` в U-Boot env, пост-проверка, режим `flash_mac_direct`. |
| `dpc.py` | `DevicePostCheck`: контроль ребута устройства через DHCP/ping, проверка готовности SSH, получение фактического MAC от DHCP-сервера / ARP-таблицы (с fallback через SSH). |
| `thread_.py` | `DeviceWatcher` (ping `15.15.15.15`), `run_in_thread`, сигналы для фоновых задач. |

---

## Основной workflow

1. **Ввод оператора** → сохранение в `options.json`.
2. **Сканирование S/N** (`CUINF7`) → авто-проверка в БД (`check_serial`).
3. **Автогенерация B/N** (если включено) → `peek_next_board_id`.
4. **Выбор листа Google Sheets** (если включено).
5. **Прошивка** (`eeprom_spi` или `u-boot_environment`).
6. **Пост-проверка** (`DevicePostCheck`).
7. **Фиксация в БД** и **Google Sheets** (если включено).
8. **Сброс полей** (если `auto_reset`).

---

## Механизмы

### Сканирование и буфер

- **Буфер клавиатуры** (`keyPressEvent`): символы накапливаются в `self.buffer`, по `Enter` происходит `fix_layout(raw)` (исправление раскладки/регистра) и вызов `process_scanned`.
- **Дедупликация** (`_dedupe_scan`): если строка чётной длины и первая половина равна второй — возвращается первая половина (защита от двойного считывания сканером).

### Обработка кодов

- **S/N** — префикс `CUINF7`: поле `serial_number` → авто-проверка `check_serial` → depending от статуса: прошивка, recovery, ошибка.
- **B/N** — префиксы `Q_ITL`/`QRINF`: поле `board_number`. Если прошивка `busy` — откладывается в `pending_bn`; если MAC уже готов — вызывается `on_postcheck_clicked`.
- **Мигание B/N** (`blink_bn`): при ожидании board номер жёлто мигает.

### Проверка S/N в PostgreSQL (`db.py`)

- `check_serial`: запрос `SELECT mac, serial, postChecked FROM public.mac WHERE serial = %s`.
- **Статусы:**
  - `SERIALNO_IS_FREE` — нет записи → стоп (MAC не найден).
  - `SERIALNO_IS_NOT_FINISHED` — есть MAC, `postChecked=FALSE` → прошивка.
  - `SERIALNO_IS_BUSY` — `postChecked=TRUE` → в обычном режиме ошибка; в **Recovery Mode** — повторная прошивка.
  - `NO_LATIN` — недопустимые символы.

### Механизм отложенного B/N (`pending_bn`)

- Если B/N пришёл во время прошивки — сохраняется в `pending_bn` вместе с `sn`, `lan`, `wan`.
- После завершения прошивки/пост-чека — применяется автоматически.
- Если пришёл новый S/N — `pending_bn` сбрасывается.

### Watchdog и остановка

- **Flash watchdog** (`flash_watchdog_ms`, по умолч. 180000мс): если прошивка зависла — разблокирует UI, сбрасывает `busy`.
- **Stop event:** `stop_current_operation` → `current_stop_event.set()` → проверяется в `ssh.py` на каждом шаге.
- **Активные SSH:** глобальное множество `_ACTIVE_SSH_CLIENTS` с мьютексом; `request_abort_active_ssh` принудительно закрывает все соединения.

### DeviceWatcher (`thread_.py`)

- Фоновый `QThread` с циклом `ping -c 1 -W 1 15.15.15.15` каждые `interval` сек.
- Сигнал `status_changed(online)` → обновление индикатора Connected.
- Смена режима прошивки (`set_mode`) сбрасывает кеш состояния.

---

## Прошивка

### Режим `eeprom_spi` (`ssh.py:eeprom_flash`)

1. `find_new_host` — ping `15.15.15.15`.
2. `_connect_ssh_with_retry` — ждёт стабильный SSH (3 успешных коннекта подряд), подключается с 12 попытками.
3. Опционально: `run_install_script` — передаёт `Data/*.sh` по SCP и выполняет **без** `reboot` (regex-фильтрация строк с reboot).
4. SCP передаёт `.bin` на устройство.
5. `flash_erase /dev/mtd0 0 1` → `nandwrite.mtd-utils -p /dev/mtd0 <file>` → `cmp -n 12 <file> /dev/mtd0` (проверка первых 12 байт).
6. `flash_mac_from_spi_paramiko` — читает 12 байт из `/dev/mtd0` (dd + SFTP fallback), парсит LAN/WAN, проверяет/устанавливает `eth1addr` в U-Boot env (`fw_printenv`/`fw_setenv`), ребут.
7. `DevicePostCheck.run_postcheck` — ждёт возврата устройства (отслеживая DHCP-запрос и ping), проверяет SSH, получает подтверждённый MAC от DHCP-сервера / ARP.

### Режим `u-boot_environment` (`ssh.py:flash_mac_direct`)

- Подключение по SSH → `ensure_uboot_env` (если env сломан — `mkenvimage` + `dd` на `/dev/mmcblk2`).
- `fw_setenv eth1addr <mac>` → `sync` → ребут.
- Пост-чек.

---

## Данные

### PostgreSQL (`db.py`)

- **`make_eeprom_bytes`**: формирует байты MAC (`lan+wan` или `wan+wan` для single) в памяти, без файла-шаблона.
- **`check_serial`**: проверка S/N в БД, возвращает статус и MAC-адреса.
- **`update_mac_single`** / **`update_mac_dual`**: обновление MAC, author, date, time, board.
- **`update_postcheck`**: простановка `postChecked=TRUE`.
- **`add_board_column`**: миграция — добавление колонки `board` если нет.
- **`peek_next_board_id`** / **`commit_board_id`**: генерация и запись board ID с префиксом и штампом времени.

### Google Sheets (`g_sheets.py`)

- **Авторизация:** `oauth2client.service_account.ServiceAccountCredentials` + `gspread`.
- **Листы:** `select_or_create_sheet` — если `NEW` → создаёт лист с датой_автором, применяет `conditional_format.json` (freeze row, conditional formatting), записывает заголовок по `row_template`.
- **Список листов:** `get_sheet_list` — датированные листы сортируются по дате (поддержка `%Y-%m-%d`, `%d.%m.%y`), `NEW` в начале.
- **Запись:** `add_record` — форматированная строка `{board:<50} | {serial} |` → в колонку A.
- **Дедупликация:** `is_duplicate` — парсит существующие строки, сравнивает B/N + S/N.
- **Таймеры:** авто-обновление списка листов (`gs_sheet_refresh_seconds`), периодическая проверка авторизации (каждые 15с).

---

## Админ-режим

- **Вход:** `pkexec scripts/admin_auth_helper.sh --authorize` → запрашивает пароль системный.
- **Сессия:** таймер `admin_session_seconds` (по умолч. 600с), после истечения — `_expire_admin_session`.
- **Права:**
  - Ручной ввод S/N и B/N.
  - Переключение `require_board_number` (обязательность B/N).
  - Переключение `send_board_number` (отправлять ли B/N в БД/таблицу или заменять на дату/время).

---

## Конфигурация

- `Data/options.json` — все переключатели, пути, префиксы.
- `Data/instructions.json` — инструкции по режимам прошивки (показываются по `?`).
- Динамическое обновление: `OptionsDialog.get_options()` → `save_options()` → мгновенный эффект на UI/таймеры.
