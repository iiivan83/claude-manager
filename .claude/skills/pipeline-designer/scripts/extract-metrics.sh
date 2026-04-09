#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=en_US.UTF-8

# ==============================================================================
# extract-metrics.sh — извлечение структурных метрик из спецификации пайплайна
#
# Парсит MD-файл, находит ключевые элементы (этапы, агенты, скиллы, чеклист
# и др.) и выводит результат как валидный JSON в stdout.
#
# Использование: extract-metrics.sh <spec-file.md> [run-number]
# ==============================================================================

# Глобальная переменная для временной директории (нужна в trap для очистки)
TMP_DIR=""

# Очищает временную директорию при любом завершении скрипта
cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
}

# --- Проверка зависимостей ---------------------------------------------------

# Проверяет, что jq установлен в системе
check_jq_installed() {
  if ! command -v jq > /dev/null 2>&1; then
    echo "ERROR: jq is required but not installed. Install with: brew install jq" >&2
    exit 3
  fi
}

# --- Валидация входных данных ------------------------------------------------

# Показывает справку по использованию скрипта и завершается с кодом 1
show_usage_and_exit() {
  echo "Usage: $0 <spec-file.md> [run-number]" >&2
  exit 1
}

# Проверяет, что файл существует, имеет расширение .md, не пуст и доступен
validate_input_file() {
  local file_path="$1"

  if [ ! -f "$file_path" ]; then
    echo "ERROR: File not found: $file_path" >&2
    exit 1
  fi

  # Проверка расширения .md
  if [ "${file_path##*.}" != "md" ]; then
    echo "ERROR: Expected .md file, got: $file_path" >&2
    exit 1
  fi

  if [ ! -s "$file_path" ]; then
    echo "ERROR: File is empty: $file_path" >&2
    exit 1
  fi

  if [ ! -r "$file_path" ]; then
    echo "ERROR: File is not readable: $file_path" >&2
    exit 1
  fi
}

# Проверяет, что номер прогона — положительное целое число
validate_run_number() {
  local value="$1"
  case "$value" in
    ''|*[!0-9]*|0)
      echo "ERROR: Run number must be a positive integer, got: $value" >&2
      exit 1
      ;;
  esac
}

# Преобразует относительный путь к файлу в абсолютный
resolve_absolute_path() {
  local file_path="$1"
  local dir_part
  local base_part

  dir_part=$(cd "$(dirname "$file_path")" && pwd)
  base_part=$(basename "$file_path")
  echo "${dir_part}/${base_part}"
}

# --- Извлечение метрик ------------------------------------------------------

# Извлекает заголовки этапов и сохраняет названия в tmp-файл
# Записывает количество этапов в файл $stages_file.count
extract_stages() {
  local content="$1"
  local stages_file="$2"
  local MAX_STAGE_NAME_LENGTH=50

  # Ищем заголовки этапов по паттернам из спецификации
  local stage_headers
  stage_headers=$(echo "$content" | grep -i -E '^#{2,3}[[:space:]]+(Этап|Stage|Фаза|Step)[[:space:]]+[0-9]|^###[[:space:]]+[0-9]+\.[0-9]+' || true)

  if [ -z "$stage_headers" ]; then
    # Пустой файл stages — jq корректно обработает как пустой массив
    : > "$stages_file"
    echo "0"
    return
  fi

  local stages_count
  stages_count=$(echo "$stage_headers" | wc -l | tr -d ' ')

  # Извлекаем названия этапов в kebab-case и сохраняем в файл
  echo "$stage_headers" | while IFS= read -r line; do
    # Убираем символы # и пробелы в начале
    local name
    name=$(echo "$line" | sed -E 's/^#*[[:space:]]*//')
    # Убираем ключевое слово (Этап/Stage/Фаза/Step) и нумерацию
    name=$(echo "$name" | sed -E 's/^(Этап|Stage|Фаза|Step)[[:space:]]*[0-9.]*[:.]?[[:space:]]*//')
    # Если это подэтап вида "N.N текст" — убираем нумерацию
    name=$(echo "$name" | sed -E 's/^[0-9]+\.[0-9]+[.:[:space:]]*//')
    # Приводим к нижнему регистру
    name=$(echo "$name" | tr '[:upper:]' '[:lower:]')
    # Заменяем пробелы на дефисы
    name=$(echo "$name" | sed -E 's/[[:space:]]/-/g')
    # Убираем всё кроме букв (латиница + кириллица), цифр и дефисов
    name=$(echo "$name" | sed -E 's/[^a-zа-яё0-9-]//g')
    # Убираем начальные и конечные дефисы, а также двойные дефисы
    name=$(echo "$name" | sed -E 's/^-+//; s/-+$//; s/-{2,}/-/g')

    # Обрезаем длинные названия до MAX_STAGE_NAME_LENGTH символов
    if [ "${#name}" -gt "$MAX_STAGE_NAME_LENGTH" ]; then
      name=$(echo "$name" | cut -c1-"$MAX_STAGE_NAME_LENGTH")
      # Убираем неполное слово в конце (отрезаем по последнему дефису)
      name=$(echo "$name" | sed -E 's/-[^-]*$//')
    fi

    echo "$name"
  done > "$stages_file"

  echo "$stages_count"
}

