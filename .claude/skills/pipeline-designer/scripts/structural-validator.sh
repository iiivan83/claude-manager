#!/bin/bash
set -euo pipefail

# =============================================================================
# structural-validator.sh
# Проверка файловой структуры собранного скилла (тест T1)
# Проверяет, что все файлы на месте и ссылки не сломаны
# =============================================================================

# --- Аргументы ---
SKILL_PATH="${1:?Ошибка: укажи путь к папке скилла как первый аргумент}"
REPORTS_DIR="${2:-}"

# --- Временная метка ---
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# --- Счётчики ---
CRITICAL_PASSED=0
CRITICAL_FAILED=0
WARNINGS=0

# --- Массивы результатов ---
CRITICAL_CHECKS="[]"
NON_CRITICAL_CHECKS="[]"

# Функция добавления результата критической проверки
add_critical_check() {
  local check_name="$1"
  local status="$2"
  local details="$3"

  CRITICAL_CHECKS=$(echo "$CRITICAL_CHECKS" | python3 -c "
import sys, json
checks = json.load(sys.stdin)
checks.append({'check': '$check_name', 'status': '$status', 'details': '''$details'''})
print(json.dumps(checks))
")

  if [ "$status" = "PASS" ]; then
    CRITICAL_PASSED=$((CRITICAL_PASSED + 1))
  else
    CRITICAL_FAILED=$((CRITICAL_FAILED + 1))
  fi
}

# Функция добавления результата некритической проверки
add_non_critical_check() {
  local check_name="$1"
  local status="$2"
  local details="$3"

  NON_CRITICAL_CHECKS=$(echo "$NON_CRITICAL_CHECKS" | python3 -c "
import sys, json
checks = json.load(sys.stdin)
checks.append({'check': '$check_name', 'status': '$status', 'details': '''$details'''})
print(json.dumps(checks))
")

  if [ "$status" = "FAIL" ]; then
    WARNINGS=$((WARNINGS + 1))
  fi
}

# =============================================================================
# КРИТИЧЕСКИЕ ПРОВЕРКИ (любой FAIL = общий FAIL)
# =============================================================================

# Проверка 1: SKILL.md существует
if [ -f "${SKILL_PATH}/SKILL.md" ]; then
  add_critical_check "skill_md_exists" "PASS" "SKILL.md найден"
else
  add_critical_check "skill_md_exists" "FAIL" "SKILL.md не найден в ${SKILL_PATH}"
fi

# Проверка 2: Папка agents/ (warning — не все скиллы используют агентов)
if [ -d "${SKILL_PATH}/agents" ]; then
  AGENT_COUNT=$(find "${SKILL_PATH}/agents" -maxdepth 1 -name "*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
  if [ "$AGENT_COUNT" -gt 0 ]; then
    add_non_critical_check "agents_dir_has_files" "PASS" "Папка agents/ содержит ${AGENT_COUNT} .md файлов"
  else
    add_non_critical_check "agents_dir_has_files" "FAIL" "Папка agents/ пуста — нет .md файлов"
  fi
else
  add_non_critical_check "agents_dir_has_files" "FAIL" "Папка agents/ не найдена"
fi

# Проверка 3: references/schemas.json (warning — не все скиллы используют JSON-схемы)
if [ -f "${SKILL_PATH}/references/schemas.json" ]; then
  if python3 -c "import json; json.load(open('${SKILL_PATH}/references/schemas.json'))" 2>/dev/null; then
    add_non_critical_check "schemas_json_valid" "PASS" "schemas.json существует и содержит валидный JSON"
  else
    add_non_critical_check "schemas_json_valid" "FAIL" "schemas.json существует, но содержит невалидный JSON"
  fi
else
  add_non_critical_check "schemas_json_valid" "FAIL" "references/schemas.json не найден"
fi

# =============================================================================
# НЕКРИТИЧЕСКИЕ ПРОВЕРКИ (записываются как предупреждения)
# =============================================================================

# Проверка: YAML-заголовок в SKILL.md валиден
if [ -f "${SKILL_PATH}/SKILL.md" ]; then
  FIRST_LINE=$(head -1 "${SKILL_PATH}/SKILL.md")
  if [ "$FIRST_LINE" = "---" ]; then
    # Проверяем наличие name и description в YAML
    YAML_BLOCK=$(awk 'NR==1{next} /^---$/{exit} {print}' "${SKILL_PATH}/SKILL.md")
    HAS_NAME=$(echo "$YAML_BLOCK" | grep -c "^name:" || true)
    HAS_DESC=$(echo "$YAML_BLOCK" | grep -c "^description:" || true)
    if [ "$HAS_NAME" -gt 0 ] && [ "$HAS_DESC" -gt 0 ]; then
      add_non_critical_check "yaml_frontmatter_valid" "PASS" "YAML-заголовок содержит name и description"
    else
      add_non_critical_check "yaml_frontmatter_valid" "FAIL" "YAML-заголовок не содержит name или description"
    fi
  else
    add_non_critical_check "yaml_frontmatter_valid" "FAIL" "SKILL.md не начинается с YAML-заголовка (---)"
  fi
fi

# Проверка: Папка scripts/ существует
if [ -d "${SKILL_PATH}/scripts" ]; then
  add_non_critical_check "scripts_dir_exists" "PASS" "Папка scripts/ существует"
else
  add_non_critical_check "scripts_dir_exists" "FAIL" "Папка scripts/ не найдена"
fi

# Проверка: .sh файлы имеют право на запуск
if [ -d "${SKILL_PATH}/scripts" ]; then
  NON_EXEC_SCRIPTS=""
  for script in "${SKILL_PATH}"/scripts/*.sh; do
    [ -f "$script" ] || continue
    if [ ! -x "$script" ]; then
      NON_EXEC_SCRIPTS="${NON_EXEC_SCRIPTS} $(basename "$script")"
    fi
  done
  if [ -z "$NON_EXEC_SCRIPTS" ]; then
    add_non_critical_check "scripts_executable" "PASS" "Все .sh файлы имеют право на запуск"
  else
    add_non_critical_check "scripts_executable" "FAIL" "Файлы без права на запуск:${NON_EXEC_SCRIPTS}"
  fi
fi

# Проверка: Папка evals/ существует
if [ -d "${SKILL_PATH}/evals" ]; then
  add_non_critical_check "evals_dir_exists" "PASS" "Папка evals/ существует"
else
  add_non_critical_check "evals_dir_exists" "FAIL" "Папка evals/ не найдена"
fi

# Проверка: В schemas.json есть хотя бы одно определение
if [ -f "${SKILL_PATH}/references/schemas.json" ]; then
  DEFS_COUNT=$(python3 -c "
import json
try:
    data = json.load(open('${SKILL_PATH}/references/schemas.json'))
    defs = data.get('definitions', {})
    print(len(defs))
except:
    print(0)
" 2>/dev/null || echo "0")
  if [ "$DEFS_COUNT" -gt 0 ]; then
    add_non_critical_check "schemas_has_definitions" "PASS" "schemas.json содержит ${DEFS_COUNT} определений"
  else
    add_non_critical_check "schemas_has_definitions" "FAIL" "schemas.json не содержит определений"
  fi
fi

# Проверка: файлы, упомянутые в SKILL.md, реально существуют
if [ -f "${SKILL_PATH}/SKILL.md" ]; then
  MISSING_REFS=""
  MISSING_COUNT=0
  # Ищем ссылки на файлы формата agents/*.md и scripts/*.sh
  REFERENCED_FILES=$(grep -oE '(agents|scripts)/[a-zA-Z0-9_-]+\.(md|sh)' "${SKILL_PATH}/SKILL.md" 2>/dev/null | sort -u || true)
  for ref in $REFERENCED_FILES; do
    if [ ! -f "${SKILL_PATH}/${ref}" ]; then
      MISSING_REFS="${MISSING_REFS} ${ref}"
      MISSING_COUNT=$((MISSING_COUNT + 1))
    fi
  done
  if [ "$MISSING_COUNT" -eq 0 ]; then
    add_non_critical_check "all_references_exist" "PASS" "Все файлы, упомянутые в SKILL.md, существуют"
  else
    add_non_critical_check "all_references_exist" "FAIL" "Не найдены файлы:${MISSING_REFS}"
  fi
fi

# =============================================================================
# ПРОВЕРКИ СООТВЕТСТВИЯ AGENTS.md (все — warnings, не блокируют)
# =============================================================================

# Максимальное количество строк в SKILL.md по правилам AGENTS.md
MAX_SKILL_LINES=1000

# Проверка: SKILL.md не превышает 1000 строк
if [ -f "${SKILL_PATH}/SKILL.md" ]; then
  SKILL_LINE_COUNT=$(wc -l < "${SKILL_PATH}/SKILL.md" | tr -d ' ')
  if [ "$SKILL_LINE_COUNT" -le "$MAX_SKILL_LINES" ]; then
    add_non_critical_check "skill_md_line_limit" "PASS" "SKILL.md содержит ${SKILL_LINE_COUNT} строк (лимит: ${MAX_SKILL_LINES})"
  else
    add_non_critical_check "skill_md_line_limit" "FAIL" "SKILL.md exceeds ${MAX_SKILL_LINES} lines (actual: ${SKILL_LINE_COUNT})"
  fi
fi

# Проверка: пайплайн-скилл содержит упоминание session-report
if [ -f "${SKILL_PATH}/SKILL.md" ]; then
  # Признак пайплайн-скилла: содержит "этап", "phase" или "pipeline"
  IS_PIPELINE=$(grep -c -i -E 'этап|phase|pipeline' "${SKILL_PATH}/SKILL.md" || true)
  if [ "$IS_PIPELINE" -gt 0 ]; then
    HAS_SESSION_REPORT=$(grep -c -i 'session-report' "${SKILL_PATH}/SKILL.md" || true)
    if [ "$HAS_SESSION_REPORT" -gt 0 ]; then
      add_non_critical_check "pipeline_session_report" "PASS" "Пайплайн-скилл содержит упоминание session-report"
    else
      add_non_critical_check "pipeline_session_report" "FAIL" "Pipeline skill missing session-report"
    fi
  else
    add_non_critical_check "pipeline_session_report" "PASS" "Не пайплайн-скилл — проверка session-report не требуется"
  fi
fi

# Проверка: SKILL.md содержит ссылку на writing-style-guide
if [ -f "${SKILL_PATH}/SKILL.md" ]; then
  HAS_WRITING_STYLE=$(grep -c 'writing-style-guide' "${SKILL_PATH}/SKILL.md" || true)
  if [ "$HAS_WRITING_STYLE" -gt 0 ]; then
    add_non_critical_check "writing_style_guide_ref" "PASS" "SKILL.md содержит ссылку на writing-style-guide.md"
  else
    add_non_critical_check "writing_style_guide_ref" "FAIL" "Missing reference to writing-style-guide.md"
  fi
fi

# Проверка: все файлы agents/, на которые ссылается SKILL.md, существуют
if [ -f "${SKILL_PATH}/SKILL.md" ]; then
  MISSING_AGENTS=""
  MISSING_AGENT_COUNT=0
  # Ищем ссылки формата agents/имя-файла.md
  REFERENCED_AGENTS=$(grep -oE 'agents/[a-zA-Z0-9_-]+\.md' "${SKILL_PATH}/SKILL.md" 2>/dev/null | sort -u || true)
  for agent_ref in $REFERENCED_AGENTS; do
    if [ ! -f "${SKILL_PATH}/${agent_ref}" ]; then
      MISSING_AGENTS="${MISSING_AGENTS} ${agent_ref}"
      MISSING_AGENT_COUNT=$((MISSING_AGENT_COUNT + 1))
    fi
  done
  if [ "$MISSING_AGENT_COUNT" -eq 0 ]; then
    add_non_critical_check "agent_files_exist" "PASS" "Все файлы агентов из SKILL.md существуют"
  else
    # Выводим каждый отсутствующий файл в сообщении
    for missing_agent in $MISSING_AGENTS; do
      add_non_critical_check "agent_files_exist" "FAIL" "Referenced agent file not found: ${missing_agent}"
    done
  fi
fi

# Проверка: если есть папка evals/, в ней должен быть evals.json
if [ -d "${SKILL_PATH}/evals" ]; then
  if [ -f "${SKILL_PATH}/evals/evals.json" ]; then
    add_non_critical_check "evals_json_exists" "PASS" "evals/evals.json найден"
  else
    add_non_critical_check "evals_json_exists" "FAIL" "evals/ directory exists but evals.json not found"
  fi
fi

# Проверка: нет литеральных --max-budget-usd <число> в скилле
# Все значения должны подставляться из BUDGET_LIGHT/NORMAL/HEAVY через
# ~/.claude/cli-budgets.env (см. ~/.claude/CLAUDE.md, раздел
# "CLI Subprocess Budgets Rule"). Литералы ломают единый источник истины
# и валятся при любом переезде на другие лимиты.
BUDGET_VIOLATIONS=""
BUDGET_VIOLATION_COUNT=0
for budget_target in "${SKILL_PATH}/SKILL.md" "${SKILL_PATH}"/agents/*.md "${SKILL_PATH}"/scripts/*.sh; do
  [ -f "$budget_target" ] || continue
  BUDGET_MATCHES=$(grep -c -E -- '--max-budget-usd[[:space:]]+[0-9]+' "$budget_target" 2>/dev/null || true)
  BUDGET_MATCHES=${BUDGET_MATCHES:-0}
  if [ "$BUDGET_MATCHES" -gt 0 ]; then
    BUDGET_VIOLATIONS="${BUDGET_VIOLATIONS} $(basename "$budget_target")(${BUDGET_MATCHES})"
    BUDGET_VIOLATION_COUNT=$((BUDGET_VIOLATION_COUNT + BUDGET_MATCHES))
  fi
done
if [ "$BUDGET_VIOLATION_COUNT" -eq 0 ]; then
  add_non_critical_check "no_literal_cli_budgets" "PASS" "No literal --max-budget-usd values (values come from cli-budgets.env)"
else
  add_non_critical_check "no_literal_cli_budgets" "FAIL" "Literal --max-budget-usd values found:${BUDGET_VIOLATIONS}. Use BUDGET_LIGHT/NORMAL/HEAVY from cli-budgets.env"
fi

# =============================================================================
# ФОРМИРОВАНИЕ ИТОГОВОГО ОТЧЁТА
# =============================================================================

# Определяем общий статус
if [ "$CRITICAL_FAILED" -eq 0 ]; then
  OVERALL_STATUS="PASS"
else
  OVERALL_STATUS="FAIL"
fi

# Формируем JSON-отчёт
REPORT=$(python3 -c "
import json

report = {
    'test_name': 'structural-validation',
    'timestamp': '${TIMESTAMP}',
    'critical_checks': ${CRITICAL_CHECKS},
    'non_critical_checks': ${NON_CRITICAL_CHECKS},
    'summary': {
        'critical_passed': ${CRITICAL_PASSED},
        'critical_failed': ${CRITICAL_FAILED},
        'warnings': ${WARNINGS},
        'status': '${OVERALL_STATUS}'
    }
}

print(json.dumps(report, indent=2, ensure_ascii=False))
")

# Выводим отчёт
echo "$REPORT"

# Сохраняем в файл если указана папка отчётов
if [ -n "$REPORTS_DIR" ]; then
  mkdir -p "$REPORTS_DIR"
  FILE_TIMESTAMP=$(date +"%d-%m-%H-%M")
  echo "$REPORT" > "${REPORTS_DIR}/${FILE_TIMESTAMP}-structural-validation.json"
fi

# Код возврата: 0 если PASS, 1 если FAIL
if [ "$OVERALL_STATUS" = "FAIL" ]; then
  exit 1
fi
