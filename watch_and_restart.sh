#!/bin/bash
# Скрипт следит за изменениями в .py файлах бота и перезапускает его.
# Запуск: ./watch_and_restart.sh
# Остановка: Ctrl+C

# Корень проекта — папка, где лежит этот скрипт
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SRC_DIR="$PROJECT_DIR/src/claude_manager"
# Глобальный lock-файл — тот же, что использует Python-код бота
PID_FILE="$HOME/.claude-manager.lock"

# Интервал проверки изменений (в секундах)
CHECK_INTERVAL_SECONDS=2

# Цвета для читаемости в терминале
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${RESET} $(date '+%H:%M:%S') $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${RESET} $(date '+%H:%M:%S') $1"
}

log_error() {
    echo -e "${RED}[ERROR]${RESET} $(date '+%H:%M:%S') $1"
}

# Остановить бота по PID
stop_bot() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log_info "Останавливаю бота (PID: $pid)..."
            kill "$pid" 2>/dev/null
            # Ждём завершения (максимум 5 секунд)
            for i in 1 2 3 4 5; do
                if ! kill -0 "$pid" 2>/dev/null; then
                    break
                fi
                sleep 1
            done
            # Если всё ещё работает — принудительно
            if kill -0 "$pid" 2>/dev/null; then
                log_warn "Бот не остановился, принудительное завершение..."
                kill -9 "$pid" 2>/dev/null
            fi
        fi
    fi
}

# Запустить бота
start_bot() {
    log_info "Запускаю бота..."
    cd "$PROJECT_DIR" || exit 1
    python3 -m claude_manager &
    local new_pid=$!
    echo "$new_pid" > "$PID_FILE"
    log_info "Бот запущен (PID: $new_pid)"
}

# Получить «отпечаток» файлов — объединённые даты изменения всех .py файлов
get_files_fingerprint() {
    find "$SRC_DIR" -name "*.py" -exec stat -f "%m %N" {} \; 2>/dev/null | sort
}

# При завершении скрипта — остановить бота
cleanup() {
    log_info "Завершение работы наблюдателя..."
    stop_bot
    exit 0
}
trap cleanup SIGINT SIGTERM

# --- Старт ---
log_info "Наблюдатель запущен. Слежу за папкой: $SRC_DIR"
log_info "Нажмите Ctrl+C для остановки"
echo ""

# Остановить текущий бот (если запущен) и запустить заново
stop_bot
start_bot

# Запомнить начальное состояние файлов
previous_fingerprint=$(get_files_fingerprint)

# Бесконечный цикл проверки изменений
while true; do
    sleep "$CHECK_INTERVAL_SECONDS"

    current_fingerprint=$(get_files_fingerprint)

    if [ "$current_fingerprint" != "$previous_fingerprint" ]; then
        echo ""
        log_warn "Обнаружены изменения в файлах! Перезапускаю бота..."
        stop_bot
        start_bot
        previous_fingerprint=$(get_files_fingerprint)
        echo ""
    fi
done
