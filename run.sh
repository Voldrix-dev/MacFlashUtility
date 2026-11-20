#!/usr/bin/env bash

if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    CYAN='\033[0;36m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; BLUE=''; BOLD=''; NC=''
fi

# 1. Директория проекта и пользователь
PROJECT_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
cd "$PROJECT_DIR" || { echo "Ошибка: Не удалось перейти в $PROJECT_DIR" >&2; exit 1; }

REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || id -u -n)}"
REAL_HOME="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6 || true)"
REAL_HOME="${REAL_HOME:-$HOME}"

LOG_FILE="$PROJECT_DIR/Data/launcher.log"

# Ротация лога при размере более 1 МБ
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv -f "$LOG_FILE.tmp" "$LOG_FILE" 2>/dev/null || true
fi

_log_file() {
    [ -d "$PROJECT_DIR/Data" ] && printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2" >> "$LOG_FILE" 2>/dev/null || true
}

log()  { echo -e "${GREEN}[OK]${NC} $1"; _log_file "INFO" "$1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; _log_file "WARN" "$1"; }
info() { echo -e "${CYAN}[INFO]${NC} $1"; _log_file "INFO" "$1"; }

error() {
    local msg="$1"
    echo -e "${RED}[ERROR]${NC} $msg" >&2
    _log_file "ERROR" "$msg"

    if [ ! -t 1 ]; then
        if command -v notify-send >/dev/null 2>&1; then
            notify-send -u critical "MacFlashUtility - Ошибка запуска" "$msg" 2>/dev/null || true
        elif command -v kdialog >/dev/null 2>&1; then
            kdialog --error "$msg" --title "MacFlashUtility" 2>/dev/null || true
        elif command -v zenity >/dev/null 2>&1; then
            zenity --error --text="$msg" --title="MacFlashUtility" 2>/dev/null || true
        elif command -v xmessage >/dev/null 2>&1; then
            echo "$msg" | xmessage -center -file - 2>/dev/null || true
        fi
    fi

    exit 1
}

# 2. Аргументы командной строки
CUSTOM_VENV=""
FORCE_REINSTALL=false
FORCE_USER=false
FORCE_ROOT=false
DO_CHECK_ONLY=false
SETUP_CAPS_ONLY=false
APP_ARGS=()

show_help() {
    cat <<EOF
Использование: $(basename "$0") [ОПЦИИ] [-- АРГУМЕНТЫ]

Универсальный запуск Mac Flash Utility.

Опции:
  -h, --help           Показать справку
  -c, --check          Проверить окружение
  --setup-caps         Настроить сетевые capabilities
  --reinstall          Переустановить зависимости
  --user               Запуск от обычного пользователя
  --root               Запуск приложения через sudo
  --venv <путь>        Использовать указанное venv

Дополнительные аргументы передаются run.py.
EOF
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) show_help ;;
        -c|--check) DO_CHECK_ONLY=true; shift ;;
        --setup-caps) SETUP_CAPS_ONLY=true; shift ;;
        --reinstall) FORCE_REINSTALL=true; shift ;;
        --user) FORCE_USER=true; shift ;;
        --root) FORCE_ROOT=true; shift ;;
        --venv)
            [ -n "${2:-}" ] || error "--venv требует путь"
            CUSTOM_VENV="$2"; shift 2
            ;;
        --)
            shift; APP_ARGS+=("$@"); break
            ;;
        *)
            APP_ARGS+=("$1"); shift
            ;;
    esac
done

[ "$FORCE_USER" = true ] && [ "$FORCE_ROOT" = true ] && error "Нельзя одновременно использовать --user и --root"

# Баннер
if [ -t 1 ] && [ "$DO_CHECK_ONLY" = false ]; then
    echo "Проект:       $PROJECT_DIR"
    echo "Пользователь: $REAL_USER (uid: $(id -u "$REAL_USER" 2>/dev/null || id -u))"
    echo "Система:      $(uname -s) $(uname -r)"
    echo "Хост:         $(hostname)"
    echo ""
fi

# 3. Проверка Python
command -v python3 >/dev/null 2>&1 || error "Python 3 не найден в системе."
PY_VER_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)"
PY_VER_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)"

if [ "$PY_VER_MAJOR" -lt 3 ] || { [ "$PY_VER_MAJOR" -eq 3 ] && [ "$PY_VER_MINOR" -lt 9 ]; }; then
    error "Требуется Python 3.9+. Обнаружен: $(python3 --version 2>&1)"
fi

# 4. Проверка проекта
[ -f "$PROJECT_DIR/run.py" ] || error "run.py не найден: $PROJECT_DIR"

# 5. Виртуальное окружение
VENV=""

