#!/bin/bash
set -euo pipefail

# ==============================================================================
# extract-metrics.sh — извлечение метрик полноты собранного скилла (тест T4)
#
# Проверяет структуру готового скилла: считает этапы, агентов, скрипты, схемы,
# разделы SKILL.md, общее количество строк, наличие обработки ошибок и
# инструкций по логированию. Выводит JSON-отчёт с результатами.
#
# Использование: extract-metrics.sh <папка-скилла> [папка-отчётов]
# ==============================================================================

# --- Локаль для корректной работы grep с кириллицей ---
export LC_ALL=en_US.UTF-8

# --- Константы ---

TEST_NAME="completeness-metrics"
# Общее количество проверяемых метрик
TOTAL_METRICS=8
# Минимальные пороги для каждой метрики
THRESHOLD_STAGES=1
THRESHOLD_AGENTS=1
THRESHOLD_SCRIPTS=0
THRESHOLD_SCHEMAS=1
THRESHOLD_SKILL_MD_SECTIONS=1
THRESHOLD_TOTAL_LINES=1

# Глобальная переменная для временной директории (нужна для trap)
TMP_DIR=""

# --- Очистка при завершении ---

# Удаляет временную директорию при любом завершении скрипта
cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
}

# --- Проверка зависимостей ---

# Проверяет, что jq установлен в системе
check_jq_installed() {
  if ! command -v jq > /dev/null 2>&1; then
    echo "ERROR: jq is required but not installed. Install with: brew install jq" >&2
    exit 3
  fi
}

# --- Валидация входных данных ---

# Показывает справку по использованию и завершается с кодом 1
show_usage_and_exit() {
  echo "Usage: $0 <skill-folder-path> [reports-dir]" >&2
  exit 1
}

# Проверяет, что указанный путь — существующая директория скилла
validate_skill_folder() {
  local folder_path="$1"

  if [ ! -d "$folder_path" ]; then
    echo "ERROR: Skill folder not found: $folder_path" >&2
    exit 1
  fi

  if [ ! -f "$folder_path/SKILL.md" ]; then
    echo "ERROR: SKILL.md not found in: $folder_path" >&2
    exit 1
  fi
}

# Проверяет, что папка отчётов существует (или создаёт её)
ensure_reports_dir() {
  local reports_dir="$1"

  if [ ! -d "$reports_dir" ]; then
    mkdir -p "$reports_dir" || {
      echo "ERROR: Cannot create reports directory: $reports_dir" >&2
      exit 1
    }
  fi
}

# Преобразует относительный путь в абсолютный
resolve_absolute_path() {
  local target_path="$1"
  local dir_part
  local base_part

  dir_part=$(cd "$(dirname "$target_path")" && pwd)
  base_part=$(basename "$target_path")
  echo "${dir_part}/${base_part}"
}

# --- Подсчёт метрик ---

# Считает количество этапов — заголовки H2/H3 с «Этап N» или «Stage N» в SKILL.md
count_stages() {
  local skill_md_path="$1"
  local count

  count=$(grep -c -i -E '^#{2,3}[[:space:]]+(Этап|Stage)[[:space:]]+[0-9]' "$skill_md_path" || true)
  echo "$count"
}

