#!/usr/bin/env bash
set -euo pipefail

# validate-step-progression.sh — проверяет последовательность шагов
# в orchestrator-log.json: хронологический порядок, отсутствие пропусков,
# наличие обязательных полей и финализация пайплайна.

# --- Константы ---

# Обязательные поля в каждом шаге
REQUIRED_STEP_FIELDS=("start_time" "end_time" "success")
# Поле, по которому определяется фаза (phase или step_number)
PHASE_FIELD_PRIMARY="phase"
PHASE_FIELD_FALLBACK="step_number"

# --- Переменные состояния ---

# Счётчики результатов
pass_count=0
fail_count=0
warn_count=0

# Флаг для определения доступного JSON-парсера
json_parser=""

# --- Локаль для корректной работы с Unicode ---
export LC_ALL=en_US.UTF-8

# --- Вспомогательные функции вывода ---

# Печатает строку с меткой [PASS]
print_pass() {
  local message="$1"
  echo "[PASS] $message"
  pass_count=$((pass_count + 1))
}

# Печатает строку с меткой [FAIL]
print_fail() {
  local message="$1"
  echo "[FAIL] $message"
  fail_count=$((fail_count + 1))
}

# Печатает строку с меткой [WARN]
print_warn() {
  local message="$1"
  echo "[WARN] $message"
  warn_count=$((warn_count + 1))
}

# --- Определение JSON-парсера ---

# Выбирает jq или python как fallback для парсинга JSON
detect_json_parser() {
  if command -v jq > /dev/null 2>&1; then
    json_parser="jq"
    return
  fi

  if command -v python3 > /dev/null 2>&1; then
    json_parser="python3"
    return
  fi

  if command -v python > /dev/null 2>&1; then
    json_parser="python"
    return
  fi

  echo "ERROR: Neither jq nor python found. Install jq: brew install jq" >&2
  exit 1
}

# Выполняет jq-выражение над файлом, используя доступный парсер
run_json_query() {
  local file_path="$1"
  local jq_expression="$2"

  if [ "$json_parser" = "jq" ]; then
    jq -r "$jq_expression" "$file_path"
  else
    # Fallback: конвертируем jq-выражение в python через stdin
    # Поддерживаем только базовые запросы, используемые в этом скрипте
    "$json_parser" -c "
import json, sys
with open('$file_path') as f:
    data = json.load(f)
expr = '''$jq_expression'''
# Базовая обработка простых jq-путей
if expr == '.steps | length':
    print(len(data.get('steps', [])))
elif expr == '.steps | type':
    print(type(data.get('steps')).__name__.replace('list','array').replace('NoneType','null'))
elif expr == '.completed_at // empty':
    val = data.get('completed_at')
    if val: print(val)
elif expr == '.final_status // empty':
    val = data.get('final_status')
    if val: print(val)
elif expr.startswith('.steps['):
    # Парсим индекс и поле: .steps[0].field или .steps[0].field // empty
    import re
    m = re.match(r'\.steps\[(\d+)\]\.(\w+)( // empty)?', expr)
    if m:
        idx, field, fallback = int(m.group(1)), m.group(2), m.group(3)
        steps = data.get('steps', [])
        if idx < len(steps):
            val = steps[idx].get(field)
            if val is not None:
                print(json.dumps(val) if isinstance(val, bool) else val)
            elif not fallback:
                print('null')
elif expr.startswith('.steps[] | .'):
    # .steps[] | .field // empty
    import re
    m = re.match(r'\.steps\[\] \| \.(\w+)( // empty)?', expr)
    if m:
        field, fallback = m.group(1), m.group(2)
        for step in data.get('steps', []):
            val = step.get(field)
            if val is not None:
                print(json.dumps(val) if isinstance(val, bool) else val)
            elif not fallback:
                print('null')
" 2>/dev/null
  fi
}

# --- Валидация входных данных ---

validate_arguments() {
  local file_path="$1"

  if [ -z "$file_path" ]; then
    echo "Usage: $0 <orchestrator-log.json>" >&2
    exit 1
  fi

  if [ ! -f "$file_path" ]; then
    echo "ERROR: File not found: $file_path" >&2
    exit 1
  fi

  if [ ! -r "$file_path" ]; then
    echo "ERROR: File is not readable: $file_path" >&2
    exit 1
  fi

  if [ ! -s "$file_path" ]; then
    echo "ERROR: File is empty: $file_path" >&2
    exit 1
  fi
}

# --- Проверка 1: Валидный JSON ---

