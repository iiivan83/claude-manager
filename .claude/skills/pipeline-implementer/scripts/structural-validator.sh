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
# ПРОВЕРКИ PYTHON-СКРИПТОВ (все — warnings)
# =============================================================================
# Три проверки для Python-скриптов скилла:
#   1. preflight-check — проверка внешних зависимостей в начале run_*.py
#   2. line-buffered stdout — небуферизованный вывод в точках входа
#   3. durable LLM iteration — инкрементальная запись в LLM-циклах
#
# Каждая реализована через python3 heredoc с парсингом AST (ast — стандартный
# модуль для анализа синтаксиса Python). Многострочные паттерны вроде
# "try/import/except" одним grep не ловятся, поэтому нужен настоящий разбор.
#
# Вердикт каждого питоновского блока: OK / MISSING / SKIP / NO_LLM_LOOP.
#   OK           — признак найден, всё хорошо
#   MISSING      — признак не найден, вешаем warning
#   SKIP         — файл не парсится (битый синтаксис), проверку пропускаем
#   NO_LLM_LOOP  — в файле нет LLM-цикла (только для проверки 3)

# -----------------------------------------------------------------------------
# Проверка: preflight-check импорта зависимостей (warning)
# -----------------------------------------------------------------------------
# В каждом scripts/run_*.py ищем в первых 50 строках либо функцию
# check_environment / validate_dependencies, либо try/except(ImportError |
# AttributeError | ModuleNotFoundError) с import внутри. Без такого защитного
# блока тяжёлый пайплайн может упасть через 10 минут работы из-за отсутствующей
# библиотеки. См. "Принцип preflight-проверки окружения" в CLAUDE.md.
#
# TODO(через неделю после 2026-04-10): перевести из warning в critical.
run_preflight_check_on_file() {
  local script_file="$1"
  python3 - "$script_file" <<'PY'
import ast
import sys

script_path = sys.argv[1]
MAX_LINE_FOR_PREFLIGHT_CHECK = 50
GUARD_FUNCTION_NAMES = {"check_environment", "validate_dependencies"}
ALLOWED_IMPORT_EXCEPTIONS = {"ImportError", "AttributeError", "ModuleNotFoundError"}

with open(script_path, "r", encoding="utf-8") as source_file:
    source_text = source_file.read()

try:
    module_tree = ast.parse(source_text)
except SyntaxError:
    # Битый скрипт — это задача другой проверки, молча пропускаем.
    print("SKIP")
    sys.exit(0)

has_guard_function = False
has_try_import_guard = False

# Только прямые дети модуля (верхний уровень), в первых 50 строках
for node in ast.iter_child_nodes(module_tree):
    if node.lineno > MAX_LINE_FOR_PREFLIGHT_CHECK:
        continue

    # (а) Именованная функция-страж
    if isinstance(node, ast.FunctionDef) and node.name in GUARD_FUNCTION_NAMES:
        has_guard_function = True

    # (б) try/except(ImportError) с import внутри
    if isinstance(node, ast.Try):
        has_import_inside = any(
            isinstance(child, (ast.Import, ast.ImportFrom))
            for child in ast.walk(node)
        )
        if not has_import_inside:
            continue
        for handler in node.handlers:
            caught_exceptions = set()
            if isinstance(handler.type, ast.Name):
                caught_exceptions.add(handler.type.id)
            elif isinstance(handler.type, ast.Tuple):
                for element in handler.type.elts:
                    if isinstance(element, ast.Name):
                        caught_exceptions.add(element.id)
            if caught_exceptions & ALLOWED_IMPORT_EXCEPTIONS:
                has_try_import_guard = True
                break

print("OK" if (has_guard_function or has_try_import_guard) else "MISSING")
PY
}

if [ -d "${SKILL_PATH}/scripts" ]; then
  for preflight_target in "${SKILL_PATH}"/scripts/run_*.py; do
    [ -f "$preflight_target" ] || continue
    preflight_verdict=$(run_preflight_check_on_file "$preflight_target")
    preflight_basename=$(basename "$preflight_target")
    if [ "$preflight_verdict" = "OK" ]; then
      add_non_critical_check "preflight_check" "PASS" \
        "preflight-check: в ${preflight_basename} найдена проверка зависимостей в первых 50 строках"
    elif [ "$preflight_verdict" = "MISSING" ]; then
      add_non_critical_check "preflight_check" "FAIL" \
        "preflight-check: в ${preflight_basename} не найдена проверка зависимостей в первых 50 строках. Любой скилл с внешними Python-библиотеками должен проверять их импорт на старте — см. Принцип preflight-проверки окружения в CLAUDE.md."
    fi
  done