# Считает количество .md-файлов в папке agents/
count_agents() {
  local skill_folder="$1"
  local agents_dir="${skill_folder}/agents"

  if [ ! -d "$agents_dir" ]; then
    echo "0"
    return
  fi

  local count
  count=$(find "$agents_dir" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  echo "$count"
}

# Считает количество .sh-файлов в папке scripts/
count_scripts() {
  local skill_folder="$1"
  local scripts_dir="${skill_folder}/scripts"

  if [ ! -d "$scripts_dir" ]; then
    echo "0"
    return
  fi

  local count
  count=$(find "$scripts_dir" -maxdepth 1 -name '*.sh' -type f | wc -l | tr -d ' ')
  echo "$count"
}

# Считает количество определений схем в references/schemas.json
# Каждый ключ верхнего уровня в JSON — одна схема
count_schemas() {
  local skill_folder="$1"
  local schemas_path="${skill_folder}/references/schemas.json"

  if [ ! -f "$schemas_path" ]; then
    echo "0"
    return
  fi

  # Проверяем, что файл — валидный JSON
  if ! jq empty "$schemas_path" 2>/dev/null; then
    echo "0"
    return
  fi

  local count
  count=$(jq 'keys | length' "$schemas_path" 2>/dev/null || echo "0")
  echo "$count"
}

# Считает количество разделов H2 (##) в SKILL.md
count_skill_md_sections() {
  local skill_md_path="$1"
  local count

  count=$(grep -c -E '^## ' "$skill_md_path" || true)
  echo "$count"
}

# Считает общее количество строк во всех файлах скилла
count_total_lines() {
  local skill_folder="$1"
  local total

  # Считаем строки во всех текстовых файлах (.md, .sh, .json)
  total=$(find "$skill_folder" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.json' \) -exec cat {} + | wc -l | tr -d ' ')
  echo "$total"
}

# Проверяет наличие инструкций по обработке ошибок в каждом файле агента
# Возвращает "true", если все агенты содержат такие инструкции
check_error_handling() {
  local skill_folder="$1"
  local agents_dir="${skill_folder}/agents"

  if [ ! -d "$agents_dir" ]; then
    echo "false"
    return
  fi

  # Проверяем, есть ли хотя бы один агент
  local agent_count
  agent_count=$(find "$agents_dir" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  if [ "$agent_count" -eq 0 ]; then
    echo "false"
    return
  fi

  # Паттерны, указывающие на обработку ошибок в инструкциях агента
  local error_pattern='[Оо]бработка ошиб|[Оо]шибк|[Пп]ри ошибке|[Пп]ри сбое|error.?handl|[Ff]allback|[Пп]ри провале|[Ее]сли.*не удал|[Ее]сли.*провал'

  # Каждый агент должен содержать хотя бы одно упоминание обработки ошибок
  local agents_without_error_handling
  agents_without_error_handling=0

  while IFS= read -r agent_file; do
    local match_count
    match_count=$(grep -c -i -E "$error_pattern" "$agent_file" || true)
    if [ "$match_count" -eq 0 ]; then
      agents_without_error_handling=$((agents_without_error_handling + 1))
    fi
  done < <(find "$agents_dir" -maxdepth 1 -name '*.md' -type f)

  if [ "$agents_without_error_handling" -eq 0 ]; then
    echo "true"
  else
    echo "false"
  fi
}

# Проверяет наличие инструкций по логированию в файлах скилла
# Возвращает "true", если найдены упоминания логирования
check_logging() {
  local skill_folder="$1"

  # Паттерны, указывающие на инструкции по логированию
  local logging_pattern='[Лл]ог|[Лл]огиров|[Зз]апис.*в.*лог|[Зз]апис.*в.*журнал|logging|log_|write.*log|append.*log'

  # Ищем во всех .md-файлах скилла (SKILL.md и агенты)
  local match_count
  match_count=$(find "$skill_folder" -type f -name '*.md' -exec grep -l -i -E "$logging_pattern" {} + 2>/dev/null | wc -l | tr -d ' ')

  if [ "$match_count" -gt 0 ]; then
    echo "true"
  else
    echo "false"
  fi
}

# --- Формирование результата ---

# Определяет статус метрики: PASS или FAIL
# Для числовых метрик: значение >= порог → PASS
evaluate_numeric_metric() {
  local value="$1"
  local threshold="$2"

  if [ "$value" -ge "$threshold" ]; then
    echo "PASS"
  else
    echo "FAIL"
  fi
}

# Определяет статус булевой метрики: true → PASS, false → FAIL
evaluate_boolean_metric() {
  local value="$1"

  if [ "$value" = "true" ]; then
    echo "PASS"
  else
    echo "FAIL"
  fi
}

# Формирует JSON-объект одной числовой метрики
build_metric_json() {
  local value="$1"
  local threshold="$2"
  local status="$3"

  jq -n \
    --arg value "$value" \
    --arg threshold "$threshold" \
    --arg status "$status" \
    '{value: ($value | tonumber), threshold: $threshold, status: $status}'
}

# Формирует JSON-объект одной булевой метрики
build_boolean_metric_json() {
  local value="$1"
  local threshold="$2"
  local status="$3"

  jq -n \
    --argjson value "$value" \
    --arg threshold "$threshold" \
    --arg status "$status" \
    '{value: $value, threshold: $threshold, status: $status}'
}

# --- Главная функция ---

main() {
  # Шаг 1: проверка зависимостей
  check_jq_installed

  # Шаг 2: валидация аргументов
  if [ $# -lt 1 ] || [ -z "${1:-}" ]; then
    show_usage_and_exit
  fi

  local skill_folder="$1"
  validate_skill_folder "$skill_folder"

  # Преобразуем путь к папке в абсолютный
  skill_folder=$(resolve_absolute_path "$skill_folder")

  # Необязательный аргумент: папка для сохранения отчёта
  local reports_dir=""
  if [ $# -ge 2 ] && [ -n "${2:-}" ]; then
    reports_dir="$2"
    ensure_reports_dir "$reports_dir"
    reports_dir=$(resolve_absolute_path "$reports_dir")
  fi

  # Шаг 3: создание временной директории
  TMP_DIR=$(mktemp -d) || {
    echo "ERROR: Cannot create temporary directory" >&2
    exit 2
  }
  trap cleanup EXIT INT TERM

  # Шаг 4: путь к SKILL.md
  local skill_md_path="${skill_folder}/SKILL.md"

  # Шаг 5: подсчёт всех метрик
  local stages_count
  stages_count=$(count_stages "$skill_md_path")

  local agents_count
  agents_count=$(count_agents "$skill_folder")

  local scripts_count
  scripts_count=$(count_scripts "$skill_folder")

  local schemas_count
  schemas_count=$(count_schemas "$skill_folder")

  local skill_md_sections
  skill_md_sections=$(count_skill_md_sections "$skill_md_path")

  local total_lines
  total_lines=$(count_total_lines "$skill_folder")

  local has_error_handling
  has_error_handling=$(check_error_handling "$skill_folder")

  local has_logging
  has_logging=$(check_logging "$skill_folder")

  # Шаг 6: определение статусов
  local stages_status
  stages_status=$(evaluate_numeric_metric "$stages_count" "$THRESHOLD_STAGES")

  local agents_status
  agents_status=$(evaluate_numeric_metric "$agents_count" "$THRESHOLD_AGENTS")

  local scripts_status
  scripts_status=$(evaluate_numeric_metric "$scripts_count" "$THRESHOLD_SCRIPTS")

  local schemas_status
  schemas_status=$(evaluate_numeric_metric "$schemas_count" "$THRESHOLD_SCHEMAS")

  local sections_status
  sections_status=$(evaluate_numeric_metric "$skill_md_sections" "$THRESHOLD_SKILL_MD_SECTIONS")

  local lines_status
  lines_status=$(evaluate_numeric_metric "$total_lines" "$THRESHOLD_TOTAL_LINES")

  local error_handling_status
  error_handling_status=$(evaluate_boolean_metric "$has_error_handling")

  local logging_status
  logging_status=$(evaluate_boolean_metric "$has_logging")

  # Шаг 7: подсчёт итогов
  local passed_count=0
  local failed_count=0

  for status in "$stages_status" "$agents_status" "$scripts_status" \
                "$schemas_status" "$sections_status" "$lines_status" \
                "$error_handling_status" "$logging_status"; do
    if [ "$status" = "PASS" ]; then
      passed_count=$((passed_count + 1))
    else
      failed_count=$((failed_count + 1))
    fi
  done

  local overall_status="PASS"
  if [ "$failed_count" -gt 0 ]; then
    overall_status="FAIL"
  fi

  # Шаг 8: сборка JSON-отчёта
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  # Собираем каждую метрику отдельно во временные файлы
  build_metric_json "$stages_count" ">=${THRESHOLD_STAGES}" "$stages_status" \
    > "$TMP_DIR/m_stages.json"
  build_metric_json "$agents_count" ">=${THRESHOLD_AGENTS}" "$agents_status" \
    > "$TMP_DIR/m_agents.json"
  build_metric_json "$scripts_count" ">=${THRESHOLD_SCRIPTS}" "$scripts_status" \
    > "$TMP_DIR/m_scripts.json"
  build_metric_json "$schemas_count" ">=${THRESHOLD_SCHEMAS}" "$schemas_status" \
    > "$TMP_DIR/m_schemas.json"
  build_metric_json "$skill_md_sections" ">=${THRESHOLD_SKILL_MD_SECTIONS}" "$sections_status" \
    > "$TMP_DIR/m_sections.json"
  build_metric_json "$total_lines" ">=${THRESHOLD_TOTAL_LINES}" "$lines_status" \
    > "$TMP_DIR/m_lines.json"
  build_boolean_metric_json "$has_error_handling" "=true" "$error_handling_status" \
    > "$TMP_DIR/m_error.json"
  build_boolean_metric_json "$has_logging" "=true" "$logging_status" \
    > "$TMP_DIR/m_logging.json"

  # Собираем итоговый JSON из всех частей
  local json_output
  json_output=$(jq -n \
    --arg test_name "$TEST_NAME" \
    --arg timestamp "$timestamp" \
    --slurpfile stages "$TMP_DIR/m_stages.json" \
    --slurpfile agents "$TMP_DIR/m_agents.json" \
    --slurpfile scripts "$TMP_DIR/m_scripts.json" \
    --slurpfile schemas "$TMP_DIR/m_schemas.json" \
    --slurpfile sections "$TMP_DIR/m_sections.json" \
    --slurpfile lines "$TMP_DIR/m_lines.json" \
    --slurpfile error "$TMP_DIR/m_error.json" \
    --slurpfile logging "$TMP_DIR/m_logging.json" \
    --argjson total_metrics "$TOTAL_METRICS" \
    --argjson passed "$passed_count" \
    --argjson failed "$failed_count" \
    --arg overall_status "$overall_status" \
    '{
      test_name: $test_name,
      timestamp: $timestamp,
      metrics: {
        stages_count: $stages[0],
        agents_count: $agents[0],
        scripts_count: $scripts[0],
        schemas_count: $schemas[0],
        skill_md_sections: $sections[0],
        total_lines: $lines[0],
        has_error_handling: $error[0],
        has_logging: $logging[0]
      },
      summary: {
        total_metrics: $total_metrics,
        passed: $passed,
        failed: $failed,
        status: $overall_status
      }
    }') || {
    echo "ERROR: Failed to generate JSON output" >&2
    exit 2
  }

  # Шаг 9: вывод JSON в stdout
  echo "$json_output"

  # Шаг 10: если указана папка отчётов — сохраняем туда копию
  if [ -n "$reports_dir" ]; then
    local report_filename
    report_filename=$(date +"%d-%m-%H-%M")-${TEST_NAME}.json
    local report_path="${reports_dir}/${report_filename}"

    echo "$json_output" > "$report_path"
    echo "Report saved to: $report_path" >&2
  fi
}

main "$@"
