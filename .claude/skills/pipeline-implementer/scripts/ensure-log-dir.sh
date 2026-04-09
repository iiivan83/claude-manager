#!/bin/bash
set -euo pipefail

# Скрипт создаёт структуру папок для логов пайплайна.
# Безопасен для повторного запуска — если папки уже есть, ничего не сломается.
#
# Использование:
#   ./ensure-log-dir.sh <путь-к-проекту> <путь-к-логам>
#
# Аргументы:
#   project-path — корневая папка проекта (используется для проверки существования)
#   log-path     — папка, внутри которой будет создана структура логов
#
# Вывод (JSON):
#   Успех: {"status": "success", "log_dir": "<путь>", "created_dirs": [...]}
#   Ошибка: {"status": "error", "message": "..."}

# --- Названия подпапок для логов ---
# Каждая подпапка хранит определённый тип артефактов пайплайна
SUBDIRECTORIES=(
  "agent-outputs"          # Результаты работы агентов
  "test-reports"           # Отчёты о прохождении тестов
  "fix-cycle"              # Логи циклов исправления ошибок
  "dry-run-test-results"   # Результаты пробных запусков
  "completeness-metrics"   # Метрики полноты реализации
  "effectiveness-eval"     # Оценка эффективности пайплайна
)

REQUIRED_ARGUMENT_COUNT=2

# --- Вспомогательные функции ---

# Печатает JSON-ошибку в stdout и завершает скрипт
print_error_and_exit() {
  local message="$1"
  echo "{\"status\": \"error\", \"message\": \"${message}\"}"
  exit 1
}

# Собирает JSON-массив из списка путей
build_json_array_from_paths() {
  local paths=("$@")
  local json_array="["
  local first_element=true

  for path in "${paths[@]}"; do
    if [ "$first_element" = true ]; then
      first_element=false
    else
      json_array+=", "
    fi
    json_array+="\"${path}\""
  done

  json_array+="]"
  echo "$json_array"
}

# --- Проверка аргументов ---

if [ "$#" -ne "$REQUIRED_ARGUMENT_COUNT" ]; then
  print_error_and_exit "Ожидается 2 аргумента: <путь-к-проекту> <путь-к-логам>. Получено: $#"
fi

project_path="$1"
log_path="$2"

# Проверяем, что папка проекта существует
if [ ! -d "$project_path" ]; then
  print_error_and_exit "Папка проекта не найдена: ${project_path}"
fi

# --- Создание структуры папок ---

# Создаём корневую папку логов (mkdir -p не падает, если папка уже есть)
if ! mkdir -p "$log_path" 2>/dev/null; then
  print_error_and_exit "Не удалось создать папку логов: ${log_path}"
fi

created_directories=()

for subdirectory_name in "${SUBDIRECTORIES[@]}"; do
  full_path="${log_path}/${subdirectory_name}"

  if ! mkdir -p "$full_path" 2>/dev/null; then
    print_error_and_exit "Не удалось создать подпапку: ${full_path}"
  fi

  created_directories+=("$full_path")
done

# --- Вывод результата ---

directories_json=$(build_json_array_from_paths "${created_directories[@]}")
echo "{\"status\": \"success\", \"log_dir\": \"${log_path}\", \"created_dirs\": ${directories_json}}"