# Подсчитывает количество уникальных агентов в спецификации
count_agents() {
  local content="$1"

  # Приоритет 1: ищем имена файлов агентов (agents/*.md)
  local agents_by_files
  agents_by_files=$(echo "$content" | grep -o -i -E 'agents/[a-z-]+\.md' | sort -u | wc -l | tr -d ' ')

  if [ "$agents_by_files" -gt 0 ]; then
    echo "$agents_by_files"
    return
  fi

  # Приоритет 2: ищем текстовые упоминания агентов
  local agents_by_text
  agents_by_text=$(echo "$content" | grep -i -E '\*\*тип:\*\*[[:space:]]*агент|тип:[[:space:]]*агент|^#{2,4}.*(агент|agent)' | sort -u | wc -l | tr -d ' ')

  echo "$agents_by_text"
}

# Извлекает названия скиллов и сохраняет в tmp-файл
extract_skills() {
  local content="$1"
  local skills_file="$2"

  # Шаг 1: извлечь имена из путей .claude/skills/{имя}
  local skills_from_paths
  skills_from_paths=$(echo "$content" | grep -o -E '\.claude/skills/[a-z][a-z0-9-]+' | sed 's|.claude/skills/||' | sort -u || true)

  # Шаг 2: извлечь имена из текстовых упоминаний «скилл X» / «skill X»
  local skills_from_text
  skills_from_text=$(echo "$content" | grep -i -o -E '(скилл|skill)[[:space:]]+[«"]?[a-z][a-z0-9-]+[»"]?' | sed -E 's/^.*[[:space:]]//' | tr -d '«»""' | sort -u || true)

  # Шаг 3: объединить, убрать дубли, исключить pipeline-designer
  {
    if [ -n "$skills_from_paths" ]; then echo "$skills_from_paths"; fi
    if [ -n "$skills_from_text" ]; then echo "$skills_from_text"; fi
  } | sort -u | grep -v '^pipeline-designer$' > "$skills_file" || true

  # Если файл не создан (оба источника пустые), создать пустой
  if [ ! -f "$skills_file" ]; then
    : > "$skills_file"
  fi
}

# Проверяет наличие упоминаний бэкапа в тексте
check_has_backup() {
  local content="$1"
  local count
  count=$(echo "$content" | grep -i -c -E 'бэкап|backup|резервн|back.?up' || true)

  if [ "$count" -gt 0 ]; then
    echo "true"
  else
    echo "false"
  fi
}

# Проверяет наличие упоминаний отката/rollback в тексте
check_has_rollback() {
  local content="$1"
  local count
  count=$(echo "$content" | grep -i -c -E 'откат|rollback|roll.?back|восстановлен|revert' || true)

  if [ "$count" -gt 0 ]; then
    echo "true"
  else
    echo "false"
  fi
}

