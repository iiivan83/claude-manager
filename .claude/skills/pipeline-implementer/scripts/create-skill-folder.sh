#!/usr/bin/env bash
set -euo pipefail

# Скрипт создаёт папку нового скилла со стандартной структурой подкаталогов
# (agents/, scripts/, references/, evals/) и инициализирует orchestrator-log.json
# в директории логов.
# Вывод — JSON с результатом операции в stdout.

# --- Константы ---

# Минимальная допустимая длина имени
MIN_NAME_LENGTH=3
# Максимальная допустимая длина имени
MAX_NAME_LENGTH=100
# Подкаталоги, которые создаются внутри папки скилла
SKILL_SUBDIRECTORIES=("agents" "scripts" "references" "evals")
# Код ошибки: папка уже существует
EXIT_CODE_FOLDER_EXISTS=10
# Код ошибки: некорректные аргументы
EXIT_CODE_INVALID_ARGS=1
# Код ошибки: ошибка файловой системы
EXIT_CODE_FS_ERROR=2
# Код ошибки: отсутствует зависимость (jq)
EXIT_CODE_MISSING_DEPENDENCY=3

# --- Вспомогательные функции ---

# Выводит сообщение об ошибке в stderr
print_error() {
  echo "$1" >&2
}

# Проверяет, что jq установлен в системе
check_jq_installed() {
  if ! command -v jq >/dev/null 2>&1; then
    print_error "ERROR: jq is not installed. Install it: brew install jq"
    exit "$EXIT_CODE_MISSING_DEPENDENCY"
  fi
}

# Показывает справку по использованию скрипта
print_usage() {
  print_error "Usage: create-skill-folder.sh <pipeline-name> <project-path> <skill-name> <log-path> [--overwrite] [--suffix <suffix>]"
  print_error ""
  print_error "Arguments:"
  print_error "  pipeline-name   Имя пайплайна (a-z, 0-9, дефис)"
  print_error "  project-path    Путь к корню проекта"
  print_error "  skill-name      Имя создаваемого скилла"
  print_error "  log-path        Относительный путь к директории логов"
  print_error ""
  print_error "Flags:"
  print_error "  --overwrite     Перезаписать существующую папку скилла"
  print_error "  --suffix <s>    Добавить суффикс к имени папки скилла"
  print_error ""
  print_error "Example: create-skill-folder.sh my-pipeline . my-skill dev/docs/logs/skills-modifications"
}

# --- Функции валидации ---

# Проверяет, что имя содержит только допустимые символы: a-z, 0-9, дефис
validate_allowed_characters() {
  local name="$1"
  local label="$2"
  if ! echo "$name" | grep -qE '^[a-z0-9-]+$'; then
    print_error "ERROR: ${label} contains invalid characters: ${name}. Allowed: a-z, 0-9, hyphen"
    exit "$EXIT_CODE_INVALID_ARGS"
  fi
}

# Проверяет, что имя не начинается и не заканчивается дефисом, и не содержит двойных дефисов
validate_no_bad_hyphens() {
  local name="$1"
  local label="$2"
  if [[ "$name" == -* ]]; then
    print_error "ERROR: ${label} must not start with a hyphen: ${name}"
    exit "$EXIT_CODE_INVALID_ARGS"
  fi
  if [[ "$name" == *- ]]; then
    print_error "ERROR: ${label} must not end with a hyphen: ${name}"
    exit "$EXIT_CODE_INVALID_ARGS"
  fi
  if echo "$name" | grep -q '\-\-'; then
    print_error "ERROR: ${label} must not contain double hyphens: ${name}"
    exit "$EXIT_CODE_INVALID_ARGS"
  fi
}

