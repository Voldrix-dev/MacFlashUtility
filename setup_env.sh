#!/usr/bin/env bash
set -e
VENV_DIR="macenv"
echo "🚀 Установка окружения для MacFlashUtility"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
echo "📦 Устанавливаю зависимости"
pip install --upgrade pip
pip install pyside6 qtawesome paramiko scp oauth2client gspread psycopg2-binary
echo "✅ Готово! Окружение собрано."
echo "Чтобы активировать:"
echo "source $VENV_DIR/bin/activate"

