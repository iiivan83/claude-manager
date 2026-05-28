#!/bin/bash
set -e

# Безопасный рестарт claude-manager через systemd.
# Выполняет preflight (editable install здоров?), systemctl restart, post-flight
# (сервис активен + Python-бот запущен?). Exit 0 — успех, exit 1 — провал с диагностикой.
# ВАЖНО: только для ВНЕШНЕГО вызова (терминал). Самоперезапуск из бота — /restart.

SERVICE_NAME="claude-manager.service"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
VENV_BIN="${PROJECT_DIR}/.venv/bin"
LOG_FILE="$HOME/.local/state/claude-manager/claude-manager.log"

POST_FLIGHT_CHECK_COUNT=3
POST_FLIGHT_CHECK_INTERVAL_SECONDS=5
LOG_TAIL_LINES=30

# --- Helpers (тестируются отдельно через source-only mode) ---

check_editable_install() {
    local venv_bin="${1:-$VENV_BIN}"

    if [[ ! -x "${venv_bin}/claude-manager" ]]; then
        echo "FAIL: ${venv_bin}/claude-manager не найден или не исполняем"
        return 1
    fi
    if ! "${venv_bin}/python" -c "import claude_manager" 2>&1; then
        echo "FAIL: модуль claude_manager не импортируется"
        "${venv_bin}/python" -c "import sys; print('python:', sys.executable); print('version:', sys.version)" 2>&1
        return 1
    fi
    return 0
}

service_is_running() {
    local service="${1:-$SERVICE_NAME}"
    systemctl --user is-active --quiet "$service" \
        && pgrep -f "claude_manager" >/dev/null 2>&1
}

print_diagnostics_on_failure() {
    local service="${1:-$SERVICE_NAME}"
    local log_file="${2:-$LOG_FILE}"

    echo ""
    echo "=== Последние ${LOG_TAIL_LINES} строк ${log_file} ==="
    tail -"$LOG_TAIL_LINES" "$log_file" 2>/dev/null || echo "(лог не найден)"
    echo ""
    echo "=== Последние ${LOG_TAIL_LINES} строк journalctl ==="
    journalctl --user -u "$service" -n "$LOG_TAIL_LINES" --no-pager 2>/dev/null \
        || echo "(journal недоступен)"
}

# --- Source-only mode (для тестов: загружаем функции, не запускаем основной поток) ---

if [[ "${CLAUDE_MANAGER_RESTART_SOURCE_ONLY:-0}" == "1" ]]; then
    return 0 2>/dev/null || exit 0
fi

# --- Main flow ---

echo "=== Preflight ==="
if ! check_editable_install; then
    echo ""
    echo "Починка: cd $PROJECT_DIR && source .venv/bin/activate && pip install -e \".[dev]\""
    exit 1
fi
echo "OK: editable install здоров"

echo ""
echo "=== Restart ==="
systemctl --user restart "$SERVICE_NAME"
echo "systemctl --user restart отправлен"

echo ""
echo "=== Post-flight ==="
POST_FLIGHT_OK=false
for CHECK_NUMBER in $(seq 1 "$POST_FLIGHT_CHECK_COUNT"); do
    sleep "$POST_FLIGHT_CHECK_INTERVAL_SECONDS"

    if service_is_running; then
        SERVICE_PID=$(systemctl --user show -p MainPID --value "$SERVICE_NAME")
        echo "OK: $SERVICE_NAME active, MainPID=$SERVICE_PID (попытка $CHECK_NUMBER/$POST_FLIGHT_CHECK_COUNT)"
        POST_FLIGHT_OK=true
        break
    fi

    if [[ "$CHECK_NUMBER" -lt "$POST_FLIGHT_CHECK_COUNT" ]]; then
        echo "WARN: попытка $CHECK_NUMBER/$POST_FLIGHT_CHECK_COUNT — сервис ещё не активен"
    fi
done

if [[ "$POST_FLIGHT_OK" != "true" ]]; then
    echo "FAIL: сервис не поднялся за $POST_FLIGHT_CHECK_COUNT проверок"
    print_diagnostics_on_failure
    exit 1
fi

echo ""
echo "=== Последние строки лога ==="
tail -"$LOG_TAIL_LINES" "$LOG_FILE" 2>/dev/null || echo "(лог не найден)"

echo ""
echo "=== Рестарт завершён успешно ==="
