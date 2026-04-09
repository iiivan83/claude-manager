#!/usr/bin/env bash

# --- Шаг 1. Определение корнев��го пути проекта ---
# Если пер��ый аргумент не передан, используем текущую директорию
project_root="${1:-.}"

# --- Шаг 1b. Получение пути к директории логов из аргумента ---
# Путь передаётся оркестратором, который извлекает его из глобального референса
if [ -z "${2:-}" ]; then
  echo "ERROR: log-base-dir argument is required" >&2
  echo "Usage: ensure-log-dir.sh <project-root> <log-base-dir>" >&2
  echo "Example: ensure-log-dir.sh . dev/docs/logs/skills-modifications" >&2
  exit 1
fi
LOG_DIR_RELATIVE_PATH="$2"

# Сохраняем исходное значение для сообщений об ошибках (до преобразования в абсолютный путь)
original_path="$project_root"

# --- Шаг 2. Валидация корневого пути ---
if [ ! -e "$project_root" ]; then
  echo "ERROR: Path not found: $project_root" >&2
  exit 1
fi

if [ ! -d "$project_root" ]; then
  echo "ERROR: Not a directory: $project_root" >&2
  exit 1
fi

# --- Шаг 3. Преобразование пути в абсолютный ---
project_root="$(cd "$project_root" 2>/dev/null && pwd)"

if [ -z "$project_root" ]; then
  echo "ERROR: Path not found: $original_path" >&2
  exit 1
fi

# --- Шаг 4. Формирование целевого пути ---
target_dir="${project_root}/${LOG_DIR_RELATIVE_PATH}"

# --- Шаг 5. Проверка существования целевой директории ---
if [ -d "$target_dir" ]; then
  echo "ensure-log-dir: directory already exists: $target_dir"
  exit 0
fi

# --- Шаг 6. Создание директории ---
if mkdir -p "$target_dir"; then
  echo "ensure-log-dir: directory created: $target_dir"
  exit 0
else
  echo "ERROR: Failed to create directory: $target_dir" >&2
  exit 2
fi
