#!/usr/bin/env bash
set -euo pipefail

# Скрипт архивирует результаты работы pipeline-explorer в папку логов.
# Создаёт папку с датой/временем в начале имени, копирует туда:
# - рабочий пайплайн (pipeline/)
# - все логи из рабочей папки
# - полную копию рабочей папки (pipeline-workspace/)
#
# Использование:
#   archive-pipeline-run.sh <task-name> <project-root> <workspace-path> <pipeline-path>
#
# Пример:
#   archive-pipeline-run.sh google-sheets-etl /Users/ivan/project ./pipeline-workspace ./pipeline

# --- Константы ---

# Путь к папке логов относительно корня проекта (совпадает с pipeline-designer)
LOGS_BASE_DIR="dev/docs/logs/skills-modifications"
# Префикс имени скилла для папки архива
SKILL_PREFIX="pipeline-explorer"

# --- Функции ---

# Выводит сообщение об ошибке в stderr (стандартный поток ошибок)
print_error() {
  echo "ERROR: $1" >&2
}

# Показывает справку по использованию скрипта
print_usage() {
  echo "Usage: archive-pipeline-run.sh <task-name> <project-root> <workspace-path> <pipeline-path>" >&2
}

# Проверяет, что переданный путь — существующая директория
validate_directory() {
  local path="$1"
  local label="$2"

  if [ ! -e "$path" ]; then
    print_error "${label} not found: ${path}"
    exit 1
  fi

  if [ ! -d "$path" ]; then
    print_error "${label} is not a directory: ${path}"
    exit 1
  fi
}

# Преобразует путь в абсолютный через переход в директорию
resolve_absolute_path() {
  local raw_path="$1"
  cd "$raw_path" && pwd
}

# --- Основной алгоритм ---

# Шаг 1. Проверка аргументов
if [ "$#" -lt 4 ]; then
  print_error "Expected 4 arguments, got $#"
  print_usage
  exit 1
fi

task_name="$1"
raw_project_root="$2"
raw_workspace_path="$3"
raw_pipeline_path="$4"

# Шаг 2. Валидация всех путей
validate_directory "$raw_project_root" "Project root"
validate_directory "$raw_workspace_path" "Workspace"
validate_directory "$raw_pipeline_path" "Pipeline"

# Шаг 3. Преобразование в абсолютные пути
project_root=$(resolve_absolute_path "$raw_project_root")
workspace_path=$(resolve_absolute_path "$raw_workspace_path")
pipeline_path=$(resolve_absolute_path "$raw_pipeline_path")

# Шаг 4. Формирование имени папки архива с датой/временем в начале
# Формат: ДД.ММ_ЧЧ.МИН (день.месяц_час.минута)
timestamp=$(date '+%d.%m_%H.%M')
archive_folder_name="${timestamp}-${SKILL_PREFIX}-${task_name}"
archive_dir="${project_root}/${LOGS_BASE_DIR}/${archive_folder_name}"

# Шаг 5. Проверка идемпотентности — если папка уже есть, выходим
if [ -d "$archive_dir" ]; then
  echo "archive-pipeline-run: archive already exists: ${archive_dir}"
  echo "$archive_dir"
  exit 0
fi

# Шаг 6. Создание базовой директории логов (если не существует)
mkdir -p "${project_root}/${LOGS_BASE_DIR}"

# Шаг 7. Создание папки архива со структурой подкаталогов
mkdir -p "${archive_dir}/pipeline"
mkdir -p "${archive_dir}/workspace"
mkdir -p "${archive_dir}/logs"

# Шаг 8. Копирование рабочего пайплайна (готовый код, который выполняет задачу)
cp -R "${pipeline_path}/"* "${archive_dir}/pipeline/" 2>/dev/null || true
echo "archive-pipeline-run: pipeline copied"

# Шаг 9. Копирование логов отдельно для быстрого доступа
# orchestrator-log.json — главный лог хода работы
if [ -f "${workspace_path}/orchestrator-log.json" ]; then
  cp "${workspace_path}/orchestrator-log.json" "${archive_dir}/logs/"
fi

# failure-log — журнал неудачных попыток
if [ -d "${workspace_path}/failure-log" ]; then
  cp -R "${workspace_path}/failure-log" "${archive_dir}/logs/"
fi

# agent-outputs — результаты работы каждого агента
if [ -d "${workspace_path}/agent-outputs" ]; then
  cp -R "${workspace_path}/agent-outputs" "${archive_dir}/logs/"
fi

echo "archive-pipeline-run: logs copied"

# Шаг 10. Копирование полной рабочей папки (pipeline-workspace) целиком
cp -R "${workspace_path}/"* "${archive_dir}/workspace/" 2>/dev/null || true
echo "archive-pipeline-run: workspace copied"

# Шаг 11. Вывод пути к созданному архиву
echo "archive-pipeline-run: archive created: ${archive_dir}"
echo "$archive_dir"
