#!/usr/bin/env bash

# ==============================================================================
#                       MAC FLASH UTILITY (MacFU)
#                          UNIVERSAL LAUNCHER
# ==============================================================================

# Цветовое оформление вывода в терминале
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    CYAN=''
    BLUE=''
    BOLD=''
    NC=''
fi

# ==============================================================================
# 1. Определение директории проекта и пользователя
# ==============================================================================

PROJECT_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
cd "$PROJECT_DIR" || {
    echo "Ошибка: Не удалось перейти в директорию $PROJECT_DIR" >&2
    exit 1
}

# Определение оригинального непривилегированного пользователя и его домашней папки
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || id -u -n)}"
REAL_HOME=""
if [ -n "$REAL_USER" ]; then
    REAL_HOME="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6 || true)"
fi
REAL_HOME="${REAL_HOME:-$HOME}"

LOG_FILE="$PROJECT_DIR/Data/launcher.log"

# Ротация лога запуска (если больше 1 МБ)
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv -f "$LOG_FILE.tmp" "$LOG_FILE" 2>/dev/null || true
fi

_log_file() {
    local level="$1"
    local msg="$2"
    if [ -d "$PROJECT_DIR/Data" ]; then
        printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$msg" >> "$LOG_FILE" 2>/dev/null || true
    fi
}

log() {
    echo -e "${GREEN}[OK]${NC} $1"
    _log_file "INFO" "$1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    _log_file "WARN" "$1"
}

info() {
    echo -e "${CYAN}[INFO]${NC} $1"
    _log_file "INFO" "$1"
}

error() {
    local msg="$1"
    echo -e "${RED}[ERROR]${NC} $msg" >&2
    _log_file "ERROR" "$msg"

    # Если скрипт вызван не из терминала (например, клик по .desktop файлу),
    # показываем графическое окно с ошибкой, чтобы оператор увидел причину
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

# ==============================================================================
# 2. Обработка аргументов командной строки
# ==============================================================================

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

Универсальный скрипт запуска Mac Flash Utility (MacFU).

Опции:
  -h, --help           Показать эту справку и выйти
  -c, --check          Проверить окружение, зависимости и права без запуска GUI
  --setup-caps         Настроить сетевые права (capabilities) для работы DHCP без root
  --reinstall          Принудительно переустановить зависимости из requirements.txt
  --user               Запустить от обычного пользователя (пропустить sudo/root)
  --root               Принудительно запустить через sudo
  --venv <путь>        Использовать указанное виртуальное окружение

Дополнительные аргументы передаются приложению run.py.
EOF
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            show_help
            ;;
        -c|--check)
            DO_CHECK_ONLY=true
            shift
            ;;
        --setup-caps)
            SETUP_CAPS_ONLY=true
            shift
            ;;
        --reinstall)
            FORCE_REINSTALL=true
            shift
            ;;
        --user)
            FORCE_USER=true
            shift
            ;;
        --root)
            FORCE_ROOT=true
            shift
            ;;
        --venv)
            if [ -n "${2:-}" ]; then
                CUSTOM_VENV="$2"
                shift 2
            else
                error "Параметр --venv требует указания пути к окружению"
            fi
            ;;
        --)
            shift
            APP_ARGS+=("$@")
            break
            ;;
        *)
            APP_ARGS+=("$1")
            shift
            ;;
    esac
done

# Вывод баннера в терминале
if [ -t 1 ] && [ "$DO_CHECK_ONLY" = false ]; then
    echo -e "${CYAN}"
    echo "=========================================="
    echo "          MAC FLASH UTILITY"
    echo "          UNIVERSAL LAUNCHER"
    echo "=========================================="
    echo -e "${NC}"
    echo "Проект:       $PROJECT_DIR"
    echo "Пользователь: $REAL_USER (uid: $(id -u "$REAL_USER" 2>/dev/null || id -u))"
    echo "Система:      $(uname -s) $(uname -r)"
    echo "Хост:         $(hostname)"
    echo ""
fi

# ==============================================================================
# 3. Проверка Python
# ==============================================================================

if ! command -v python3 >/dev/null 2>&1; then
    error "Интерпретатор python3 не найден в системе. Установите Python 3."
fi

PY_VER_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)"
PY_VER_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)"

if [ "$PY_VER_MAJOR" -lt 3 ] || ([ "$PY_VER_MAJOR" -eq 3 ] && [ "$PY_VER_MINOR" -lt 9 ]); then
    error "Для работы требуется Python 3.9 или выше. Обнаружен: $(python3 --version 2>&1)"