# Проверяет, что длина имени в допустимом диапазоне
validate_name_length() {
  local name="$1"
  local label="$2"
  local length=${#name}
  if [ "$length" -lt "$MIN_NAME_LENGTH" ] || [ "$length" -gt "$MAX_NAME_LENGTH" ]; then
    print_error "ERROR: ${label} must be ${MIN_NAME_LENGTH}-${MAX_NAME_LENGTH} characters long, got ${length}: ${name}"
    exit "$EXIT_CODE_INVALID_ARGS"
  fi
}

# Выполняет все проверки имени (символы, дефисы, длина)
validate_name() {
  local name="$1"
  local label="$2"
  validate_allowed_characters "$name" "$label"
  validate_no_bad_hyphens "$name" "$label"
  validate_name_length "$name" "$label"
}

# --- Функции работы с путями ---

# Определяет и валидирует корневую директорию проекта, возвращает абсолютный путь
resolve_project_path() {
  local raw_path="$1"

  if [ ! -e "$raw_path" ]; then
    print_error "ERROR: Project path not found: ${raw_path}"
    exit "$EXIT_CODE_INVALID_ARGS"
  fi

  if [ ! -d "$raw_path" ]; then
    print_error "ERROR: Project path is not a directory: ${raw_path}"
    exit "$EXIT_CODE_INVALID_ARGS"
  fi

  # Преобразуем в абсолютный путь через subshell, чтобы не менять cwd скрипта
  cd "$raw_path" && pwd
}

# --- Функции генерации временных меток ---

# Генерирует временную метку для имён папок: ДД.ММ_ЧЧ.МИН (например, 06.04_17.30)
generate_folder_timestamp() {
  date '+%d.%m_%H.%M'
}

# Генерирует временную метку для имён файлов: ДД-ММ-ЧЧ-МИН (например, 06-04-17-30)
generate_file_timestamp() {
  date '+%d-%m-%H-%M'
}

# --- Функции создания структуры ---

# Создаёт подкаталоги внутри папки скилла (agents/, scripts/, references/, evals/)
create_skill_subdirectories() {
  local skill_directory="$1"

  for subdirectory in "${SKILL_SUBDIRECTORIES[@]}"; do
    if ! mkdir -p "${skill_directory}/${subdirectory}"; then
      print_error "ERROR: Failed to create subdirectory: ${skill_directory}/${subdirectory}"
      exit "$EXIT_CODE_FS_ERROR"
    fi
  done
}

# Создаёт начальный файл orchestrator-log.json в директории логов
create_orchestrator_log() {
  local log_directory="$1"
  local pipeline_name="$2"
  local skill_name="$3"
  local folder_timestamp="$4"
  local file_timestamp="$5"

  # Убеждаемся, что директория логов существует
  if ! mkdir -p "$log_directory"; then
    print_error "ERROR: Failed to create log directory: ${log_directory}"
    exit "$EXIT_CODE_FS_ERROR"
  fi

  local log_file="${log_directory}/orchestrator-log.json"

  if ! jq -n \
    --arg pipeline "$pipeline_name" \
    --arg skill "$skill_name" \
    --arg created_at "$(date '+%Y-%m-%dT%H:%M:%S')" \
    --arg folder_timestamp "$folder_timestamp" \
    --arg file_timestamp "$file_timestamp" \
    '{
      pipeline: $pipeline,
      skill: $skill,
      created_at: $created_at,
      folder_timestamp: $folder_timestamp,
      file_timestamp: $file_timestamp,
      steps: []
    }' > "$log_file"; then
    print_error "ERROR: Failed to write orchestrator-log.json in: ${log_directory}"
    exit "$EXIT_CODE_FS_ERROR"
  fi
}

# Удаляет папку скилла, если она существует (для режима --overwrite)
remove_existing_skill_directory() {
  local skill_directory="$1"
  if [ -d "$skill_directory" ]; then
    if ! rm -rf "$skill_directory"; then
      print_error "ERROR: Failed to remove existing directory: ${skill_directory}"
      exit "$EXIT_CODE_FS_ERROR"
    fi
  fi
}

# Выводит JSON с результатом успешного создания
print_success_result() {
  local skill_directory="$1"
  local log_directory="$2"
  local folder_timestamp="$3"
  local file_timestamp="$4"

  jq -n \
    --arg status "created" \
    --arg skill_path "$skill_directory" \
    --arg log_path "$log_directory" \
    --arg folder_timestamp "$folder_timestamp" \
    --arg file_timestamp "$file_timestamp" \
    '{
      status: $status,
      skill_path: $skill_path,
      log_path: $log_path,
      folder_timestamp: $folder_timestamp,
      file_timestamp: $file_timestamp
    }'
}

