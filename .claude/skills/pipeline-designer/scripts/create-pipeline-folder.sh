#!/usr/bin/env bash
set -euo pipefail

# Скрипт создаёт папку пайплайна со стандартной структурой подкаталогов
# и инициализированным JSON-логом оркестратора.
# Выводит в stdout абсолютный путь к созданной (или существующей) папке.

# --- Константы ---

# Минимальная допустимая длина имени пайплайна
MIN_NAME_LENGTH=3
# Максимальная допустимая длина имени пайплайна
MAX_NAME_LENGTH=100
# Относительный путь от корня проекта до папки с пайплайнами — передаётся как 4-й аргумент
# Оркестратор извлекает этот путь из глобального референса document-naming-and-placement.md
PIPELINES_BASE_DIR=""

# --- Функции ---

# Выводит сообщение об ошибке в stderr
print_error() {
  echo "$1" >&2
}

# Проверяет, что jq установлен в системе
check_jq_installed() {
  if ! command -v jq >/dev/null 2>&1; then
    print_error "ERROR: jq is not installed. Install it: brew install jq"
    exit 3
  fi
}

# Показывает справку по использованию скрипта
print_usage() {
  print_error "Usage: create-pipeline-folder.sh <pipeline-name> [project-root] <skill-name> <pipelines-base-dir>"
  print_error "Example: create-pipeline-folder.sh my-pipeline . pipeline-designer dev/docs/logs/skills-modifications"
}

# Проверяет, что имя содержит только допустимые символы: a-z, 0-9, дефис
validate_allowed_characters() {
  local name="$1"
  if ! echo "$name" | grep -qE '^[a-z0-9-]+$'; then
    print_error "ERROR: Pipeline name contains invalid characters: ${name}. Allowed: a-z, 0-9, hyphen"
    exit 1
  fi
}

# Генерирует префикс даты-времени в формате ДД.ММ_ЧЧ.МИН (например, 03.04_14.30)
generate_datetime_prefix() {
  date '+%d.%m_%H.%M'
}

# Проверяет, что имя не начинается с дефиса
validate_no_leading_hyphen() {
  local name="$1"
  if [[ "$name" == -* ]]; then
    print_error "ERROR: Pipeline name must not start with a hyphen: ${name}"
    exit 1
  fi
}

# Проверяет, что имя не заканчивается дефисом
validate_no_trailing_hyphen() {
  local name="$1"
  if [[ "$name" == *- ]]; then
    print_error "ERROR: Pipeline name must not end with a hyphen: ${name}"
    exit 1
  fi
}

# Проверяет, что имя не содержит двойных дефисов
validate_no_double_hyphens() {
  local name="$1"
  if echo "$name" | grep -q '\-\-'; then
    print_error "ERROR: Pipeline name must not contain double hyphens: ${name}"
    exit 1
  fi
}

# Проверяет, что длина имени в допустимом диапазоне (3-100 символов)
validate_name_length() {
  local name="$1"
  local length=${#name}
  if [ "$length" -lt "$MIN_NAME_LENGTH" ] || [ "$length" -gt "$MAX_NAME_LENGTH" ]; then
    print_error "ERROR: Pipeline name must be 3-100 characters long, got ${length}: ${name}"
    exit 1
  fi
}

# Выполняет все проверки имени пайплайна в порядке из спецификации
validate_pipeline_name() {
  local name="$1"
  validate_allowed_characters "$name"
  validate_no_leading_hyphen "$name"
  validate_no_trailing_hyphen "$name"
  validate_no_double_hyphens "$name"
  validate_name_length "$name"
}

# Определяет и валидирует корневую директорию проекта
resolve_project_root() {
  local raw_path="$1"

  # Проверяем существование пути
  if [ ! -e "$raw_path" ]; then
    print_error "ERROR: Project root directory not found: ${raw_path}"
    exit 1
  fi

  # Проверяем, что это директория, а не файл
  if [ ! -d "$raw_path" ]; then
    print_error "ERROR: Project root is not a directory: ${raw_path}"
    exit 1
  fi

  # Преобразуем в абсолютный путь через subshell, чтобы не менять cwd скрипта
  project_root=$(cd "$raw_path" && pwd)
}

# Создаёт структуру подкаталогов внутри папки пайплайна
create_directory_structure() {
  local pipeline_dir="$1"
  if ! mkdir -p \
    "${pipeline_dir}/agent-outputs" \
    "${pipeline_dir}/test-reports" \
    "${pipeline_dir}/dry-run-test-results/artifacts" \
    "${pipeline_dir}/eval-test-results" \
    "${pipeline_dir}/fix-cycle"; then
    print_error "ERROR: Failed to create directory structure in: ${pipeline_dir}"
    exit 2
  fi
}

# Создаёт файл orchestrator-log.json с начальной структурой
create_orchestrator_log() {
  local pipeline_dir="$1"
  local pipeline_name="$2"
  if ! jq -n \
    --arg pipeline "$pipeline_name" \
    --arg skill "$skill_name" \
    --arg created_at "$(date '+%Y-%m-%dT%H:%M:%S')" \
    '{pipeline: $pipeline, initiated_by_skill: $skill, created_at: $created_at, steps: []}' \
    > "${pipeline_dir}/orchestrator-log.json"; then
    print_error "ERROR: Failed to write orchestrator-log.json in: ${pipeline_dir}"
    rm -rf "${pipeline_dir}"
    exit 2
  fi
}

# --- Основной алгоритм ---

# Шаг 1. Проверка зависимостей
check_jq_installed

# Шаг 2. Разбор и валидация имени пайплайна ($1)
if [ -z "${1:-}" ]; then
  print_usage
  exit 1
fi

pipeline_name="$1"
validate_pipeline_name "$pipeline_name"

# Шаг 3. Разбор и валидация корня проекта ($2)
raw_project_root="${2:-$(pwd)}"
resolve_project_root "$raw_project_root"

# Шаг 4. Разбор и валидация имени скилла ($3)
if [ -z "${3:-}" ]; then
  print_error "ERROR: skill-name is required"
  print_usage
  exit 1
fi

skill_name="$3"
validate_pipeline_name "$skill_name"

# Шаг 4b. Разбор и валидация базового пути к пайплайнам ($4)
# Этот путь передаётся оркестратором, который извлекает его из глобального референса
if [ -z "${4:-}" ]; then
  print_error "ERROR: pipelines-base-dir is required"
  print_usage
  exit 1
fi
PIPELINES_BASE_DIR="$4"

# Шаг 5. Формирование це��евого пути (дата-время + имя скилла + имя пайплайна)
datetime_prefix=$(generate_datetime_prefix)
folder_name="${datetime_prefix}-${skill_name}-${pipeline_name}"
pipeline_dir="${project_root}/${PIPELINES_BASE_DIR}/${folder_name}"

# Шаг 6. Проверка идемпотентности
if [ -e "$pipeline_dir" ]; then
  if [ -d "$pipeline_dir" ]; then
    # Папка уже существует — возвращаем путь без изменений
    echo "$pipeline_dir"
    exit 0
  else
    # По пути существует файл, а не директория
    print_error "ERROR: Path exists but is not a directory: ${pipeline_dir}"
    exit 2
  fi
fi

# Шаг 7. Создание структуры директорий
create_directory_structure "$pipeline_dir"

# Шаг 8. Инициализация orchestrator-log.json
create_orchestrator_log "$pipeline_dir" "$pipeline_name"

# Шаг 9. Вывод результата
echo "$pipeline_dir"