if [ -n "$CUSTOM_VENV" ] && [ -d "$CUSTOM_VENV" ]; then
    VENV="$CUSTOM_VENV"
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    VENV="$PROJECT_DIR/.venv"
elif [ -x "$REAL_HOME/.venv/bin/python" ]; then
    VENV="$REAL_HOME/.venv"
else
    VENV="$([ -w "$PROJECT_DIR" ] && echo "$PROJECT_DIR/.venv" || echo "$REAL_HOME/.venv")"
fi

VENV_PYTHON="$VENV/bin/python"
CAP_PYTHON="$VENV/bin/python-cap"
APP_PYTHON="$VENV_PYTHON"

# Requirements
REQUIREMENTS=""
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    REQUIREMENTS="$PROJECT_DIR/requirements.txt"
elif [ -f "$PROJECT_DIR/scripts/requirements.txt" ]; then
    REQUIREMENTS="$PROJECT_DIR/scripts/requirements.txt"
fi

# Проверка зависимостей
check_dependencies() {
    [ -x "$VENV_PYTHON" ] || return 1

    "$VENV_PYTHON" -c "
modules = ['PySide6', 'psycopg2', 'gspread', 'oauth2client', 'paramiko', 'scp', 'scapy', 'pyroute2']
missing = []
for m in modules:
    try:
        __import__(m)
    except ImportError:
        missing.append(m)
if missing:
    print(','.join(missing))
    raise SystemExit(1)
" 2>/dev/null
}

# Проверка capabilities
has_net_caps() {
    [ -x "$CAP_PYTHON" ] || return 1
    command -v getcap >/dev/null 2>&1 || return 1

    local caps
    caps="$(getcap "$CAP_PYTHON" 2>/dev/null || true)"

    [[ "$caps" == *cap_net_raw* ]] && [[ "$caps" == *cap_net_admin* ]]
}

# Привилегированные команды
run_privileged() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif [ -t 1 ]; then
        sudo "$@"
    elif command -v pkexec >/dev/null 2>&1; then
        pkexec "$@"
    else
        sudo "$@"
    fi
}

# Настройка capabilities
apply_net_caps() {
    [ -x "$VENV_PYTHON" ] || { warn "Python venv не найден: $VENV_PYTHON"; return 1; }
    command -v setcap >/dev/null 2>&1 || { warn "setcap не найден. Установите libcap."; return 1; }
    command -v getcap >/dev/null 2>&1 || { warn "getcap не найден. Установите libcap."; return 1; }
    local real_python caps
    real_python="$(readlink -f "$VENV_PYTHON" 2>/dev/null)" || return 1
    [ -f "$real_python" ] || { warn "Python не найден: $real_python"; return 1; }
    info "Создание отдельного Python с сетевыми правами"
    cp -f "$real_python" "$CAP_PYTHON" || { warn "Не удалось создать $CAP_PYTHON"; return 1; }
    chmod 755 "$CAP_PYTHON" || return 1
    run_privileged setcap cap_net_raw,cap_net_admin+ep "$CAP_PYTHON" || { warn "Не удалось назначить capabilities"; return 1; }
    caps="$(getcap "$CAP_PYTHON" 2>/dev/null || true)"
    if [[ "$caps" == *cap_net_raw* ]] && [[ "$caps" == *cap_net_admin* ]]; then
        log "Capabilities успешно назначены"
        log "$caps"
        "$CAP_PYTHON" -c 'import scapy; print("Scapy OK")' >/dev/null 2>&1 || {
            warn "python-cap не может импортировать Scapy"
            return 1
        }
        log "python-cap успешно проверен"
        return 0
    fi
    warn "Проверка capabilities не пройдена"
    return 1
}