fi

# ==============================================================================
# 4. Проверка структуры проекта
# ==============================================================================

[ -f "$PROJECT_DIR/run.py" ] || error "Главный файл приложения run.py не найден в $PROJECT_DIR"

# ==============================================================================
# 5. Виртуальное окружение
# ==============================================================================

# Приоритет поиска venv:
# 1. Указанный через --venv или переменную MACFU_VENV
# 2. Локальный внутри проекта: $PROJECT_DIR/.venv
# 3. В домашней папке пользователя: $REAL_HOME/.venv
# 4. Старый локальный venv: $PROJECT_DIR/macenv
VENV=""
if [ -n "$CUSTOM_VENV" ] && [ -d "$CUSTOM_VENV" ]; then
    VENV="$CUSTOM_VENV"
elif [ -n "${MACFU_VENV:-}" ] && [ -d "$MACFU_VENV" ]; then
    VENV="$MACFU_VENV"
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    VENV="$PROJECT_DIR/.venv"
elif [ -x "$REAL_HOME/.venv/bin/python" ]; then
    VENV="$REAL_HOME/.venv"
elif [ -x "$PROJECT_DIR/macenv/bin/python" ]; then
    VENV="$PROJECT_DIR/macenv"
else
    # Если venv ещё не создан нигде:
    if [ -w "$PROJECT_DIR" ]; then
        VENV="$PROJECT_DIR/.venv"
    else
        VENV="$REAL_HOME/.venv"
    fi
fi

VENV_PYTHON="$VENV/bin/python"

# Требования к зависимостям
REQUIREMENTS=""
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    REQUIREMENTS="$PROJECT_DIR/requirements.txt"
elif [ -f "$PROJECT_DIR/scripts/requirements.txt" ]; then
    REQUIREMENTS="$PROJECT_DIR/scripts/requirements.txt"
fi

# Функция проверки импорта зависимостей
check_dependencies() {
    [ -x "$VENV_PYTHON" ] || return 1
    "$VENV_PYTHON" -c "
import sys
modules = ['PySide6', 'psycopg2', 'gspread', 'oauth2client', 'paramiko', 'scp', 'scapy', 'pyroute2']
missing = []
for m in modules:
    try:
        __import__(m)
    except ImportError:
        missing.append(m)
if missing:
    print(','.join(missing))
    sys.exit(1)
sys.exit(0)
" 2>/dev/null
}

# Функция проверки capabilities на исполняемом файле python
has_net_caps() {
    [ -x "$VENV_PYTHON" ] || return 1
    command -v getcap >/dev/null 2>&1 || return 1
    local target
    target="$(readlink -f "$VENV_PYTHON" 2>/dev/null || echo "$VENV_PYTHON")"
    local caps
    caps="$(getcap "$target" 2>/dev/null || true)"
    echo "$caps" | grep -q "cap_net_raw" && echo "$caps" | grep -q "cap_net_admin"
}

