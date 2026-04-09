#!/usr/bin/env bash
# Запуск юнит-тестов проекта
# Автоматически определяет тестовый фреймворк и запускает все юнит-тесты
# Использование: ./run-unit-tests.sh [project-path] [report-output-path]

set -euo pipefail

PROJECT_PATH="${1:-.}"
REPORT_OUTPUT="${2:-test-reports/unit-test-report.json}"

cd "$PROJECT_PATH"

# Определяем тестовый фреймворк по файлам проекта
detect_test_framework() {
  if [ -f "package.json" ]; then
    # Node.js проект — ищем тестовый фреймворк
    if grep -q '"jest"' package.json 2>/dev/null; then
      echo "jest"
    elif grep -q '"vitest"' package.json 2>/dev/null; then
      echo "vitest"
    elif grep -q '"mocha"' package.json 2>/dev/null; then
      echo "mocha"
    elif grep -q '"test"' package.json 2>/dev/null; then
      echo "npm-test"
    else
      echo "none"
    fi
  elif [ -f "pytest.ini" ] || [ -f "pyproject.toml" ] || [ -f "setup.py" ]; then
    if command -v pytest &>/dev/null; then
      echo "pytest"
    elif command -v python3 &>/dev/null; then
      echo "unittest"
    else
      echo "none"
    fi
  elif [ -f "go.mod" ]; then
    echo "go-test"
  elif [ -f "Cargo.toml" ]; then
    echo "cargo-test"
  else
    echo "none"
  fi
}

FRAMEWORK=$(detect_test_framework)
echo "Определён тестовый фреймворк: $FRAMEWORK"

# Убеждаемся что папка для отчёта существует
mkdir -p "$(dirname "$REPORT_OUTPUT")"

EXIT_CODE=0

case "$FRAMEWORK" in
  jest)
    npx jest --json --outputFile="$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
    ;;
  vitest)
    npx vitest run --reporter=json --outputFile="$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
    ;;
  mocha)
    npx mocha --reporter json > "$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
    ;;
  npm-test)
    npm test 2>&1 || EXIT_CODE=$?
    # npm test не всегда генерирует JSON — создаём минимальный отчёт
    if [ ! -f "$REPORT_OUTPUT" ]; then
      echo "{\"framework\":\"npm-test\",\"exit_code\":$EXIT_CODE}" > "$REPORT_OUTPUT"
    fi
    ;;
  pytest)
    python3 -m pytest --tb=short -q --json-report --json-report-file="$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
    if [ ! -f "$REPORT_OUTPUT" ]; then
      # Если плагин json-report не установлен — запуск без него
      python3 -m pytest --tb=short -q 2>&1 || EXIT_CODE=$?
      echo "{\"framework\":\"pytest\",\"exit_code\":$EXIT_CODE}" > "$REPORT_OUTPUT"
    fi
    ;;
  unittest)
    python3 -m unittest discover -s tests -p "test_*.py" 2>&1 || EXIT_CODE=$?
    echo "{\"framework\":\"unittest\",\"exit_code\":$EXIT_CODE}" > "$REPORT_OUTPUT"
    ;;
  go-test)
    go test ./... -json > "$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
    ;;
  cargo-test)
    cargo test -- --format json > "$REPORT_OUTPUT" 2>&1 || EXIT_CODE=$?
    ;;
  none)
    echo "Тестовый фреймворк не определён. Пропуск юнит-тестов."
    echo "{\"framework\":\"none\",\"message\":\"No test framework detected\",\"exit_code\":0}" > "$REPORT_OUTPUT"
    EXIT_CODE=0
    ;;
esac

echo "Тесты завершены с кодом: $EXIT_CODE"
echo "Отчёт сохранён в: $REPORT_OUTPUT"

exit $EXIT_CODE
