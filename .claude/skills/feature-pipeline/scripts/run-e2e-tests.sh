#!/usr/bin/env bash
# Запуск E2E (end-to-end) тестов проекта
# Ищет тесты с паттернами e2e/end-to-end в имени файла или папки
# Использование: ./run-e2e-tests.sh [project-path] [report-output-path]

set -euo pipefail

PROJECT_PATH="${1:-.}"
REPORT_OUTPUT="${2:-test-reports/e2e-test-report.json}"

cd "$PROJECT_PATH"

mkdir -p "$(dirname "$REPORT_OUTPUT")"

EXIT_CODE=0

# Определяем тип проекта и ищем E2E тесты
if [ -f "package.json" ]; then
  # Node.js — ищем E2E тесты
  if grep -q '"test:e2e"' package.json 2>/dev/null; then
    npm run test:e2e 2>&1 || EXIT_CODE=$?
  elif grep -q '"cypress"' package.json 2>/dev/null; then
    npx cypress run --reporter json 2>&1 || EXIT_CODE=$?
  elif grep -q '"playwright"' package.json 2>/dev/null; then
    npx playwright test --reporter=json 2>&1 || EXIT_CODE=$?
  elif grep -q '"jest"' package.json 2>/dev/null; then
    npx jest --testPathPattern="e2e|end-to-end" --json --outputFile="$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
  else
    echo "E2E тестовый фреймворк не найден в package.json"
    EXIT_CODE=0
  fi
elif [ -f "pytest.ini" ] || [ -f "pyproject.toml" ] || [ -f "setup.py" ]; then
  # Python — запуск E2E тестов
  if python3 -m pytest --collect-only -m e2e 2>/dev/null | grep -q "test"; then
    python3 -m pytest -m e2e --tb=short -q 2>&1 || EXIT_CODE=$?
  elif [ -d "tests/e2e" ]; then
    python3 -m pytest tests/e2e --tb=short -q 2>&1 || EXIT_CODE=$?
  else
    echo "E2E тесты не найдены"
    EXIT_CODE=0
  fi
elif [ -f "go.mod" ]; then
  # Go — запуск тестов с тегом e2e
  go test ./... -tags=e2e -json > "$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
else
  echo "Тип проекта не определён для E2E тестов"
  EXIT_CODE=0
fi

# Создаём отчёт если его нет
if [ ! -f "$REPORT_OUTPUT" ]; then
  echo "{\"type\":\"e2e\",\"exit_code\":$EXIT_CODE}" > "$REPORT_OUTPUT"
fi

echo "E2E тесты завершены с кодом: $EXIT_CODE"
echo "Отчёт сохранён в: $REPORT_OUTPUT"

exit $EXIT_CODE
