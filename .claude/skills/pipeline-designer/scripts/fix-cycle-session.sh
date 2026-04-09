#!/usr/bin/env bash
set -euo pipefail

# Подключаем глобальные бюджеты для CLI-подпроцессов (см. ~/.claude/CLAUDE.md,
# раздел "CLI Subprocess Budgets Rule"). Если файла нет на машине — ничего
# не валится: используется fallback в самом вызове claude --print через
# "${BUDGET_NORMAL:-5}".
# shellcheck disable=SC1091
source ~/.claude/cli-budgets.env 2>/dev/null || true

# Таймаут для Claude-сессии (15 минут в секундах)
TIMEOUT_SECONDS=900

# Префикс для всех информационных сообщений в stderr
LOG_PREFIX="fix-cycle-session"

# PID процессов для очистки при прерывании
watchdog_pid=""
claude_pid=""

# --- Функция очистки: убивает watchdog и Claude при прерывании ---
# || true нужен потому что kill/wait могут вернуть ненулевой код
# если процесс уже завершился, а set -e прервёт скрипт
cleanup() {
  if [ -n "${watchdog_pid:-}" ]; then
    kill "$watchdog_pid" 2>/dev/null || true
    wait "$watchdog_pid" 2>/dev/null || true
  fi
  if [ -n "${claude_pid:-}" ]; then
    kill "$claude_pid" 2>/dev/null || true
    wait "$claude_pid" 2>/dev/null || true
  fi
}

trap 'cleanup' EXIT INT TERM

# --- Функция: вывод информационного сообщения в stderr ---
log_info() {
  echo "${LOG_PREFIX}: $1" >&2
}

# --- Функция: вывод ошибки в stderr и завершение с указанным кодом ---
log_error_and_exit() {
  local message="$1"
  local exit_code="$2"
  echo "ERROR: ${message}" >&2
  exit "$exit_code"
}

# --- Шаг 1. Проверка зависимостей ---
check_dependencies() {
  if ! command -v jq >/dev/null 2>&1; then
    log_error_and_exit "jq is required but not installed. Install with: brew install jq" 3
  fi
  if ! command -v claude >/dev/null 2>&1; then
    log_error_and_exit "claude CLI is required but not installed. Install from: https://claude.ai/download" 3
  fi
}