fi

# -----------------------------------------------------------------------------
# Проверка: line-buffered stdout в точках входа (warning)
# -----------------------------------------------------------------------------
# В каждом scripts/run_*.py, scripts/stage_*.py, scripts/main.py ищем
# sys.stdout.reconfigure(line_buffering=True) либо присваивание
# os.environ["PYTHONUNBUFFERED"] = "1" / os.environ.setdefault(...) —
# в первых 30 строках файла или внутри функции main(). Без этого при
# запуске через python3 script.py > log & прогресс будет висеть в буфере
# и не попадёт в файл лога. См. "Стандарт долгих CLI-прогонов" в CLAUDE.md.
#
# TODO(через неделю после 2026-04-10): перевести из warning в critical.
run_buffering_check_on_file() {
  local script_file="$1"
  python3 - "$script_file" <<'PY'
import ast
import sys

script_path = sys.argv[1]
MAX_LINE_FOR_BUFFERING_SETUP = 30

with open(script_path, "r", encoding="utf-8") as source_file:
    source_text = source_file.read()

try:
    module_tree = ast.parse(source_text)
except SyntaxError:
    print("SKIP")
    sys.exit(0)

def is_stdout_reconfigure_call(node):
    """Проверка вызова sys.stdout.reconfigure(line_buffering=True)."""
    if not isinstance(node, ast.Call):
        return False
    outer_attr = node.func
    if not isinstance(outer_attr, ast.Attribute) or outer_attr.attr != "reconfigure":
        return False
    inner_attr = outer_attr.value
    if not isinstance(inner_attr, ast.Attribute) or inner_attr.attr != "stdout":
        return False
    return any(keyword.arg == "line_buffering" for keyword in node.keywords)

def is_pythonunbuffered_setup(node):
    """Присваивание os.environ['PYTHONUNBUFFERED'] = ... или .setdefault(...)."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "environ"):
                key_node = target.slice
                if isinstance(key_node, ast.Constant) and key_node.value == "PYTHONUNBUFFERED":
                    return True
    if isinstance(node, ast.Call):
        call_func = node.func
        if (isinstance(call_func, ast.Attribute)
            and call_func.attr == "setdefault"
            and isinstance(call_func.value, ast.Attribute)
            and call_func.value.attr == "environ"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "PYTHONUNBUFFERED"):
            return True
    return False

def is_buffering_setup(node):
    return is_stdout_reconfigure_call(node) or is_pythonunbuffered_setup(node)

# (а) Поиск в первых 30 строках файла (любая глубина)
for node in ast.walk(module_tree):
    if getattr(node, "lineno", 10**9) > MAX_LINE_FOR_BUFFERING_SETUP:
        continue
    if is_buffering_setup(node):
        print("OK")
        sys.exit(0)

# (б) Поиск внутри функции main() (любая глубина вложенности)
for node in ast.walk(module_tree):
    if isinstance(node, ast.FunctionDef) and node.name == "main":
        for child in ast.walk(node):
            if is_buffering_setup(child):
                print("OK")
                sys.exit(0)

print("MISSING")
PY
}

if [ -d "${SKILL_PATH}/scripts" ]; then
  for buffering_target in "${SKILL_PATH}"/scripts/run_*.py \
                          "${SKILL_PATH}"/scripts/stage_*.py \
                          "${SKILL_PATH}"/scripts/main.py; do
    [ -f "$buffering_target" ] || continue
    buffering_verdict=$(run_buffering_check_on_file "$buffering_target")
    buffering_basename=$(basename "$buffering_target")
    if [ "$buffering_verdict" = "OK" ]; then
      add_non_critical_check "line_buffered_stdout" "PASS" \
        "line-buffered stdout: в ${buffering_basename} настроен небуферизованный вывод"
    elif [ "$buffering_verdict" = "MISSING" ]; then
      add_non_critical_check "line_buffered_stdout" "FAIL" \
        "line-buffered stdout: в ${buffering_basename} нет sys.stdout.reconfigure(line_buffering=True) в начале main(). Без этого при запуске через python3 script.py > log & прогресс будет висеть в буфере — см. Стандарт долгих CLI-прогонов в CLAUDE.md."
    fi
  done
fi

# -----------------------------------------------------------------------------
# Проверка: durable LLM iteration (warning, навсегда)
# -----------------------------------------------------------------------------
# В каждом scripts/*.py ищем циклы, содержащие вызов claude CLI (прямо
# или через обёртку-функцию), и проверяем три признака durable iteration:
#   (а) инкрементальная запись — json.dump/json.dumps внутри тела цикла
#   (б) resume mode          — вызов .exists() в функции, содержащей цикл
#   (в) skip-on-error        — try/except/continue без raise/sys.exit рядом
# Если хотя бы одного признака нет — warning. Проверка остаётся warning
# навсегда: возможны false positives, потому что код LLM-цикла пишут
# сильно по-разному. Неправильный паттерн приводит к катастрофическим
# потерям работы — см. отчёт 10-04_10-42_google-sheets-etl-three-errors.md.
run_durable_iteration_check_on_file() {
  local script_file="$1"
  python3 - "$script_file" <<'PY'
import ast
import sys

script_path = sys.argv[1]

with open(script_path, "r", encoding="utf-8") as source_file:
    source_lines = source_file.readlines()

try:
    module_tree = ast.parse("".join(source_lines))
except SyntaxError:
    print("SKIP")
    sys.exit(0)

def node_source_text(node):
    """Возвращает исходник узла по lineno и end_lineno."""
    start_line = node.lineno
    end_line = getattr(node, "end_lineno", start_line)
    return "".join(source_lines[start_line - 1:end_line])

# Собираем имена функций-обёрток над claude CLI:
# в их теле есть литерал "claude" И вызов subprocess
llm_wrapper_function_names = set()
for node in ast.walk(module_tree):
    if not isinstance(node, ast.FunctionDef):
        continue
    function_text = node_source_text(node)
    if ('"claude"' in function_text or "'claude'" in function_text) and "subprocess" in function_text:
        llm_wrapper_function_names.add(node.name)

def loop_calls_llm(loop_node):
    """Цикл считается LLM-циклом, если внутри прямо упомянут claude
    или вызвана одна из функций-обёрток."""
    loop_text = node_source_text(loop_node)
    if '"claude"' in loop_text or "'claude'" in loop_text:
        return True
    for child in ast.walk(loop_node):
        if not isinstance(child, ast.Call):
            continue
        called_name = None
        if isinstance(child.func, ast.Name):
            called_name = child.func.id
        elif isinstance(child.func, ast.Attribute):
            called_name = child.func.attr
        if called_name and called_name in llm_wrapper_function_names:
            return True
    return False

llm_loops = [node for node in ast.walk(module_tree)
             if isinstance(node, (ast.For, ast.While)) and loop_calls_llm(node)]

if not llm_loops:
    print("NO_LLM_LOOP")
    sys.exit(0)

def find_enclosing_function(target_loop):
    """Находит функцию верхнего уровня, внутри которой лежит этот цикл."""
    for candidate in ast.walk(module_tree):
        if not isinstance(candidate, ast.FunctionDef):
            continue
        for descendant in ast.walk(candidate):
            if descendant is target_loop:
                return candidate
    return None

def has_skip_on_error(loop_node):
    """try/except с continue в обработчике и без raise/sys.exit."""
    for child in ast.walk(loop_node):
        if not isinstance(child, ast.Try):
            continue
        for handler in child.handlers:
            handler_text = node_source_text(handler)
            if ("continue" in handler_text
                and "sys.exit" not in handler_text
                and "raise" not in handler_text):
                return True
    return False

# Если хотя бы один LLM-цикл не соответствует всем трём признакам — warning
for loop in llm_loops:
    loop_text = node_source_text(loop)
    incremental_write = ("json.dump(" in loop_text) or ("json.dumps(" in loop_text)
    skip_on_error = has_skip_on_error(loop)
    enclosing_function = find_enclosing_function(loop)
    resume_mode = False
    if enclosing_function is not None:
        enclosing_text = node_source_text(enclosing_function)
        resume_mode = ".exists(" in enclosing_text

    if not (incremental_write and skip_on_error and resume_mode):
        print("MISSING")
        sys.exit(0)

print("OK")
PY
}

if [ -d "${SKILL_PATH}/scripts" ]; then
  for durable_target in "${SKILL_PATH}"/scripts/*.py; do
    [ -f "$durable_target" ] || continue
    durable_verdict=$(run_durable_iteration_check_on_file "$durable_target")
    durable_basename=$(basename "$durable_target")
    if [ "$durable_verdict" = "OK" ]; then
      add_non_critical_check "durable_llm_iteration" "PASS" \
        "durable LLM iteration: в ${durable_basename} LLM-цикл содержит инкрементальную запись, resume mode и skip-on-error"
    elif [ "$durable_verdict" = "MISSING" ]; then
      add_non_critical_check "durable_llm_iteration" "FAIL" \
        "durable LLM iteration: в ${durable_basename} найден LLM-цикл без признаков инкрементальной записи / resume mode / skip-on-error. Проверь вручную — см. Принцип durable iteration для LLM-циклов в CLAUDE.md. Это пока только warning (возможны false positives), но неправильный паттерн приводит к катастрофическим потерям работы — см. отчёт 10-04_10-42_google-sheets-etl-three-errors.md."
    fi
    # NO_LLM_LOOP и SKIP молча пропускаем — это не нарушение
  done
fi


# =============================================================================
# ПРОВЕРКИ CLI-ВЫЗОВОВ В СКИЛЛАХ (все — warnings)
# =============================================================================
# Две проверки для bash-блоков с `claude -p` в файлах скилла
# (SKILL.md, agents/*.md, scripts/*.sh, references/*.md):
#   1. cli_write_pattern      — скилл, который правит файлы в .claude/skills/
#                                через CLI-подпроцесс, должен использовать
#                                штатный шаблон X.1/X.2 (python3 + open().write())
#                                или явно ссылаться на раздел CLAUDE.md
#                                «Запись в .claude/skills/ из CLI-подпроцессов»
#   2. cli_result_validation  — после каждого `claude -p` должна быть проверка
#                                результата на диске: grep/cat/diff/test -s/
#                                Read/python3 -c "open(...)"
#
# Оба правила описаны в корневом CLAUDE.md, раздел «Запись в `.claude/skills/`
# из CLI-подпроцессов» — подразделы «Штатный способ: Bash + python3»,
# «Шаблон X.1», «Шаблон X.2» и «Валидация результата CLI-вызова».
#
# Обе проверки реализованы одной функцией `scan_cli_blocks_in_file` —
# она находит все bash-блоки с `claude -p` в файле и возвращает по каждому
# три флага:
#   - touches_skills_dir — блок содержит признаки правки файла в .claude/skills/
#   - has_x_pattern      — в блоке есть шаблон X.1/X.2 или ссылка на CLAUDE.md
#   - has_result_check   — в блоке (или ±20 строк вокруг) есть чтение результата
#
# Вердикты:
#   OK                 — в файле есть CLI-блоки, все удовлетворяют правилу
#   MISSING_X_PATTERN  — правит .claude/skills/ без штатного шаблона X.1/X.2
#   MISSING_RESULT     — нет проверки результата хотя бы у одного CLI-вызова
#   NO_CLI_CALL        — в файле нет `claude -p`, проверка не применяется

# Функция сканирует bash-блоки с `claude -p` в одном файле и печатает
# по строке на каждый блок в формате:
#   touches_skills_dir has_x_pattern has_result_check
# где каждое поле — "1" или "0". Отдельная строка "NO_CLI_CALL" если блоков нет.
scan_cli_blocks_in_file() {
  local target_file="$1"
  python3 - "$target_file" <<'PY'
import re
import sys

target_path = sys.argv[1]

# Сколько строк окрестности брать для поиска проверки результата —
# правило из CLAUDE.md допускает проверку в соседних строках, не только
# внутри самого heredoc-блока с `claude -p`.
RESULT_CHECK_WINDOW = 20

# Сколько строк максимум берём как тело одного CLI-блока, если heredoc
# не закрылся явным маркером (страховка от глобального захвата файла).
MAX_CLI_BLOCK_LINES = 120

# Ключевые слова, по которым мы считаем, что в блоке есть «правка файла
# в .claude/skills/» — то есть задание, которое должно применять штатный
# шаблон X.1/X.2. Слова подобраны нейтрально: «измени», «обнови», «перезапиши»,
# Edit/Write tool, «правит файл» и т.п.
EDIT_INTENT_KEYWORDS = [
    "edit(", "write(", "edit tool", "write tool",
    "правка", "измени", "обнови", "перезапиши", "правит файл", "изменяет файл",
    "replace", "update file", "write to", "edit file", "modify file",
]

# Признаки штатного шаблона X.1/X.2 из CLAUDE.md — либо литеральный
# python3-heredoc с open().write(), либо текстовая ссылка на раздел.
X_PATTERN_LITERAL_MARKERS = [
    "python3 << 'pyeof'",
    "python3 <<'pyeof'",
    "python3 << \"pyeof\"",
]
X_PATTERN_TEXTUAL_MARKERS = [
    "шаблон x.1", "шаблон x.2",
    "запись в .claude/skills/",
    "cli-подпроцесс",
]

# Маркеры проверки результата CLI-вызова (чтение файла на диске).
RESULT_CHECK_MARKERS = [
    "grep ", "grep\t", "cat ", "cat\t",
    "diff ", "diff\t", "test -s", "test -f",
    "read(", "read tool",
    "python3 -c \"open", "python3 -c 'open",
    "python3 -c \"print(open", "python3 -c 'print(open",
]

try:
    with open(target_path, "r", encoding="utf-8") as source_file:
        file_lines = source_file.readlines()
except (OSError, UnicodeDecodeError):
    print("NO_CLI_CALL")
    sys.exit(0)

# Ищем все строки, где начинается bash-блок с `claude -p`.
# Дополнительно ищем признаки heredoc-тела, которое часто лежит сразу
# после строки с `claude -p` (в виде `<<'PROMPT'` ... `PROMPT`).
cli_line_indexes = []
for line_index, line_text in enumerate(file_lines):
    # Пропускаем упоминания в markdown-тексте вида "через claude -p" без
    # реальной команды — у настоящей команды слева либо начало строки,
    # либо `env`, либо пробел после отступа.
    stripped_line = line_text.lstrip()
    if "claude -p" not in stripped_line:
        continue
    # Отсекаем очевидные строчки-описания внутри markdown-абзаца:
    # настоящая команда почти всегда либо в ``` bash блоке, либо заканчивается
    # на обратный слэш (перенос строки в bash), либо содержит `env -u CLAUDECODE`.
    is_real_command = (
        stripped_line.rstrip().endswith("\\")
        or "env -u CLAUDECODE" in stripped_line
        or stripped_line.startswith("claude -p")
        or stripped_line.startswith("$ claude -p")
    )
    if not is_real_command:
        continue
    cli_line_indexes.append(line_index)

if not cli_line_indexes:
    print("NO_CLI_CALL")
    sys.exit(0)

# Для каждого вхождения `claude -p` собираем тело блока — от строки с командой
# до закрывающего heredoc-маркера (PROMPT / PY / PYEOF / EOF) или до лимита.
def collect_cli_block(start_index):
    block_lines = [file_lines[start_index]]
    heredoc_marker = None
    # Ищем маркер heredoc в самой команде или в ближайших 5 строках (он часто
    # стоит в конце строки с командой или на следующей строке после `<<'...'`).
    heredoc_pattern = re.compile(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?")
    for lookup_index in range(start_index, min(start_index + 5, len(file_lines))):
        marker_match = heredoc_pattern.search(file_lines[lookup_index])
        if marker_match:
            heredoc_marker = marker_match.group(1)
            break
    end_index = start_index
    for walk_index in range(start_index + 1, min(start_index + MAX_CLI_BLOCK_LINES, len(file_lines))):
        walk_line = file_lines[walk_index]
        block_lines.append(walk_line)
        end_index = walk_index
        if heredoc_marker and walk_line.strip() == heredoc_marker:
            break
    return end_index, "".join(block_lines)

for cli_index in cli_line_indexes:
    block_end_index, block_text = collect_cli_block(cli_index)
    block_text_lower = block_text.lower()

    # (а) Признак правки файла в .claude/skills/ — одновременно путь и намерение
    touches_skills_path = ".claude/skills/" in block_text_lower
    has_edit_intent = any(keyword in block_text_lower for keyword in EDIT_INTENT_KEYWORDS)
    touches_skills_dir = touches_skills_path and has_edit_intent

    # (б) Наличие штатного шаблона X.1/X.2 или ссылки на CLAUDE.md
    has_literal_x_pattern = any(marker in block_text_lower for marker in X_PATTERN_LITERAL_MARKERS)
    has_python3_open_write = (
        "python3" in block_text_lower
        and "open(" in block_text_lower
        and ".write(" in block_text_lower
    )
    has_textual_claude_md_ref = any(marker in block_text_lower for marker in X_PATTERN_TEXTUAL_MARKERS)
    has_x_pattern = has_literal_x_pattern or has_python3_open_write or has_textual_claude_md_ref

    # (в) Проверка результата CLI-вызова в теле блока или в ±20 строк вокруг
    window_start = max(0, cli_index - RESULT_CHECK_WINDOW)
    window_end = min(len(file_lines), block_end_index + RESULT_CHECK_WINDOW + 1)
    window_text_lower = "".join(file_lines[window_start:window_end]).lower()
    has_result_check = any(marker in window_text_lower for marker in RESULT_CHECK_MARKERS)

    touches_flag = "1" if touches_skills_dir else "0"
    x_pattern_flag = "1" if has_x_pattern else "0"
    result_check_flag = "1" if has_result_check else "0"
    print(f"{touches_flag} {x_pattern_flag} {result_check_flag}")
PY
}

# Собираем список файлов скилла, по которым гоняем проверку CLI-блоков.
# Берём SKILL.md, всё в agents/, все .sh и .md в scripts/, все .md в references/.
# Служебные файлы самого pipeline-implementer/scripts/ не трогаем — иначе
# валидатор будет бить сам себя по примерам в комментариях.
collect_cli_scan_targets() {
  local skill_root="$1"
  local target_list=()
  if [ -f "${skill_root}/SKILL.md" ]; then
    target_list+=("${skill_root}/SKILL.md")
  fi
  if [ -d "${skill_root}/agents" ]; then
    for agent_file in "${skill_root}"/agents/*.md; do
      [ -f "$agent_file" ] || continue
      target_list+=("$agent_file")
    done
  fi
  if [ -d "${skill_root}/scripts" ]; then
    for script_file in "${skill_root}"/scripts/*.sh "${skill_root}"/scripts/*.md; do
      [ -f "$script_file" ] || continue
      # Пропускаем сам structural-validator.sh — в нём внутри heredoc лежат
      # примеры паттернов, по которым мы не должны себя ловить.
      if [ "$(basename "$script_file")" = "structural-validator.sh" ]; then
        continue
      fi
      target_list+=("$script_file")
    done
  fi
  if [ -d "${skill_root}/references" ]; then
    for reference_file in "${skill_root}"/references/*.md; do
      [ -f "$reference_file" ] || continue
      target_list+=("$reference_file")
    done
  fi
  printf '%s\n' "${target_list[@]}"
}

# -----------------------------------------------------------------------------
# Проверка: штатный шаблон X.1/X.2 в CLI-вызовах, правящих .claude/skills/ (warning)
# -----------------------------------------------------------------------------
# Если внутри bash-блока с `claude -p` есть путь .claude/skills/ и намерение
# правки (Edit/Write/«измени»/«обнови»/«перезапиши»/...), блок обязан содержать
# один из штатных признаков: литеральный шаблон X.1/X.2 (python3 heredoc с
# open().write()) либо ссылку на раздел CLAUDE.md «Запись в .claude/skills/
# из CLI-подпроцессов». Без этого правки в защищённые пути штатно не проходят.
#
# TODO(через неделю после 2026-04-10): перевести из warning в critical.
CLI_WRITE_PATTERN_MISSING_FILES=""
CLI_WRITE_PATTERN_OK_FILES=0
if [ -f "${SKILL_PATH}/SKILL.md" ] || [ -d "${SKILL_PATH}/agents" ] || [ -d "${SKILL_PATH}/scripts" ] || [ -d "${SKILL_PATH}/references" ]; then
  while IFS= read -r cli_scan_target; do
    [ -n "$cli_scan_target" ] || continue
    scan_output=$(scan_cli_blocks_in_file "$cli_scan_target")
    if [ "$scan_output" = "NO_CLI_CALL" ]; then
      continue
    fi
    file_has_missing_x_pattern=0
    while IFS= read -r scan_row; do
      [ -n "$scan_row" ] || continue
      touches_flag=$(echo "$scan_row" | awk '{print $1}')
      x_pattern_flag=$(echo "$scan_row" | awk '{print $2}')
      if [ "$touches_flag" = "1" ] && [ "$x_pattern_flag" = "0" ]; then
        file_has_missing_x_pattern=1
      fi
    done <<< "$scan_output"
    if [ "$file_has_missing_x_pattern" = "1" ]; then
      CLI_WRITE_PATTERN_MISSING_FILES="${CLI_WRITE_PATTERN_MISSING_FILES} $(basename "$cli_scan_target")"
    else
      CLI_WRITE_PATTERN_OK_FILES=$((CLI_WRITE_PATTERN_OK_FILES + 1))
    fi
  done < <(collect_cli_scan_targets "$SKILL_PATH")
fi

if [ -z "$CLI_WRITE_PATTERN_MISSING_FILES" ]; then
  add_non_critical_check "cli_write_pattern" "PASS" \
    "cli_write_pattern: все CLI-вызовы, правящие .claude/skills/, используют штатный шаблон X.1/X.2 или ссылаются на раздел CLAUDE.md"
else
  add_non_critical_check "cli_write_pattern" "FAIL" \
    "cli_write_pattern: в файлах${CLI_WRITE_PATTERN_MISSING_FILES} найден CLI-вызов, который правит .claude/skills/, но не содержит штатного шаблона X.1/X.2 и не ссылается на раздел CLAUDE.md 'Запись в .claude/skills/ из CLI-подпроцессов'. Используй шаблон X.1 (полная перезапись) или X.2 (точечная замена) — см. CLAUDE.md, строки 159–245."
fi

# -----------------------------------------------------------------------------
# Проверка: валидация результата CLI-вызова (warning)
# -----------------------------------------------------------------------------
# После каждого bash-блока с `claude -p` должен быть хотя бы один оператор
# чтения целевого файла в ±20 строк: grep/cat/diff/test -s/Read/
# python3 -c "open(...)". Без такой проверки оркестратор доверяет отчёту
# агента вслепую — см. подраздел «Валидация результата CLI-вызова» в CLAUDE.md.
#
# TODO(через неделю после 2026-04-10): перевести из warning в critical.
CLI_RESULT_CHECK_MISSING_FILES=""
CLI_RESULT_CHECK_OK_FILES=0
if [ -f "${SKILL_PATH}/SKILL.md" ] || [ -d "${SKILL_PATH}/agents" ] || [ -d "${SKILL_PATH}/scripts" ] || [ -d "${SKILL_PATH}/references" ]; then
  while IFS= read -r cli_scan_target; do
    [ -n "$cli_scan_target" ] || continue
    scan_output=$(scan_cli_blocks_in_file "$cli_scan_target")
    if [ "$scan_output" = "NO_CLI_CALL" ]; then
      continue
    fi
    file_has_missing_result_check=0
    while IFS= read -r scan_row; do
      [ -n "$scan_row" ] || continue
      result_check_flag=$(echo "$scan_row" | awk '{print $3}')
      if [ "$result_check_flag" = "0" ]; then
        file_has_missing_result_check=1
      fi
    done <<< "$scan_output"
    if [ "$file_has_missing_result_check" = "1" ]; then
      CLI_RESULT_CHECK_MISSING_FILES="${CLI_RESULT_CHECK_MISSING_FILES} $(basename "$cli_scan_target")"
    else
      CLI_RESULT_CHECK_OK_FILES=$((CLI_RESULT_CHECK_OK_FILES + 1))
    fi
  done < <(collect_cli_scan_targets "$SKILL_PATH")
fi

if [ -z "$CLI_RESULT_CHECK_MISSING_FILES" ]; then
  add_non_critical_check "cli_result_validation" "PASS" \
    "cli_result_validation: все CLI-вызовы имеют проверку результата на диске (grep/cat/diff/test -s/Read)"
else
  add_non_critical_check "cli_result_validation" "FAIL" \
    "cli_result_validation: в файлах${CLI_RESULT_CHECK_MISSING_FILES} после CLI-вызова нет проверки результата на диске — правило из CLAUDE.md раздел 'Запись в .claude/skills/ из CLI-подпроцессов' подраздел 'Валидация результата CLI-вызова'. Добавь grep/cat/diff/test -s или Read на изменённый файл в соседних строках."
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

# TODO: интегрировать вызов валидатора в skill-creator, update-skill, fast-skill-updater —
# сейчас валидатор запускается только из pipeline-implementer, что пропускает
# все обновления существующих скиллов.
