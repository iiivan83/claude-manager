#!/bin/bash
set -euo pipefail

# Безопасный рестарт claude-manager через launchd.
# Выполняет preflight-проверку (editable install жив?), kickstart, post-flight (сервис поднялся?).
# Exit 0 = успех, exit 1 = провал с диагностикой.
# ВАЖНО: скрипт предназначен для ВНЕШНЕГО вызова (терминал, другой агент).
# НЕ вызывайте из подпроцесса самого бота (Claude Code Bash tool) —
# скрипт убьёт собственное дерево процессов. Для самоперезапуска: /restart.

SERVICE_LABEL="com.ivan.claude-manager"
PROJECT_DIR="/Users/ivan/Desktop/claude-sandbox/claude_manager"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"
LOG_FILE="$HOME/Library/Logs/claude-manager.log"
# launchd stderr — только необработанные крэши Python; основные логи — в LOG_FILE
ERROR_LOG_FILE="$HOME/Library/Logs/claude-manager.error.log"

POST_FLIGHT_CHECK_COUNT=3
POST_FLIGHT_CHECK_INTERVAL_SECONDS=10
LOG_TAIL_LINES=20

launchctl_service_pid_from_status() {
    local service_status="$1"
    awk '{print $1}' <<< "$service_status"
}

launchctl_service_exit_code_from_status() {
    local service_status="$1"
    awk '{print $2}' <<< "$service_status"
}

launchctl_service_has_running_pid() {
    local service_status="$1"
    local service_pid

    service_pid=$(launchctl_service_pid_from_status "$service_status")
    [[ "$service_pid" =~ ^[0-9]+$ ]]
}

process_table_has_claude_manager_python_child() {
    local wrapper_pid="$1"
    local process_table="$2"

    awk -v wrapper_pid="$wrapper_pid" '
        $1 == wrapper_pid && /runpy\._run_module_as_main\("claude_manager"\)/ {
            found = 1
        }
        END { exit found ? 0 : 1 }
    ' <<< "$process_table"
}

claude_manager_python_child_is_running() {
    local wrapper_pid="$1"
    local process_table

    process_table=$(ps -axo ppid=,command=)
    process_table_has_claude_manager_python_child "$wrapper_pid" "$process_table"
}

if [[ "${CLAUDE_MANAGER_RESTART_SOURCE_ONLY:-0}" == "1" ]]; then
    return 0 2>/dev/null || exit 0
fi

# Защита от вызова изнутри бота: скрипт убьёт собственное дерево процессов
BOT_PID=$(launchctl list | awk "/$SERVICE_LABEL/ {print \$1}")
if [[ -n "$BOT_PID" && "$BOT_PID" != "-" ]]; then
    CURRENT_PID=$$
    ANCESTOR_PID=$CURRENT_PID
    while [[ "$ANCESTOR_PID" -gt 1 ]]; do
        ANCESTOR_PID=$(ps -o ppid= -p "$ANCESTOR_PID" 2>/dev/null | tr -d ' ')
        if [[ "$ANCESTOR_PID" == "$BOT_PID" ]]; then
            echo "FAIL: скрипт вызван изнутри процесса бота (PID $BOT_PID)."
            echo "Бот не может перезапустить себя из своего собственного процесса."
            echo "Запустите скрипт из терминала или из другого агента."
            exit 2
        fi
    done
fi

echo "=== Preflight ==="

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "FAIL: Python не найден: $VENV_PYTHON"
    exit 1
fi

if ! "$VENV_PYTHON" -c "import sys; sys.path.insert(0, '${PROJECT_DIR}/src'); import claude_manager" 2>&1; then
    echo ""
    echo "FAIL: модуль claude_manager не импортируется."
    echo "Починка: cd $PROJECT_DIR && source .venv/bin/activate && pip install -e ."
    exit 1
fi

echo "OK: модуль claude_manager импортируется"

if ! "$VENV_PYTHON" -c "import sys; sys.path.insert(0, '${PROJECT_DIR}/src'); import claude_manager; from claude_manager.config import load_config; load_config()" 2>&1; then
    echo ""
    echo "WARN: конфиг не загружается (возможно, .env отсутствует или невалиден)"
fi

echo ""
echo "=== Kickstart ==="

launchctl kickstart -k "gui/$(id -u)/${SERVICE_LABEL}" 2>&1
echo "kickstart отправлен, запускаю post-flight проверку (${POST_FLIGHT_CHECK_COUNT} попыток с интервалом ${POST_FLIGHT_CHECK_INTERVAL_SECONDS}с)..."

echo ""
echo "=== Post-flight ==="

POST_FLIGHT_OK=false
SERVICE_PID="-"
EXIT_CODE="unknown"

for CHECK_NUMBER in $(seq 1 "$POST_FLIGHT_CHECK_COUNT"); do
    sleep "$POST_FLIGHT_CHECK_INTERVAL_SECONDS"

    SERVICE_STATUS=$(launchctl list | grep "$SERVICE_LABEL" || true)

    # Сервис исчез из launchctl — launchd потерял его, ретраить бессмысленно
    if [[ -z "$SERVICE_STATUS" ]]; then
        echo "FAIL: сервис $SERVICE_LABEL не найден в launchctl list"
        echo "Последние строки error.log:"
        tail -"$LOG_TAIL_LINES" "$ERROR_LOG_FILE" 2>/dev/null || echo "(лог не найден)"
        exit 1
    fi

    SERVICE_PID=$(launchctl_service_pid_from_status "$SERVICE_STATUS")
    EXIT_CODE=$(launchctl_service_exit_code_from_status "$SERVICE_STATUS")

    # Живой PID wrapper-а ещё не значит, что Python-бот уже поднялся.
    if launchctl_service_has_running_pid "$SERVICE_STATUS" \
        && claude_manager_python_child_is_running "$SERVICE_PID"; then
        echo "OK: сервис в launchctl list, PID = $SERVICE_PID, Python-бот запущен, last exit code = $EXIT_CODE (попытка $CHECK_NUMBER/$POST_FLIGHT_CHECK_COUNT)"
        POST_FLIGHT_OK=true
        break
    fi

    # Wrapper может переживать retry-паузу после startup crash — ждём Python-процесс.
    if [[ "$CHECK_NUMBER" -lt "$POST_FLIGHT_CHECK_COUNT" ]]; then
        echo "WARN: попытка $CHECK_NUMBER/$POST_FLIGHT_CHECK_COUNT, PID = $SERVICE_PID, exit code $EXIT_CODE — Python-бот ещё не найден, жду следующей проверки..."
    fi
done

if [[ "$POST_FLIGHT_OK" != "true" ]]; then
    echo "FAIL: сервис не поднялся после $POST_FLIGHT_CHECK_COUNT проверок, последний PID = $SERVICE_PID, последний exit code = $EXIT_CODE"
    echo ""
    echo "Последние строки error.log:"
    tail -"$LOG_TAIL_LINES" "$ERROR_LOG_FILE" 2>/dev/null || echo "(лог не найден)"
    exit 1
fi

echo ""
echo "Последние строки лога:"
tail -"$LOG_TAIL_LINES" "$LOG_FILE" 2>/dev/null || echo "(лог не найден)"

echo ""
echo "=== Рестарт завершён успешно ==="