check_valid_json() {
  local file_path="$1"

  if [ "$json_parser" = "jq" ]; then
    if jq empty "$file_path" 2>/dev/null; then
      print_pass "Valid JSON structure"
      return 0
    fi
  else
    if "$json_parser" -c "import json; json.load(open('$file_path'))" 2>/dev/null; then
      print_pass "Valid JSON structure"
      return 0
    fi
  fi

  print_fail "Invalid JSON structure"
  return 1
}

# --- Проверка 2: Поле steps — массив ---

check_steps_is_array() {
  local file_path="$1"
  local steps_type

  steps_type=$(run_json_query "$file_path" '.steps | type')

  if [ "$steps_type" = "array" ]; then
    local step_count
    step_count=$(run_json_query "$file_path" '.steps | length')
    print_pass "Steps field is an array ($step_count steps)"
    return 0
  fi

  print_fail "Steps field is not an array (got: $steps_type)"
  return 1
}

# --- Проверка 3: Обязательные поля в каждом шаге ---

check_required_fields() {
  local file_path="$1"
  local step_count
  local all_present=true

  step_count=$(run_json_query "$file_path" '.steps | length')

  local step_index=0
  while [ "$step_index" -lt "$step_count" ]; do
    for field in "${REQUIRED_STEP_FIELDS[@]}"; do
      local value
      value=$(run_json_query "$file_path" ".steps[$step_index].$field // empty")

      if [ -z "$value" ]; then
        # Номер шага для человека — с единицы
        local human_step=$((step_index + 1))
        print_fail "Missing required field: $field in step $human_step"
        all_present=false
      fi
    done
    step_index=$((step_index + 1))
  done

  # Проверяем наличие идентификатора фазы (phase или step_number)
  step_index=0
  while [ "$step_index" -lt "$step_count" ]; do
    local phase_value
    local step_number_value
    phase_value=$(run_json_query "$file_path" ".steps[$step_index].$PHASE_FIELD_PRIMARY // empty")
    step_number_value=$(run_json_query "$file_path" ".steps[$step_index].$PHASE_FIELD_FALLBACK // empty")

    if [ -z "$phase_value" ] && [ -z "$step_number_value" ]; then
      local human_step=$((step_index + 1))
      print_fail "Missing phase identifier (phase or step_number) in step $human_step"
      all_present=false
    fi
    step_index=$((step_index + 1))
  done

  if [ "$all_present" = true ]; then
    print_pass "All required fields present in all $step_count steps"
  fi
}

# --- Проверка 4: Хронологический порядок шагов ---

check_chronological_order() {
  local file_path="$1"
  local step_count
  local order_ok=true

  step_count=$(run_json_query "$file_path" '.steps | length')

  if [ "$step_count" -lt 2 ]; then
    print_pass "Steps in chronological order ($step_count steps, nothing to compare)"
    return 0
  fi

  local step_index=1
  while [ "$step_index" -lt "$step_count" ]; do
    local prev_end
    local current_start

    prev_end=$(run_json_query "$file_path" ".steps[$((step_index - 1))].end_time // empty")
    current_start=$(run_json_query "$file_path" ".steps[$step_index].start_time // empty")

    # Пропускаем проверку, если какое-то время отсутствует (это уловит check_required_fields)
    if [ -z "$prev_end" ] || [ -z "$current_start" ]; then
      step_index=$((step_index + 1))
      continue
    fi

    # Сравниваем строки дат лексикографически — работает для ISO 8601
    if [[ "$current_start" < "$prev_end" ]]; then
      local human_prev=$((step_index))
      local human_current=$((step_index + 1))
      print_fail "Step $human_current starts ($current_start) before step $human_prev ends ($prev_end)"
      order_ok=false
    fi
    step_index=$((step_index + 1))
  done

  if [ "$order_ok" = true ]; then
    print_pass "Steps in chronological order ($step_count steps)"
  fi
}

# --- Проверка 5: Пропущенные фазы ---