# 6. Диагностика --check
if [ "$DO_CHECK_ONLY" = true ]; then
    echo ""
    echo -e "${CYAN}=== ДИАГНОСТИКА ОКРУЖЕНИЯ MacFU ===${NC}"
    echo "Системный Python:   $(python3 --version 2>&1)"
    echo "Проект:             $PROJECT_DIR"
    echo "Пользователь:       $REAL_USER"
    echo "Домашняя папка:    $REAL_HOME"
    echo "Venv:              $VENV"

    if [ -x "$VENV_PYTHON" ]; then
        echo -e "Venv:              ${GREEN}Создан${NC} ($("$VENV_PYTHON" --version 2>&1))"

        MISSING_DIAG=""
        if MISSING_DIAG="$(check_dependencies)"; then
            echo -e "Зависимости:       ${GREEN}Все установлены${NC}"
        else
            echo -e "Зависимости:       ${RED}Отсутствуют: $MISSING_DIAG${NC}"
        fi

        if has_net_caps; then
            echo -e "Capabilities:      ${GREEN}Настроены${NC}"
            echo "Python:            $CAP_PYTHON"
        else
            echo -e "Capabilities:      ${YELLOW}Не настроены${NC}"
        fi
    else
        echo -e "Venv:              ${YELLOW}Не создан${NC}"
        echo -e "Зависимости:       ${YELLOW}Ожидают инициализации${NC}"
    fi

    OPTIONS_FILE="$PROJECT_DIR/Data/options.json"
    DHCP_STATUS="false"

    if [ -f "$OPTIONS_FILE" ]; then
        DHCP_STATUS="$(python3 -c "import json; print(str(json.load(open('$OPTIONS_FILE')).get('dhcp_enabled', False)).lower())" 2>/dev/null || echo "false")"
    fi

    echo "DHCP:              $DHCP_STATUS"

    if sudo -n true 2>/dev/null; then
        echo -e "Sudo:              ${GREEN}Без пароля${NC}"
    else
        echo -e "Sudo:              ${YELLOW}Требует пароль${NC}"
    fi

    echo "Wayland:           ${WAYLAND_DISPLAY:-нет}"
    echo "X11:               ${DISPLAY:-нет}"
    echo "XDG_RUNTIME_DIR:   ${XDG_RUNTIME_DIR:-нет}"

    DESKTOP_ENTRY="$REAL_HOME/Desktop/MacFU.desktop"
    if [ -f "$DESKTOP_ENTRY" ]; then
        echo -e "Desktop:           ${GREEN}Ярлык присутствует${NC} ($DESKTOP_ENTRY)"
    else
        echo -e "Desktop:           ${YELLOW}Ярлык отсутствует${NC}"
    fi

    exit 0
fi

# Создание виртуального окружения
if [ ! -x "$VENV_PYTHON" ]; then
    [ ! -e "$VENV" ] || [ -d "$VENV" ] || error "По пути $VENV находится файл."

    info "Создание виртуального окружения: $VENV"
    python3 -m venv "$VENV" || error "Не удалось создать venv. Установите пакет venv для Python."

    "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null 2>&1 || true
    log "Venv создан"
else
    log "Используем venv: $VENV"
fi

# Проверка pip
if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    warn "pip отсутствует. Выполняется ensurepip."
    "$VENV_PYTHON" -m ensurepip --default-pip >/dev/null 2>&1 || true
fi

# 7. Проверка и установка зависимостей
MISSING_MODS=""
if ! MISSING_MODS="$(check_dependencies)"; then
    NEED_INSTALL=true
else
    NEED_INSTALL=false
fi

HASH_FILE="$VENV/.macfu_requirements_hash"
CURRENT_HASH=""
OLD_HASH=""

if [ -n "$REQUIREMENTS" ]; then
    CURRENT_HASH="$(sha256sum "$REQUIREMENTS" 2>/dev/null | awk '{print $1}')"
    [ -f "$HASH_FILE" ] && OLD_HASH="$(cat "$HASH_FILE" 2>/dev/null || true)"
    [ "$CURRENT_HASH" != "$OLD_HASH" ] && NEED_INSTALL=true
fi

[ "$FORCE_REINSTALL" = true ] && NEED_INSTALL=true

if [ "$NEED_INSTALL" = true ]; then
    if [ -n "$REQUIREMENTS" ]; then
        [ -n "$MISSING_MODS" ] && warn "Отсутствуют модули: $MISSING_MODS"
        info "Установка зависимостей: $REQUIREMENTS"

        if "$VENV_PYTHON" -m pip install --timeout 10 --retries 1 -r "$REQUIREMENTS"; then
            printf '%s\n' "$CURRENT_HASH" > "$HASH_FILE" 2>/dev/null || true
            log "Зависимости установлены"
        elif check_dependencies >/dev/null 2>&1; then
            warn "pip завершился с ошибкой, но необходимые модули доступны"
        else
            error "Не удалось установить зависимости: $MISSING_MODS"
        fi
    else
        warn "requirements.txt не найден"
    fi
else
    log "Зависимости актуальны"
fi

# 8. DHCP и capabilities
OPTIONS_FILE="$PROJECT_DIR/Data/options.json"
DHCP_ENABLED="true"

if [ -f "$OPTIONS_FILE" ]; then
    DHCP_ENABLED="$(python3 -c "import json; print(str(json.load(open('$OPTIONS_FILE')).get('dhcp_enabled', True)).lower())" 2>/dev/null || echo "true")"
fi

# Настройка capabilities отдельно
if [ "$SETUP_CAPS_ONLY" = true ]; then
    apply_net_caps && { log "DHCP capabilities настроены"; exit 0; }
    error "Настройка capabilities не удалась"
fi

# Определение режима запуска
RUN_MODE="user"

if [ "$FORCE_ROOT" = true ]; then
    RUN_MODE="sudo"