# ==============================================================================
# Режим диагностики (--check) — выполняется без изменения файлов и без pip
# ==============================================================================
if [ "$DO_CHECK_ONLY" = true ]; then
    echo ""
    echo -e "${CYAN}=== ДИАГНОСТИКА ОКРУЖЕНИЯ MacFU ===${NC}"
    echo "Системный Python: $(python3 --version 2>&1)"
    echo "Директория проекта: $PROJECT_DIR"
    echo "Пользователь:       $REAL_USER (uid: $(id -u "$REAL_USER" 2>/dev/null || id -u))"
    echo "Домашняя папка:     $REAL_HOME"
    echo "Целевой Venv:       $VENV"

    if [ -x "$VENV_PYTHON" ]; then
        echo -e "Статус Venv:        ${GREEN}Существует и активен${NC} ($("$VENV_PYTHON" --version 2>&1))"
        MISSING_DIAG=""
        if MISSING_DIAG="$(check_dependencies)"; then
            echo -e "Зависимости:        ${GREEN}Все необходимые модули установлены${NC}"
        else
            echo -e "Зависимости:        ${RED}Отсутствуют: $MISSING_DIAG${NC}"
        fi
        if has_net_caps; then
            echo -e "Capabilities:       ${GREEN}cap_net_raw, cap_net_admin настроены (Root не требуется)${NC}"
        else
            echo -e "Capabilities:       ${YELLOW}Не настроены${NC}"
        fi
    else
        echo -e "Статус Venv:        ${YELLOW}Не создан${NC} (будет создан при обычном запуске ./run.sh)"
        echo -e "Зависимости:        ${YELLOW}Ожидают инициализации окружения${NC}"
    fi

    # Проверка DHCP в options.json
    OPTIONS_FILE="$PROJECT_DIR/Data/options.json"
    DHCP_STATUS="false"
    if [ -f "$OPTIONS_FILE" ]; then
        DHCP_STATUS="$(python3 -c "import json; print(str(json.load(open('$OPTIONS_FILE')).get('dhcp_enabled', False)).lower())" 2>/dev/null || echo "false")"
    fi
    echo "DHCP-сервер в опциях: $DHCP_STATUS"

    if sudo -n true 2>/dev/null || ([ -x "$VENV_PYTHON" ] && sudo -n "$VENV_PYTHON" -c "exit(0)" 2>/dev/null); then
        echo -e "Sudo NOPASSWD:      ${GREEN}Доступно без пароля${NC}"
    else
        echo -e "Sudo NOPASSWD:      ${YELLOW}Требует пароль или не настроено${NC}"
    fi

    echo "Wayland сессия:     ${WAYLAND_DISPLAY:-нет}"
    echo "X11 Display:        ${DISPLAY:-нет}"
    echo "XDG_RUNTIME_DIR:    ${XDG_RUNTIME_DIR:-нет}"

    DESKTOP_ENTRY="$REAL_HOME/Desktop/MacFU.desktop"
    if [ -f "$DESKTOP_ENTRY" ]; then
        echo -e "Ярлык на Desktop:   ${GREEN}Присутствует${NC} ($DESKTOP_ENTRY)"
    else
        echo -e "Ярлык на Desktop:   ${YELLOW}Отсутствует${NC}"
    fi
    echo -e "${CYAN}===================================${NC}"
    exit 0
fi

# Создание окружения, если оно не существует
if [ ! -x "$VENV_PYTHON" ]; then
    if [ -e "$VENV" ] && [ ! -d "$VENV" ]; then
        error "По пути $VENV существует файл, не являющийся директорией."
    fi

    info "Виртуальное окружение не найдено. Создаем: $VENV"
    if ! python3 -m venv "$VENV"; then
        error "Не удалось создать виртуальное окружение $VENV. Убедитесь, что установлен пакет python3-venv (sudo apt install python3-venv или аналогичный)."
    fi

    # Обновляем pip один раз только при первичной инициализации окружения (без блокировки при оффлайне)
    "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null 2>&1 || true
    log "Виртуальное окружение создано: $VENV"
else
    log "Используем окружение: $VENV"
fi

# Проверяем наличие pip в venv
if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    warn "pip не обнаружен в $VENV. Пробуем инициализировать через ensurepip..."
    "$VENV_PYTHON" -m ensurepip --default-pip >/dev/null 2>&1 || true
fi

# ==============================================================================
# 6. Проверка и установка зависимостей
# ==============================================================================

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
    if [ "$CURRENT_HASH" != "$OLD_HASH" ]; then
        NEED_INSTALL=true
    fi
fi

if [ "$FORCE_REINSTALL" = true ]; then
    NEED_INSTALL=true
fi

if [ "$NEED_INSTALL" = true ]; then
    if [ -n "$REQUIREMENTS" ]; then
        if [ -n "$MISSING_MODS" ]; then
            warn "Отсутствуют модули: $MISSING_MODS"
        fi
        info "Установка/обновление зависимостей из $REQUIREMENTS..."

        if "$VENV_PYTHON" -m pip install --timeout 10 --retries 1 -r "$REQUIREMENTS"; then
            printf '%s\n' "$CURRENT_HASH" > "$HASH_FILE" 2>/dev/null || true
            log "Зависимости успешно установлены"
        else
            # Если pip завершился с ошибкой (например, устройство оффлайн на стенде)
            if check_dependencies >/dev/null 2>&1; then
                warn "Установка через pip не удалась (нет сети?), но все критические модули уже установлены. Продолжаем запуск."
            else
                error "Не удалось установить зависимости ($MISSING_MODS). Проверьте интернет или установите пакеты вручную."
            fi
        fi
    else
        warn "Файл requirements.txt не найден. Пропуск установки зависимостей."
    fi
else
    log "Зависимости проверены и актуальны"
fi

# ==============================================================================
# 7. Проверка сетевых прав и DHCP (Linux capabilities / Sudo)
# ==============================================================================

OPTIONS_FILE="$PROJECT_DIR/Data/options.json"
DHCP_ENABLED="true"

