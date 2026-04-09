#!/usr/bin/env bash
# Запуск интеграционных тестов проекта
# Ищет тесты с паттернами integration/integ в имени файла или папки
# Использование: ./run-integration-tests.sh [project-path] [report-output-path]

set -euo pipefail

PROJECT_PATH="${1:-.}"
REPORT_OUTPUT="${2:-test-reports/integration-test-report.json}"

cd "$PROJECT_PATH"

mkdir -p "$(dirname "$REPORT_OUTPUT")"

EXIT_CODE=0

# Определяем тип проекта и ищем интеграционные тесты
if [ -f "package.json" ]; then
  # Node.js — ищем integration тесты
  if grep -q '"test:integration"' package.json 2>/dev/null; then
    npm run test:integration 2>&1 || EXIT_CODE=$?
  elif grep -q '"jest"' package.json 2>/dev/null; then
    # Jest — запуск тестов из папки integration или с суффиксом .integration
    npx jest --testPathPattern="integration|integ" --json --outputFile="$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
  elif grep -q '"vitest"' package.json 2>/dev/null; then
    npx vitest run --reporter=json --outputFile="$REPORT_OUTPUT" "**/*integration*" "**/*integ*" 2>&1 || EXIT_CODE=$?
  else
    echo "Скрипт интеграционных тестов не найден в package.json"
    EXIT_CODE=0
  fi
elif [ -f "pytest.ini" ] || [ -f "pyproject.toml" ] || [ -f "setup.py" ]; then
  # Python — запуск тестов из папки integration или с маркером
  if python3 -m pytest --collect-only -m integration 2>/dev/null | grep -q "test"; then
    python3 -m pytest -m integration --tb=short -q 2>&1 || EXIT_CODE=$?
  elif [ -d "tests/integration" ]; then
    python3 -m pytest tests/integration --tb=short -q 2>&1 || EXIT_CODE=$?
  else
    echo "Интеграционные тесты не найдены"
    EXIT_CODE=0
  fi
elif [ -f "go.mod" ]; then
  # Go — запуск тестов с тегом integration
  go test ./... -tags=integration -json > "$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
else
  echo "Тип проекта не определён для интеграционных тестов"
  EXIT_CODE=0
fi

# Создаём отчёт если его нет
if [ ! -f "$REPORT_OUTPUT" ]; then
  echo "{\"type\":\"integration\",\"exit_code\":$EXIT_CODE}" > "$REPORT_OUTPUT"
fi

echo "Интеграционные тесты завершены с кодом: $EXIT_CODE"
echo "Отчёт сохранён в: $REPORT_OUTPUT"

exit $EXIT_CODE
