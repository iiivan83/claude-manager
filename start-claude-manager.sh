#!/bin/bash
set -uo pipefail

# Обёртка для запуска Claude Manager через launchd с retry-логикой.
#
# Проблема: Python 3.13 иногда крэшится при инициализации с
# "Fatal Python error: error evaluating path" / InterruptedError EINTR
# в getpath.py — PEP 475 (автоматический retry EINTR) ещё не активен
# на стадии "core initialized". Без обёртки единственный retry —
# ThrottleInterval=60 от launchd, что при transient crashes даёт
# простои 60-80 минут.
#
# Логика:
# 1. Запускает Python, замеряет время работы
# 2. Если крэш в первые STARTUP_CRASH_THRESHOLD_SECONDS — retry
#    (до MAX_RETRIES попыток с паузой RETRY_DELAY_SECONDS)
# 3. Если Python работал дольше порога — это не startup crash,
#    выходим с его exit code (launchd перезапустит через KeepAlive)
# 4. Если все retry исчерпаны — уведомление в Telegram / macOS

PROJECT_DIR="/Users/ivan/Desktop/claude-sandbox/claude_manager"
PYTHON_BINARY="${PROJECT_DIR}/.venv/bin/python"
ENV_FILE="${PROJECT_DIR}/.env"
ERROR_LOG_FILE="$HOME/Library/Logs/claude-manager.error.log"

MAX_RETRIES=3
STARTUP_CRASH_THRESHOLD_SECONDS=5
RETRY_DELAY_SECONDS=10
CURL_TIMEOUT_SECONDS=10

# --- Функции ---

log_message() {
    echo "[start-claude-manager] $1" >&2
}

# Читает значение переменной из .env файла.
# Аргумент: имя переменной (например TELEGRAM_BOT_TOKEN).
read_env_variable() {
    local variable_name="$1"
    if [[ ! -f "$ENV_FILE" ]]; then
        return 1
    fi
    grep "^${variable_name}=" "$ENV_FILE" | head -1 | cut -d'=' -f2-
}

# Отправляет уведомление о crash loop в Telegram.
# Если Telegram недоступен — fallback на macOS notification.
send_crash_notification() {
    local bot_token
    local allowed_user_ids
    local first_chat_id
    local notification_text="⚠️ Claude Manager не может запуститься (${MAX_RETRIES} попытки). Проверь логи: ~/Library/Logs/claude-manager.error.log"

    bot_token=$(read_env_variable "TELEGRAM_BOT_TOKEN")
    allowed_user_ids=$(read_env_variable "ALLOWED_USER_IDS")

    if [[ -z "$bot_token" || -z "$allowed_user_ids" ]]; then
        log_message "не удалось прочитать TELEGRAM_BOT_TOKEN или ALLOWED_USER_IDS из ${ENV_FILE}"
        send_macos_notification "$notification_text"
        return
    fi

    # Первый ID из списка (ALLOWED_USER_IDS=123456789,987654321 → 123456789)
    first_chat_id="${allowed_user_ids%%,*}"

    log_message "отправляю уведомление в Telegram (chat_id=${first_chat_id})..."

    local curl_exit_code
    curl --silent --max-time "$CURL_TIMEOUT_SECONDS" \
        --data-urlencode "chat_id=${first_chat_id}" \
        --data-urlencode "text=${notification_text}" \
        "https://api.telegram.org/bot${bot_token}/sendMessage" \
        > /dev/null 2>&1
    curl_exit_code=$?

    if [[ $curl_exit_code -ne 0 ]]; then
        log_message "curl завершился с кодом ${curl_exit_code}, fallback на macOS notification"
        send_macos_notification "$notification_text"
    else
        log_message "уведомление в Telegram отправлено"
    fi
}

# Показывает системное уведомление macOS (fallback когда Telegram недоступен).
send_macos_notification() {
    local text="$1"
    osascript -e "display notification \"${text}\" with title \"Claude Manager\"" 2>/dev/null || true
    log_message "macOS notification отправлено"
}

# Запускает Python-процесс бота, возвращает его exit code.
run_bot_process() {
    "$PYTHON_BINARY" -c \
        'import sys; sys.path.insert(0, "/Users/ivan/Desktop/claude-sandbox/claude_manager/src"); import runpy; runpy._run_module_as_main("claude_manager")'
    return $?
}

# --- Основной цикл ---

attempt=1

while [[ $attempt -le $MAX_RETRIES ]]; do
    log_message "attempt ${attempt}/${MAX_RETRIES}..."

    start_timestamp=$(date +%s)

    run_bot_process
    python_exit_code=$?

    end_timestamp=$(date +%s)
    runtime_seconds=$((end_timestamp - start_timestamp))

    log_message "Python завершился с кодом ${python_exit_code} после ${runtime_seconds} секунд"

    # Если процесс работал дольше порога — это не startup crash.
    # Выходим с его exit code, launchd перезапустит через KeepAlive.
    if [[ $runtime_seconds -ge $STARTUP_CRASH_THRESHOLD_SECONDS ]]; then
        log_message "runtime (${runtime_seconds}s) >= порог (${STARTUP_CRASH_THRESHOLD_SECONDS}s) — не startup crash, выхожу"
        exit "$python_exit_code"
    fi

    # Startup crash — retry если есть попытки
    if [[ $attempt -lt $MAX_RETRIES ]]; then
        log_message "startup crash (${runtime_seconds}s < ${STARTUP_CRASH_THRESHOLD_SECONDS}s), жду ${RETRY_DELAY_SECONDS}s перед retry..."
        sleep "$RETRY_DELAY_SECONDS"
    fi

    attempt=$((attempt + 1))
done

# Все retry исчерпаны
log_message "все ${MAX_RETRIES} попытки исчерпаны — crash loop"
send_crash_notification
exit 1
