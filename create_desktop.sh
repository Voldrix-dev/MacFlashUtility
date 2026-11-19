#!/bin/bash

# Скрипт создания .desktop файлов для MacFU
# Создает ярлыки на рабочем столе и в меню приложений.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_HOME="${REAL_HOME:-$HOME}"
TARGET_USER="${REAL_USER:-$(whoami)}"

DESKTOP_DIR="$TARGET_HOME/Desktop"
APPLICATIONS_DIR="$TARGET_HOME/.local/share/applications"
DESKTOP_FILE_NAME="MacFU.desktop"

QUIET=false
if [ "${1:-}" = "--quiet" ] || [ "${1:-}" = "-q" ]; then
    QUIET=true
fi

# Убедимся, что папки существуют (если доступны для записи)
mkdir -p "$DESKTOP_DIR" 2>/dev/null || true
mkdir -p "$APPLICATIONS_DIR" 2>/dev/null || true

# =========================================================
# Создаём .desktop файл (запуск от пользователя, без терминала)
# =========================================================

cat <<EOF > "$SCRIPT_DIR/$DESKTOP_FILE_NAME"
[Desktop Entry]
Version=1.0
Type=Application
Name=MacFU
Comment=MAC flashing and serial number accounting utility
Exec=$SCRIPT_DIR/run.sh
Icon=$SCRIPT_DIR/Data/run.svg
Terminal=false
Categories=Development;Utility;
Keywords=mac;flash;eeprom;serial;uart;ssh;
StartupNotify=true
EOF

# Копируем на рабочий стол и в меню приложений
cp -f "$SCRIPT_DIR/$DESKTOP_FILE_NAME" "$DESKTOP_DIR/$DESKTOP_FILE_NAME" 2>/dev/null || true
cp -f "$SCRIPT_DIR/$DESKTOP_FILE_NAME" "$APPLICATIONS_DIR/$DESKTOP_FILE_NAME" 2>/dev/null || true

# Делаем исполняемыми
chmod +x "$DESKTOP_DIR/$DESKTOP_FILE_NAME" 2>/dev/null || true
chmod +x "$APPLICATIONS_DIR/$DESKTOP_FILE_NAME" 2>/dev/null || true
chmod +x "$SCRIPT_DIR/$DESKTOP_FILE_NAME" 2>/dev/null || true

# Если запуск от root, восстанавливаем владельца файлов для обычного пользователя
if [ "$(id -u)" -eq 0 ] && [ "$TARGET_USER" != "root" ]; then
    TARGET_GROUP="$(id -g "$TARGET_USER" 2>/dev/null || echo "$TARGET_USER")"
    chown "$TARGET_USER:$TARGET_GROUP" \
        "$SCRIPT_DIR/$DESKTOP_FILE_NAME" \
        "$DESKTOP_DIR/$DESKTOP_FILE_NAME" \
        "$APPLICATIONS_DIR/$DESKTOP_FILE_NAME" 2>/dev/null || true
fi

if [ "$QUIET" = false ]; then
    echo ""
    echo "Файлы ярлыков:"
    if [ -f "$DESKTOP_DIR/$DESKTOP_FILE_NAME" ]; then
        echo "  Рабочий стол:     $DESKTOP_DIR/$DESKTOP_FILE_NAME"
    fi
    if [ -f "$APPLICATIONS_DIR/$DESKTOP_FILE_NAME" ]; then
        echo "  Меню приложений:  $APPLICATIONS_DIR/$DESKTOP_FILE_NAME"
    fi
    if [ -f "$SCRIPT_DIR/$DESKTOP_FILE_NAME" ]; then
        echo "  Проект:           $SCRIPT_DIR/$DESKTOP_FILE_NAME"
    fi
    echo ""
fi