# Функция проверки capabilities на исполняемом файле python
has_net_caps() {
    command -v getcap >/dev/null 2>&1 || return 1
    local target
    target="$(readlink -f "$VENV_PYTHON" 2>/dev/null || echo "$VENV_PYTHON")"
    local caps
    caps="$(getcap "$target" 2>/dev/null || true)"
    echo "$caps" | grep -q "cap_net_raw" && echo "$caps" | grep -q "cap_net_admin"
}

# Функция назначения capabilities на python внутри venv
apply_net_caps() {
    info "Настройка Linux capabilities для $VENV_PYTHON..."
    # В venv bin/python обычно symlink. setcap не работает на symlink.
    # Превращаем файл в реальный бинарник, если это symlink.
    if [ -L "$VENV_PYTHON" ]; then
        local real_bin
        real_bin="$(readlink -f "$VENV_PYTHON")"
        cp -f --remove-destination "$real_bin" "$VENV_PYTHON"
    fi

    # Пробуем без пароля
    if sudo -n setcap cap_net_raw,cap_net_admin+ep "$VENV_PYTHON" 2>/dev/null; then
        log "Capabilities успешно назначены (cap_net_raw, cap_net_admin)"
        return 0
    fi

    # Если есть терминал, запрашиваем sudo
    if [ -t 1 ]; then
        warn "Для работы DHCP-сервера без запуска всего приложения от root требуется один раз выдать capabilities..."
        if sudo setcap cap_net_raw,cap_net_admin+ep "$VENV_PYTHON"; then
            log "Capabilities успешно назначены"
            return 0
        fi
    elif command -v pkexec >/dev/null 2>&1; then
        # Графический запрос пароля через PolicyKit
        if pkexec setcap cap_net_raw,cap_net_admin+ep "$VENV_PYTHON" 2>/dev/null; then
            log "Capabilities успешно назначены через pkexec"
            return 0
        fi
    fi

    return 1
}

if [ "$SETUP_CAPS_ONLY" = true ]; then
    if apply_net_caps; then
        log "Сетевые права (capabilities) успешно настроены. Теперь DHCP-сервер может работать без root."
        exit 0
    else
        error "Не удалось назначить capabilities."
    fi
fi

# Определение режима запуска: user, sudo, direct_root
RUN_MODE="user"

if [ "$FORCE_USER" = true ]; then
    RUN_MODE="user"
elif [ "$FORCE_ROOT" = true ]; then
    RUN_MODE="sudo"
elif [ "$(id -u)" -eq 0 ]; then
    RUN_MODE="direct_root"
else
    # DHCP всегда включен. Проверяем, есть ли capabilities
    if has_net_caps; then
        RUN_MODE="user"
        log "DHCP активен: используются Linux capabilities (запуск от обычного пользователя)"
    else
        # Пробуем автоматически выставить capabilities, если sudo работает без пароля
        if sudo -n true 2>/dev/null && apply_net_caps; then
            RUN_MODE="user"
            log "DHCP активен: capabilities успешно настроены автоматически"
        else
            # Иначе потребуется запуск через sudo
            RUN_MODE="sudo"
            warn "DHCP активен: capabilities не настроены, требуется запуск через sudo"
        fi
    fi
fi

# ==============================================================================
# 8. Настройка Linux capabilities для DHCP-сервера (если еще не настроены)
# ==============================================================================

if [ "$RUN_MODE" != "user" ] && [ "$RUN_MODE" != "direct_root" ]; then
    if [ -t 1 ]; then
        warn "Настройка Linux capabilities для DHCP-сервера (требуется пароль sudo один раз)..."
        if sudo setcap cap_net_raw,cap_net_admin+ep "$VENV_PYTHON" 2>/dev/null; then
            log "Capabilities настроены. Запуск от обычного пользователя."
            RUN_MODE="user"
        else
            warn "Не удалось настроить capabilities. DHCP может работать с ограничениями."
        fi
    elif command -v pkexec >/dev/null 2>&1; then
        info "Настройка capabilities через pkexec..."
        if pkexec setcap cap_net_raw,cap_net_admin+ep "$VENV_PYTHON" 2>/dev/null; then
            log "Capabilities настроены. Запуск от обычного пользователя."
            RUN_MODE="user"
        else
            warn "Не удалось настроить возможности через pkexec. DHCP может работать с ограничениями."
        fi
    fi
fi

# ==============================================================================
# 9. Создание/обновление ярлыка рабочего стола
# ==============================================================================

if [ -f "$PROJECT_DIR/create_desktop.sh" ]; then
    REAL_HOME="$REAL_HOME" REAL_USER="$REAL_USER" "$PROJECT_DIR/create_desktop.sh" --quiet 2>/dev/null || true