check_missing_phases() {
  local file_path="$1"
  local step_count

  step_count=$(run_json_query "$file_path" '.steps | length')

  if [ "$step_count" -eq 0 ]; then
    print_warn "No steps to check for missing phases"
    return 0
  fi

  # Собираем номера фаз из phase ("phase-N") или step_number (N)
  local phase_numbers=()
  local uses_phase_field=false

  local step_index=0
  while [ "$step_index" -lt "$step_count" ]; do
    local phase_value
    phase_value=$(run_json_query "$file_path" ".steps[$step_index].$PHASE_FIELD_PRIMARY // empty")

    if [ -n "$phase_value" ]; then
      uses_phase_field=true
      # Извлекаем число из "phase-N"
      local num
      num=$(echo "$phase_value" | sed 's/^phase-//')
      if [[ "$num" =~ ^[0-9]+$ ]]; then
        phase_numbers+=("$num")
      fi
    else
      # Пробуем step_number
      local step_num
      step_num=$(run_json_query "$file_path" ".steps[$step_index].$PHASE_FIELD_FALLBACK // empty")
      if [ -n "$step_num" ] && [[ "$step_num" =~ ^[0-9]+$ ]]; then
        phase_numbers+=("$step_num")
      fi
    fi
    step_index=$((step_index + 1))
  done

  if [ ${#phase_numbers[@]} -eq 0 ]; then
    print_warn "No numeric phase identifiers found, cannot check for gaps"
    return 0
  fi

  # Сортируем номера и ищем пропуски
  local sorted_phases
  sorted_phases=$(printf '%s\n' "${phase_numbers[@]}" | sort -n | uniq)
  local min_phase
  local max_phase
  min_phase=$(echo "$sorted_phases" | head -1)
  max_phase=$(echo "$sorted_phases" | tail -1)

  local has_gaps=false
  local check_phase=$min_phase
  while [ "$check_phase" -le "$max_phase" ]; do
    if ! echo "$sorted_phases" | grep -qx "$check_phase"; then
      local phase_label
      if [ "$uses_phase_field" = true ]; then
        phase_label="phase-${check_phase}"
      else
        phase_label="step_number ${check_phase}"
      fi
      print_warn "Missing phase: $phase_label"
      has_gaps=true
    fi
    check_phase=$((check_phase + 1))
  done

  if [ "$has_gaps" = false ]; then
    print_pass "No missing phases (${min_phase} to ${max_phase})"
  fi
}

# --- Проверка 6: Финализация пайплайна ---

check_completion() {
  local file_path="$1"

  # Проверяем наличие completed_at или final_status на верхнем уровне
  local completed_at
  local final_status
  completed_at=$(run_json_query "$file_path" '.completed_at // empty')
  final_status=$(run_json_query "$file_path" '.final_status // empty')

  if [ -n "$completed_at" ] || [ -n "$final_status" ]; then
    local details=""
    if [ -n "$final_status" ]; then
      details="final_status=$final_status"
    fi
    if [ -n "$completed_at" ]; then
      details="${details:+$details, }completed_at=$completed_at"
    fi
    print_pass "Pipeline finalized ($details)"
    return 0
  fi

  # Если нет полей финализации, проверяем success последнего шага
  local step_count
  step_count=$(run_json_query "$file_path" '.steps | length')

  if [ "$step_count" -gt 0 ]; then
    local last_index=$((step_count - 1))
    local last_success
    last_success=$(run_json_query "$file_path" ".steps[$last_index].success // empty")

    if [ "$last_success" = "true" ]; then
      print_warn "No explicit completion markers (completed_at/final_status), but last step succeeded"
      return 0
    fi
  fi

  print_warn "No completion markers found (expected completed_at or final_status)"
}

# --- Определение exit code по результатам ---

determine_exit_code() {
  if [ "$fail_count" -gt 0 ]; then
    return 1
  fi

  if [ "$warn_count" -gt 0 ]; then
    return 2
  fi

  return 0
}

# --- Итоговая сводка ---

print_summary() {
  local total=$((pass_count + fail_count + warn_count))
  echo ""
  echo "--- Summary ---"
  echo "Total checks: $total (PASS: $pass_count, FAIL: $fail_count, WARN: $warn_count)"
}

# --- Главная функция ---

main() {
  local input_file="${1:-}"

  # Шаг 1: Определяем доступный JSON-парсер (jq или python)
  detect_json_parser

  # Шаг 2: Валидация аргументов
  validate_arguments "$input_file"

  # Шаг 3: Проверка валидности JSON
  # Если JSON невалидный, дальнейшие проверки бессмысленны
  set +e
  check_valid_json "$input_file"
  local json_valid=$?
  set -e

  if [ "$json_valid" -ne 0 ]; then
    print_summary
    exit 1
  fi

  # Шаг 4: Проверка структуры steps
  set +e
  check_steps_is_array "$input_file"
  local steps_valid=$?
  set -e

  if [ "$steps_valid" -ne 0 ]; then
    print_summary
    exit 1
  fi

  # Шаг 5: Проверка обязательных полей
  set +e
  check_required_fields "$input_file"
  set -e

  # Шаг 6: Проверка хронологического порядка
  set +e
  check_chronological_order "$input_file"
  set -e

  # Шаг 7: Проверка пропущенных фаз
  set +e
  check_missing_phases "$input_file"
  set -e

  # Шаг 8: Проверка финализации
  set +e
  check_completion "$input_file"
  set -e

  # Итоговая сводка
  print_summary

  # Exit code зависит от результатов
  set +e
  determine_exit_code
  exit $?
}

main "$@"
