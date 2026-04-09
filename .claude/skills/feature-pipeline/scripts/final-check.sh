#!/usr/bin/env bash
# Финальная проверка перед коммитом
# Прогоняет все уровни тестов и валидирует состояние рабочей копии
# Использование: ./final-check.sh [project-path] [scale] [report-output-path]

set -euo pipefail

PROJECT_PATH="${1:-.}"
SCALE="${2:-medium}"
REPORT_OUTPUT="${3:-test-reports/final-check-report.json}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT_PATH"

mkdir -p "$(dirname "$REPORT_OUTPUT")"

UNIT_EXIT=0
INTEGRATION_EXIT=0
E2E_EXIT=0

echo "=== Финальная проверка ==="
echo "Масштаб задачи: $SCALE"
echo ""

# 1. Юнит-тесты (всегда)
echo "--- Юнит-тесты ---"
"$SCRIPT_DIR/run-unit-tests.sh" "$PROJECT_PATH" "test-reports/final-unit-report.json" || UNIT_EXIT=$?

# 2. Интеграционные тесты (если масштаб >= средний)
if [ "$SCALE" = "medium" ] || [ "$SCALE" = "large" ]; then
  echo ""
  echo "--- Интеграционные тесты ---"
  "$SCRIPT_DIR/run-integration-tests.sh" "$PROJECT_PATH" "test-reports/final-integration-report.json" || INTEGRATION_EXIT=$?
else
  echo "Интеграционные тесты пропущены (масштаб: $SCALE)"
fi

# 3. E2E тесты (если масштаб >= средний)
if [ "$SCALE" = "medium" ] || [ "$SCALE" = "large" ]; then
  echo ""
  echo "--- E2E тесты ---"
  "$SCRIPT_DIR/run-e2e-tests.sh" "$PROJECT_PATH" "test-reports/final-e2e-report.json" || E2E_EXIT=$?
else
  echo "E2E тесты пропущены (масштаб: $SCALE)"
fi

# 4. Проверка состояния git
echo ""
echo "--- Состояние git ---"
UNCOMMITTED=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
echo "Ветка: $BRANCH"
echo "Незакоммиченных изменений: $UNCOMMITTED"

# Формируем итоговый отчёт
OVERALL_PASS=true
if [ "$UNIT_EXIT" -ne 0 ] || [ "$INTEGRATION_EXIT" -ne 0 ] || [ "$E2E_EXIT" -ne 0 ]; then
  OVERALL_PASS=false
fi

cat > "$REPORT_OUTPUT" << EOF
{
  "type": "final-check",
  "scale": "$SCALE",
  "overall_pass": $OVERALL_PASS,
  "unit_tests": {"exit_code": $UNIT_EXIT, "passed": $([ "$UNIT_EXIT" -eq 0 ] && echo true || echo false)},
  "integration_tests": {"exit_code": $INTEGRATION_EXIT, "passed": $([ "$INTEGRATION_EXIT" -eq 0 ] && echo true || echo false), "skipped": $([ "$SCALE" = "small" ] && echo true || echo false)},
  "e2e_tests": {"exit_code": $E2E_EXIT, "passed": $([ "$E2E_EXIT" -eq 0 ] && echo true || echo false), "skipped": $([ "$SCALE" = "small" ] && echo true || echo false)},
  "git": {"branch": "$BRANCH", "uncommitted_changes": $UNCOMMITTED}
}
EOF

echo ""
echo "=== Итог ==="
echo "Все тесты прошли: $OVERALL_PASS"
echo "Отчёт: $REPORT_OUTPUT"

if [ "$OVERALL_PASS" = "false" ]; then
  exit 1
fi