fi

# ==============================================================================
# 10. Восстановление прав на файлы проекта при завершении (если был root)
# ==============================================================================

cleanup_project_ownership() {
    if [ "$(id -u)" -eq 0 ] && [ -n "$REAL_USER" ] && [ "$REAL_USER" != "root" ]; then
        local target_group
        target_group="$(id -g "$REAL_USER" 2>/dev/null || echo "$REAL_USER")"
        chown -R "$REAL_USER:$target_group" \
            "$PROJECT_DIR/Data" \
            "$PROJECT_DIR/eeprom_files" \
            "$PROJECT_DIR/__pycache__" 2>/dev/null || true
    fi
}

trap cleanup_project_ownership EXIT INT TERM

# ==============================================================================
# 10. Настройка сетевого интерфейса для DHCP-сервера (через sudo)
# ==============================================================================

if [ -f "$PROJECT_DIR/Data/options.json" ]; then
    DHCP_IFACE="$(python3 -c "import json; print(json.load(open('$PROJECT_DIR/Data/options.json')).get('dhcp_interface',''))" 2>/dev/null || echo "")"
    DHCP_SERVER_IP="$(python3 -c "import json; print(json.load(open('$PROJECT_DIR/Data/options.json')).get('dhcp_server_ip','15.15.15.1'))" 2>/dev/null || echo "15.15.15.1")"
    DHCP_MASK="$(python3 -c "import json; print(json.load(open('$PROJECT_DIR/Data/options.json')).get('dhcp_subnet_mask','255.255.255.0'))" 2>/dev/null || echo "255.255.255.0")"

    if [ -n "$DHCP_IFACE" ]; then
        # Проверяем, нужно ли поднимать интерфейс и добавлять IP
        IFACE_STATE="$(ip link show "$DHCP_IFACE" 2>/dev/null | awk '/state/{print $9}' || echo "")"
        IFACE_IP="$(ip -4 addr show "$DHCP_IFACE" 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 || echo "")"

        if [ "$IFACE_STATE" != "UP" ]; then
            warn "Поднимаем интерфейс $DHCP_IFACE (требуется пароль sudo)..."
            if sudo ip link set "$DHCP_IFACE" up 2>/dev/null; then
                log "Интерфейс $DHCP_IFACE поднят"
            else
                warn "Не удалось поднять интерфейс $DHCP_IFACE через sudo"
            fi
        fi

        if [ "$IFACE_IP" != "$DHCP_SERVER_IP" ]; then
            warn "Настраиваем IP $DHCP_SERVER_IP/$DHCP_MASK на $DHCP_IFACE (требуется пароль sudo)..."
            if sudo ip addr add "$DHCP_SERVER_IP/$DHCP_MASK" dev "$DHCP_IFACE" 2>/dev/null; then
                log "IP $DHCP_SERVER_IP/$DHCP_MASK добавлен на $DHCP_IFACE"
            else
                warn "Не удалось добавить IP $DHCP_SERVER_IP на $DHCP_IFACE через sudo (возможно, уже настроен)"
            fi
        fi
    fi
fi

log "Запуск Mac Flash Utility в режиме: $RUN_MODE"
_log_file "INFO" "Starting application (mode: $RUN_MODE)"

# ==============================================================================
# 11. Запуск приложения (всегда от обычного пользователя, кроме direct_root)
# ==============================================================================

echo -e "${CYAN}"
echo "=========================================="
echo "          STARTING MAC FLASH UTILITY"
echo "=========================================="
echo -e "${NC}"

log "Режим запуска: $RUN_MODE"
log "Окружение: $VENV"

if [ "$RUN_MODE" = "direct_root" ]; then
    # Если уже запущен напрямую от root, восстанавливаем XDG_RUNTIME_DIR для Wayland, если возможно
    if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -n "$REAL_USER" ]; then
        USER_UID="$(id -u "$REAL_USER" 2>/dev/null || echo 1000)"
        if [ -d "/run/user/$USER_UID" ]; then
            export XDG_RUNTIME_DIR="/run/user/$USER_UID"
        fi
    fi

    "$VENV_PYTHON" "$PROJECT_DIR/run.py" "${APP_ARGS[@]}"
    APP_EXIT_CODE=$?
    cleanup_project_ownership
    exit $APP_EXIT_CODE

else
    # Запуск от обычного пользователя (интерфейс уже настроен через sudo выше)
    exec "$VENV_PYTHON" "$PROJECT_DIR/run.py" "${APP_ARGS[@]}"
fi