# --- Шаг 2. Разбор и валидация аргументов ---
validate_arguments() {
  # Проверка количества аргументов (минимум 3)
  if [ "$#" -lt 3 ]; then
    echo "Usage: $0 <spec-file.md> <failed-tests-dir> <output-dir> [iteration-number]" >&2
    exit 1
  fi

  local spec_arg="$1"
  local tests_dir_arg="$2"
  local output_dir_arg="$3"
  local iteration_arg="${4:-1}"

  # Валидация файла спецификации
  if [ ! -f "$spec_arg" ]; then
    log_error_and_exit "Spec file not found: ${spec_arg}" 1
  fi

  # Проверка расширения .md
  case "$spec_arg" in
    *.md) ;;
    *) log_error_and_exit "Expected .md file, got: ${spec_arg}" 1 ;;
  esac

  # Проверка доступности для чтения
  if [ ! -r "$spec_arg" ]; then
    log_error_and_exit "Spec file is not readable: ${spec_arg}" 1
  fi

  # Проверка что файл не пустой
  if [ ! -s "$spec_arg" ]; then
    log_error_and_exit "Spec file is empty: ${spec_arg}" 1
  fi

  # Преобразование пути к спецификации в абсолютный
  spec_file="$(cd "$(dirname "$spec_arg")" && pwd)/$(basename "$spec_arg")"

  # Валидация директории с тестами
  if [ ! -e "$tests_dir_arg" ]; then
    log_error_and_exit "Failed tests directory not found: ${tests_dir_arg}" 1
  fi

  if [ ! -d "$tests_dir_arg" ]; then
    log_error_and_exit "Not a directory: ${tests_dir_arg}" 1
  fi

  if [ ! -r "$tests_dir_arg" ]; then
    log_error_and_exit "Failed tests directory is not readable: ${tests_dir_arg}" 1
  fi

  # Проверка наличия JSON-файлов в директории тестов
  local json_found="false"
  for file in "$tests_dir_arg"/*.json; do
    if [ -f "$file" ]; then
      json_found="true"
      break
    fi
  done
  if [ "$json_found" = "false" ]; then
    log_error_and_exit "No JSON files found in: ${tests_dir_arg}" 1
  fi

  # Преобразование пути к директории тестов в абсолютный
  failed_tests_dir="$(cd "$tests_dir_arg" && pwd)"

  # Валидация директории результатов
  if [ -e "$output_dir_arg" ] && [ ! -d "$output_dir_arg" ]; then
    log_error_and_exit "Output path exists but is not a directory: ${output_dir_arg}" 2
  fi

  output_dir="$output_dir_arg"

  # Валидация номера итерации (только если явно передан)
  if [ "$#" -ge 4 ]; then
    if ! echo "$iteration_arg" | grep -q '^[1-9][0-9]*$'; then
      log_error_and_exit "Iteration number must be a positive integer, got: ${iteration_arg}" 1
    fi
  fi

  iteration="$iteration_arg"
}

# --- Шаг 3. Подготовка директорий результатов ---
create_output_directories() {
  if ! mkdir -p "$output_dir/traces" "$output_dir/fixes"; then
    log_error_and_exit "Cannot create output directories: ${output_dir}" 2
  fi

  # Преобразуем output_dir в абсолютный путь после создания
  output_dir="$(cd "$output_dir" && pwd)"
}

# --- Шаг 4. Создание бэкапа спецификации ---
create_spec_backup() {
  local backup_path="${output_dir}/spec-backup.md"
  log_info "creating spec backup: ${backup_path}"
  if ! cp "$spec_file" "$backup_path"; then
    log_error_and_exit "Cannot create spec backup: ${backup_path}" 2
  fi
}

# --- Шаг 5. Сбор JSON-файлов провалившихся тестов ---
collect_failed_tests() {
  local valid_files=""
  local valid_count=0

  for file in "$failed_tests_dir"/*.json; do
    # Проверка что glob раскрылся в реальный файл
    if [ ! -f "$file" ]; then
      continue
    fi

    # Проверка доступности для чтения
    if [ ! -r "$file" ]; then
      echo "WARNING: Skipping unreadable file: ${file}" >&2
      continue
    fi

    # Проверка что файл не пустой
    if [ ! -s "$file" ]; then
      echo "WARNING: Skipping empty file: ${file}" >&2
      continue
    fi

    # Проверка валидности JSON
    if ! jq empty "$file" 2>/dev/null; then
      echo "WARNING: Skipping invalid JSON file: ${file}" >&2
      continue
    fi

    # Добавляем файл в список валидных
    if [ -z "$valid_files" ]; then
      valid_files="$file"
    else
      valid_files="${valid_files}"$'\n'"${file}"
    fi
    valid_count=$((valid_count + 1))
  done

  # Проверка что остался хотя бы один валидный файл
  if [ "$valid_count" -eq 0 ]; then
    log_error_and_exit "No valid JSON test reports found in: ${failed_tests_dir}" 1
  fi

  log_info "collecting ${valid_count} failed test reports..."

  # Формирование объединённого JSON-массива из валидных файлов
  failed_tests_json=$(echo "$valid_files" | xargs jq -s '.')
}

# --- Шаг 6. Формирование промпта для Claude ---
build_prompt() {
  local spec_content
  spec_content=$(cat "$spec_file")

  # JSON-пример формата trace (из спецификации, вставляется дословно)
  local trace_json_example='{
  "agent": "problem-tracer",
  "pipeline": "pipeline-name",
  "timestamp": "2026-04-02T16:15:00",
  "status": "success",
  "result": {
    "traced_problem": {
      "source_test": "completeness-verification",
      "test_issue": "description of the problem from the test report",
      "culprit_agent": "drafter",
      "input_was": "what the agent received as input",
      "before_work": "state before the agent worked",
      "after_work": "what should have been the result",
      "root_cause": "why the problem occurred"
    }
  }
}'

  # JSON-пример формата fix-report (из спецификации, вставляется дословно)
  local fix_report_json_example='{
  "agent": "change-fixer",
  "iteration": '"${iteration}"',
  "timestamp": "2026-04-02T16:20:00",
  "status": "success",
  "result": {
    "fixed_problems": [
      {
        "problem_id": 1,
        "from_test": "structural-validation",
        "before": "what was in the spec before the fix",
        "after": "what became after the fix",
        "fix_description": "description of what exactly was fixed"
      }
    ],
    "total_fixed": 1,
    "updated_spec_file": "/absolute/path/to/updated-spec.md"
  }
}'

  prompt="You are a fix-cycle agent for the pipeline-designer skill. Your task is to find the root causes of failed tests and fix the specification.

## Instructions

### Step 1: Read the specification
The pipeline specification is below in the SPECIFICATION section.

### Step 2: Read the failed test reports
The test reports are below in the FAILED TESTS section.

### Step 3: Problem tracing
For each problem from the test reports, determine:
- Which test the problem came from (source_test)
- What exactly is wrong (test_issue)
- Which agent or stage made the mistake (culprit_agent)
- Why the error occurred (root_cause)

Use your tools (Write, Edit, Bash) to create all files.

For each problem, create a trace JSON file at:
${output_dir}/traces/trace-problem-{N}.json

Format for each file:
${trace_json_example}

### Step 4: Fix the specification
Fix each found problem in the specification:
- Each fix should be targeted — do not touch what works
- Save the updated specification to: ${output_dir}/updated-spec.md

### Step 5: Create fix report
Create the file ${output_dir}/fixes/fix-report.json with a report of all fixes.

Report format:
${fix_report_json_example}

Make sure the \"updated_spec_file\" field contains the absolute path: ${output_dir}/updated-spec.md
Make sure the \"iteration\" field is set to: ${iteration}

### Step 6: Output the result
At the very end, print the line:
RESULT_PATH:${output_dir}/updated-spec.md

## SPECIFICATION
${spec_content}

## FAILED TESTS
${failed_tests_json}"
}

# --- Шаг 7. Запуск Claude-сессии с таймаутом ---
run_claude_session() {
  log_info "launching Claude session (iteration ${iteration})..."

  # Запуск Claude в фоне
  echo "$prompt" | claude --print \
    --effort max \
    --dangerously-skip-permissions \
    --max-budget-usd "${BUDGET_NORMAL:-5}" \
    > "$output_dir/claude-output.txt" 2>"$output_dir/claude-errors.txt" &

  claude_pid=$!

  # Watchdog: таймаут через фоновый процесс с файлом-маркером
  (sleep "$TIMEOUT_SECONDS" && touch "$output_dir/.timed_out" && kill -TERM "$claude_pid" 2>/dev/null) &
  watchdog_pid=$!

  # Ожидание завершения Claude
  set +e
  wait "$claude_pid"
  claude_exit_code=$?
  set -e

  # Убиваем watchdog после завершения Claude
  # || true нужен потому что kill/wait могут вернуть ненулевой код
  # если watchdog уже завершился (sleep ещё идёт, но kill может не найти PID)
  kill "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  watchdog_pid=""
  claude_pid=""

  log_info "Claude session completed"

  # Проверка причины завершения: таймаут через файл-маркер
  if [ -f "$output_dir/.timed_out" ]; then
    rm -f "$output_dir/.timed_out"
    log_error_and_exit "Claude session timed out after 15 minutes" 4
  fi

  # Проверка exit code Claude
  if [ "$claude_exit_code" -ne 0 ]; then
    log_error_and_exit "Claude session failed with exit code ${claude_exit_code}. See claude-errors.txt for details" 4
  fi

  # Проверка что Claude выдал хоть что-то
  if [ ! -s "$output_dir/claude-output.txt" ]; then
    log_error_and_exit "Claude session produced no output" 4
  fi
}

# --- Шаг 8. Парсинг маркера RESULT_PATH ---
parse_result_path() {
  result_path=$(grep '^RESULT_PATH:' "$output_dir/claude-output.txt" | head -1 | cut -d: -f2-)

  if [ -z "$result_path" ]; then
    log_error_and_exit "RESULT_PATH marker not found in Claude output" 5
  fi
}

# --- Шаг 9. Верификация выходных файлов ---
verify_output_files() {
  log_info "verifying output files..."

  # 9.1. Проверка обновлённой спецификации
  if [ ! -f "$output_dir/updated-spec.md" ]; then
    log_error_and_exit "Updated spec file not found: ${output_dir}/updated-spec.md" 5
  fi

  if [ ! -s "$output_dir/updated-spec.md" ]; then
    log_error_and_exit "Updated spec file not found: ${output_dir}/updated-spec.md" 5
  fi

  # 9.2. Проверка файлов трассировки (желательные, не обязательные)
  local trace_files_found="false"
  for trace_file in "$output_dir"/traces/trace-problem-*.json; do
    if [ -f "$trace_file" ]; then
      trace_files_found="true"
      # Проверка валидности JSON
      if ! jq empty "$trace_file" 2>/dev/null; then
        echo "WARNING: Invalid JSON in trace file: ${trace_file}" >&2
        continue
      fi
      # Проверка обязательного поля
      if ! jq -e '.result.traced_problem' "$trace_file" >/dev/null 2>&1; then
        echo "WARNING: Missing result.traced_problem in: ${trace_file}" >&2
      fi
    fi
  done

  if [ "$trace_files_found" = "false" ]; then
    echo "WARNING: No trace files found in ${output_dir}/traces/" >&2
  fi

  # 9.3. Проверка отчёта об исправлениях (обязательный)
  local fix_report="${output_dir}/fixes/fix-report.json"
  if [ ! -f "$fix_report" ]; then
    log_error_and_exit "Fix report not found or invalid: ${fix_report}" 5
  fi

  if ! jq empty "$fix_report" 2>/dev/null; then
    log_error_and_exit "Fix report not found or invalid: ${fix_report}" 5
  fi

  if ! jq -e '.result.fixed_problems' "$fix_report" >/dev/null 2>&1; then
    log_error_and_exit "Fix report not found or invalid: ${fix_report}" 5
  fi

  if ! jq -e '.result.updated_spec_file' "$fix_report" >/dev/null 2>&1; then
    log_error_and_exit "Fix report not found or invalid: ${fix_report}" 5
  fi

  # 9.4. Подсчёт результатов
  # || true нужен потому что ls вернёт ненулевой код если файлов нет
  traces_count=$(ls "$output_dir"/traces/trace-problem-*.json 2>/dev/null | wc -l | tr -d ' ')
  fixes_count=$(jq '.result.total_fixed' "$fix_report")

  log_info "done -- ${traces_count} traces, ${fixes_count} fixes applied"
}

# --- Шаг 10. Вывод пути к обновлённой спецификации ---
output_result_path() {
  local updated_spec_absolute_path
  updated_spec_absolute_path="$(cd "$(dirname "$output_dir/updated-spec.md")" && pwd)/$(basename "$output_dir/updated-spec.md")"
  echo "$updated_spec_absolute_path"
}

# --- Главная функция: оркестрация всех шагов ---
main() {
  check_dependencies
  validate_arguments "$@"
  create_output_directories
  create_spec_backup
  collect_failed_tests
  build_prompt
  run_claude_session
  parse_result_path
  verify_output_files
  output_result_path
}

main "$@"
