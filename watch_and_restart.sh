#!/bin/bash
set -e

# Watcher: при изменении .py файлов в src/ перезапускает бота через systemctl.
# Зависимость: inotify-tools (sudo apt install inotify-tools).
# Запуск: ./watch_and_restart.sh
# Остановка: Ctrl+C

SERVICE_NAME="claude-manager.service"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SRC_DIR="$PROJECT_DIR/src"
DEBOUNCE_SECONDS=1

# --- Цвета для читаемости ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${RESET}  $(date '+%H:%M:%S') $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $(date '+%H:%M:%S') $1"; }
log_error() { echo -e "${RED}[ERROR]${RESET} $(date '+%H:%M:%S') $1"; }

# --- Проверка зависимости ---
if ! command -v inotifywait >/dev/null 2>&1; then
    log_error "Не найдена команда inotifywait."
    echo "  Установите пакет inotify-tools:"
    echo "    sudo apt install inotify-tools     # Debian/Ubuntu"
    echo "    sudo dnf install inotify-tools     # Fedora/RHEL"
    exit 1
fi

log_info "Наблюдатель запущен. Слежу за: $SRC_DIR"
log_info "Сервис: $SERVICE_NAME (Ctrl+C для остановки)"
echo ""

last_restart_timestamp=0

# inotifywait -m печатает события по одному на строку.
# -r — рекурсивно, --include — фильтр regex по полному пути.
# Используем process substitution, чтобы переменная last_restart_timestamp
# жила между итерациями цикла (pipe создаёт subshell).
while read -r event_line; do
    now=$(date +%s)
    since_last=$((now - last_restart_timestamp))

    # Дебаунс: если прошло меньше DEBOUNCE_SECONDS — пропускаем
    if [[ "$since_last" -lt "$DEBOUNCE_SECONDS" ]]; then
        continue
    fi

    log_warn "Изменения: $event_line"
    log_info "Перезапускаю $SERVICE_NAME..."

    if systemctl --user restart "$SERVICE_NAME"; then
        log_info "Сервис перезапущен"
    else
        log_error "Перезапуск $SERVICE_NAME провалился (exit $?)"
    fi

    last_restart_timestamp=$(date +%s)
done < <(inotifywait -m -r -e modify,create,delete --include '.*\.py$' "$SRC_DIR" --format '%w%f %e' 2>/dev/null)
