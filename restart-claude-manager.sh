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

    EXIT_CODE=$(echo "$SERVICE_STATUS" | awk '{print $2}')

    # exit code "0" или "-" (процесс ещё работает) — успех
    if [[ "$EXIT_CODE" == "0" || "$EXIT_CODE" == "-" ]]; then
        echo "OK: сервис в launchctl list, exit code = $EXIT_CODE (попытка $CHECK_NUMBER/$POST_FLIGHT_CHECK_COUNT)"
        POST_FLIGHT_OK=true
        break
    fi

    # Ненулевой exit code — процесс крэшнулся, но launchd может перезапустить (ThrottleInterval)
    if [[ "$CHECK_NUMBER" -lt "$POST_FLIGHT_CHECK_COUNT" ]]; then
        echo "WARN: попытка $CHECK_NUMBER/$POST_FLIGHT_CHECK_COUNT, exit code $EXIT_CODE — жду следующей проверки..."
    fi
done

if [[ "$POST_FLIGHT_OK" != "true" ]]; then
    echo "FAIL: сервис не поднялся после $POST_FLIGHT_CHECK_COUNT проверок, последний exit code = $EXIT_CODE"
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