elif [ "$(id -u)" -eq 0 ]; then
    RUN_MODE="direct_root"
elif [ "$FORCE_USER" = true ]; then
    RUN_MODE="user"
elif [ "$DHCP_ENABLED" = "true" ]; then
    if has_net_caps; then
        APP_PYTHON="$CAP_PYTHON"
        log "DHCP: используются capabilities"
    elif apply_net_caps; then
        APP_PYTHON="$CAP_PYTHON"
        log "DHCP: capabilities настроены автоматически"
    else
        APP_PYTHON="$VENV_PYTHON"
        warn "DHCP: запуск без capabilities"
    fi
fi

# 9. Создание/обновление ярлыка
if [ -f "$PROJECT_DIR/create_desktop.sh" ]; then
    REAL_HOME="$REAL_HOME" REAL_USER="$REAL_USER" "$PROJECT_DIR/create_desktop.sh" --quiet 2>/dev/null || true
fi

# 10. Восстановление прав
cleanup_project_ownership() {
    if [ "$(id -u)" -eq 0 ] && [ -n "$REAL_USER" ] && [ "$REAL_USER" != "root" ]; then
        local target_group
        target_group="$(id -g "$REAL_USER" 2>/dev/null || echo "$REAL_USER")"

        chown -R "$REAL_USER:$target_group" "$PROJECT_DIR/Data" "$PROJECT_DIR/eeprom_files" "$PROJECT_DIR/__pycache__" "$VENV" 2>/dev/null || true
    fi
}

trap cleanup_project_ownership EXIT INT TERM

# 11. Настройка сетевого интерфейса DHCP
if [ "$DHCP_ENABLED" = "true" ] && [ -f "$OPTIONS_FILE" ]; then
    DHCP_IFACE="$(python3 -c "import json; print(json.load(open('$OPTIONS_FILE')).get('dhcp_interface', ''))" 2>/dev/null || echo "")"
    DHCP_SERVER_IP="$(python3 -c "import json; print(json.load(open('$OPTIONS_FILE')).get('dhcp_server_ip', '15.15.15.1'))" 2>/dev/null || echo "15.15.15.1")"
    DHCP_MASK="$(python3 -c "import json; print(json.load(open('$OPTIONS_FILE')).get('dhcp_subnet_mask', '255.255.255.0'))" 2>/dev/null || echo "255.255.255.0")"

    if [ -n "$DHCP_IFACE" ]; then
        if ! ip link show "$DHCP_IFACE" >/dev/null 2>&1; then
            warn "Интерфейс не найден: $DHCP_IFACE"
        else
            IFACE_STATE="$(ip link show "$DHCP_IFACE" 2>/dev/null | awk '/state/{print $9}')"
            IFACE_IP="$(ip -4 addr show "$DHCP_IFACE" 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1)"

            if [ "$IFACE_STATE" != "UP" ]; then
                info "Поднимаем интерфейс $DHCP_IFACE"
                run_privileged ip link set "$DHCP_IFACE" up && log "Интерфейс поднят" || warn "Не удалось поднять интерфейс"
            fi

            if [ "$IFACE_IP" != "$DHCP_SERVER_IP" ]; then
                info "Настройка IP $DHCP_SERVER_IP/$DHCP_MASK"
                run_privileged ip addr add "$DHCP_SERVER_IP/$DHCP_MASK" dev "$DHCP_IFACE" && log "IP добавлен на $DHCP_IFACE" || warn "Не удалось добавить IP (возможно, уже настроен)"
            fi
        fi
    fi
fi

# 12. Запуск приложения
log "Запуск Mac Flash Utility"
_log_file "INFO" "Starting application: $RUN_MODE"
log "Режим: $RUN_MODE"
log "Venv: $VENV"
log "Python: $APP_PYTHON"

if [ "$RUN_MODE" = "direct_root" ]; then
    if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -n "$REAL_USER" ]; then
        USER_UID="$(id -u "$REAL_USER" 2>/dev/null || echo 1000)"
        [ -d "/run/user/$USER_UID" ] && export XDG_RUNTIME_DIR="/run/user/$USER_UID"
    fi

    "$APP_PYTHON" "$PROJECT_DIR/run.py" "${APP_ARGS[@]}"
    APP_EXIT_CODE=$?
    cleanup_project_ownership
    exit "$APP_EXIT_CODE"

elif [ "$RUN_MODE" = "sudo" ]; then
    sudo -E "$VENV_PYTHON" "$PROJECT_DIR/run.py" "${APP_ARGS[@]}"
    APP_EXIT_CODE=$?
    cleanup_project_ownership
    exit "$APP_EXIT_CODE"

else
    exec "$APP_PYTHON" "$PROJECT_DIR/run.py" "${APP_ARGS[@]}"
fi