# --- Разбор аргументов командной строки ---

# Флаг перезаписи: если true, существующая папка будет удалена и создана заново
overwrite_mode=false
# Суффикс: если указан, добавляется к имени папки скилла (например, my-skill-v2)
name_suffix=""
# Позиционные аргументы (без флагов) собираются в массив
positional_args=()

# Разбираем аргументы: отделяем флаги (--overwrite, --suffix) от позиционных
while [ $# -gt 0 ]; do
  case "$1" in
    --overwrite)
      overwrite_mode=true
      shift
      ;;
    --suffix)
      if [ -z "${2:-}" ]; then
        print_error "ERROR: --suffix requires a value"
        print_usage
        exit "$EXIT_CODE_INVALID_ARGS"
      fi
      name_suffix="$2"
      shift 2
      ;;
    --help|-h)
      print_usage
      exit 0
      ;;
    -*)
      print_error "ERROR: Unknown flag: $1"
      print_usage
      exit "$EXIT_CODE_INVALID_ARGS"
      ;;
    *)
      positional_args+=("$1")
      shift
      ;;
  esac
done

# --- Основной алгоритм ---

# Шаг 1. Проверяем, что jq установлен (нужен для создания JSON-вывода)
check_jq_installed

# Шаг 2. Проверяем, что переданы все обязательные аргументы
if [ "${#positional_args[@]}" -lt 4 ]; then
  print_error "ERROR: Missing required arguments (expected 4, got ${#positional_args[@]})"
  print_usage
  exit "$EXIT_CODE_INVALID_ARGS"
fi

pipeline_name="${positional_args[0]}"
raw_project_path="${positional_args[1]}"
skill_name="${positional_args[2]}"
log_relative_path="${positional_args[3]}"

# Шаг 3. Валидация имён (только допустимые символы, длина, дефисы)
validate_name "$pipeline_name" "Pipeline name"
validate_name "$skill_name" "Skill name"

# Если передан суффикс — проверяем и его
if [ -n "$name_suffix" ]; then
  validate_name "$name_suffix" "Suffix"
fi

# Шаг 4. Определяем абсолютный путь к проекту
project_path=$(resolve_project_path "$raw_project_path")

# Шаг 5. Генерируем временные метки из текущего момента
folder_timestamp=$(generate_folder_timestamp)
file_timestamp=$(generate_file_timestamp)

# Шаг 6. Формируем путь к папке скилла
# Если есть суффикс, добавляем его через дефис (например, my-skill-v2)
if [ -n "$name_suffix" ]; then
  full_skill_name="${skill_name}-${name_suffix}"
else
  full_skill_name="$skill_name"
fi

skill_directory="${project_path}/.claude/skills/${full_skill_name}"

# Шаг 7. Формируем путь к директории логов
log_directory="${project_path}/${log_relative_path}"

# Шаг 8. Проверяем, не существует ли уже папка скилла
if [ -d "$skill_directory" ]; then
  if [ "$overwrite_mode" = true ]; then
    # Режим перезаписи: удаляем старую папку и продолжаем создание
    remove_existing_skill_directory "$skill_directory"
  else
    # Папка уже есть, а перезапись не запрошена — возвращаем ошибку как JSON
    # Оркестратор получит этот JSON и решит, что делать (спросит пользователя)
    jq -n \
      --arg error "folder_exists" \
      --arg path "$skill_directory" \
      '{error: $error, path: $path}'
    exit "$EXIT_CODE_FOLDER_EXISTS"
  fi
fi

# Шаг 9. Создаём папку скилла с подкаталогами (agents/, scripts/, references/, evals/)
create_skill_subdirectories "$skill_directory"

# Шаг 10. Создаём orchestrator-log.json в директории логов
create_orchestrator_log "$log_directory" "$pipeline_name" "$full_skill_name" "$folder_timestamp" "$file_timestamp"

# Шаг 11. Выводим результат — JSON с путями и временными метками
print_success_result "$skill_directory" "$log_directory" "$folder_timestamp" "$file_timestamp"
