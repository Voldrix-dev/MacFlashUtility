# MacFU (MacFlashUtility)

**English** | [Русский](#русский)

Desktop utility built with **PySide6** for flashing MAC addresses and tracking device serial numbers. An operator scans S/N and B/N barcodes — the utility validates against a database, generates EEPROM, flashes the device over **SSH**, verifies the result, and records data in **PostgreSQL** and **Google Sheets**.

## Features

- **MAC/EEPROM flashing**: write via SSH (NAND stdin), U-Boot env, SPI.
- **Database validation**: PostgreSQL — serial numbers, MAC, statuses, post-check.
- **Google Sheets**: automatic result logging, visual style editor (`style_editor.py`).
- **Built-in DHCP server**: address assignment and MAC confirmation.
- **Admin mode**: pkexec/polkit login, required-field management.
- **Dark theme**, qtawesome icons, logs with copy/select.

## Installation

```bash
./setup_env.sh            # create venv and install dependencies
source macenv/bin/activate
python run.py             # or ./run.sh
```

Dependencies: `PySide6`, `qtawesome`, `paramiko`, `scp`, `gspread`, `oauth2client`, `psycopg2-binary`, `scapy`, `pyroute2` (see `requirements.txt`).

## Configuration

The real `Data/options.json` is not stored in the repository (contains passwords and keys). Copy the example and fill it in:

```bash
cp Data/options.json.example Data/options.json
```

Then set `ssh_password`, `db_password`, `gs_credentials_path` (Google service-account JSON key) and, if needed, `db_host`, `board_prefix`, `author`.

## Structure

| File | Purpose |
|------|---------|
| `gui.py` | Main window, workflow, DHCP, Google |
| `option.py` | Settings dialog (General, Google Sheets, Device/DB, DHCP) |
| `style_editor.py` | Visual editor for `conditional_format.json` |
| `ssh.py` | SSH flashing, NAND stdin, U-Boot, SPI |
| `db.py` | PostgreSQL, board-number journal, EEPROM generation |
| `g_sheets.py` | Google Sheets manager and worker |
| `dhcp_server.py`, `dhcp_gui.py` | DHCP server and interface |
| `mac_flash/` | Standalone MAC-flashing GUI |

Full documentation in [`info.md`](info.md).

## License

Specify your license (e.g. MIT) before publishing.

---

# Русский

Desktop-утилита на **PySide6** для прошивки MAC-адресов и учёта серийных номеров устройств. Оператор сканирует S/N и B/N штрих-кодами — утилита проверяет БД, генерирует EEPROM, прошивает устройство по **SSH**, проверяет результат и фиксирует данные в **PostgreSQL** и **Google Sheets**.

## Возможности

- **Прошивка MAC/EEPROM**: запись через SSH (NAND stdin), U-Boot env, SPI.
- **Проверка по БД**: PostgreSQL — серийные номера, MAC, статусы, пост-проверка.
- **Google Sheets**: автоматическая запись результатов в листы, визуальный редактор стиля (`style_editor.py`).
- **Встроенный DHCP-сервер**: выдача адресов и подтверждение MAC.
- **Админ-режим**: вход через polkit/pkexec, управление обязательными полями.
- **Тёмная тема**, иконки qtawesome, логи с копированием/выделением.

## Установка

```bash
./setup_env.sh            # создаёт venv и ставит зависимости
source macenv/bin/activate
python run.py             # или ./run.sh
```

Зависимости: `PySide6`, `qtawesome`, `paramiko`, `scp`, `gspread`, `oauth2client`, `psycopg2-binary`, `scapy`, `pyroute2` (см. `requirements.txt`).

## Конфигурация

Реальный конфиг `Data/options.json` не хранится в репозитории (содержит пароли и ключи). Скопируйте пример и заполните:

```bash
cp Data/options.json.example Data/options.json
```

Затем укажите `ssh_password`, `db_password`, `gs_credentials_path` (JSON-ключ сервис-аккаунта Google) и при необходимости `db_host`, `board_prefix`, `author`.

## Структура

| Файл | Назначение |
|------|------------|
| `gui.py` | Главное окно, workflow, DHCP, Google |
| `option.py` | Диалог настроек (Основные, Google Sheets, Устройство/БД, DHCP) |
| `style_editor.py` | Визуальный редактор `conditional_format.json` |
| `ssh.py` | Прошивка по SSH, NAND stdin, U-Boot, SPI |
| `db.py` | PostgreSQL, журнал board-номеров, генерация EEPROM |
| `g_sheets.py` | Google Sheets менеджер и worker |
| `dhcp_server.py`, `dhcp_gui.py` | DHCP-сервер и интерфейс |
| `mac_flash/` | Отдельный GUI прошивки MAC |

Подробная документация — в [`info.md`](info.md).

## Лицензия

Укажите вашу лицензию (например, MIT) перед публикацией.