# Проверяет наличие описания обработки ошибок в тексте
check_has_error_handling() {
  local content="$1"
  local count
  count=$(echo "$content" | grep -i -c -E 'обработка ошиб|error.?handl|при ошибке|при сбое|при провале|fallback|обработка.*(ошиб|исключ)' || true)

  if [ "$count" -gt 0 ]; then
    echo "true"
  else
    echo "false"
  fi
}

# Подсчитывает количество промптов (заголовки с «промпт»/«prompt»)
count_prompts() {
  local content="$1"
  local prompt_headers_count
  prompt_headers_count=$(echo "$content" | grep -i -c -E '^#+.*(промпт|prompt)' || true)

  echo "$prompt_headers_count"
}

# Подсчитывает количество пунктов чеклиста (- [ ] или - [x])
count_checklist_items() {
  local content="$1"
  local count
  count=$(echo "$content" | grep -c -E '^[[:space:]]*- \[(x| )\] ' || true)

  echo "$count"
}

# --- Конвертация txt -> JSON-массив -----------------------------------------

# Конвертирует текстовый файл (строка на элемент) в JSON-массив
convert_txt_to_json_array() {
  local txt_file="$1"
  local json_file="$2"

  jq -R . < "$txt_file" | jq -s . > "$json_file"
}

# --- Главная функция --------------------------------------------------------

main() {
  # Шаг 1: проверка зависимостей
  check_jq_installed

  # Шаг 2: валидация аргументов
  if [ $# -lt 1 ] || [ -z "${1:-}" ]; then
    show_usage_and_exit
  fi

  validate_input_file "$1"

  # Определяем номер прогона (по умолчанию 1)
  local run_number=1
  if [ $# -ge 2 ]; then
    validate_run_number "$2"
    run_number="$2"
  fi

  # Преобразуем путь к файлу в абсолютный
  local spec_file
  spec_file=$(resolve_absolute_path "$1")

  # Шаг 3: создание временной директории (используем глобальную TMP_DIR)
  TMP_DIR=$(mktemp -d) || {
    echo "ERROR: Cannot create temporary directory" >&2
    exit 2
  }
  trap cleanup EXIT INT TERM

  # Шаг 4: чтение содержимого файла
  local content
  content=$(cat "$spec_file")

  # Шаг 5: извлечение метрик
  local stages_count
  stages_count=$(extract_stages "$content" "$TMP_DIR/stages.txt")

  local agents_count
  agents_count=$(count_agents "$content")

  extract_skills "$content" "$TMP_DIR/skills.txt"

  local has_backup
  has_backup=$(check_has_backup "$content")

  local has_rollback
  has_rollback=$(check_has_rollback "$content")

  local has_error_handling
  has_error_handling=$(check_has_error_handling "$content")

  local prompts_count
  prompts_count=$(count_prompts "$content")

  local checklist_items
  checklist_items=$(count_checklist_items "$content")

  # Шаг 5.9: конвертация txt -> JSON-массивы
  convert_txt_to_json_array "$TMP_DIR/stages.txt" "$TMP_DIR/stages.json"
  convert_txt_to_json_array "$TMP_DIR/skills.txt" "$TMP_DIR/skills.json"

  # Шаг 6: формирование итогового JSON
  local json_output
  json_output=$(jq -n \
    --argjson run "$run_number" \
    --argjson stages_count "$stages_count" \
    --slurpfile stages "$TMP_DIR/stages.json" \
    --argjson agents_count "$agents_count" \
    --slurpfile skills "$TMP_DIR/skills.json" \
    --argjson has_backup "$has_backup" \
    --argjson has_rollback "$has_rollback" \
    --argjson has_error_handling "$has_error_handling" \
    --argjson prompts_count "$prompts_count" \
    --argjson checklist_items "$checklist_items" \
    '{
      run: $run,
      stages_count: $stages_count,
      stages: $stages[0],
      agents_count: $agents_count,
      skills_needed: $skills[0],
      has_backup: $has_backup,
      has_rollback: $has_rollback,
      has_error_handling: $has_error_handling,
      prompts_count: $prompts_count,
      checklist_items: $checklist_items
    }') || {
    echo "ERROR: Failed to generate JSON output" >&2
    exit 2
  }

  # Шаг 7: вывод JSON в stdout
  echo "$json_output"
}

main "$@"
