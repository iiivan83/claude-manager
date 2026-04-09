#!/bin/bash
set -euo pipefail

# Локаль для корректной работы с кириллицей в выводе Claude
export LC_ALL=en_US.UTF-8

# --- Константы ---

# Таймаут ожидания Claude (10 минут)
TIMEOUT_SECONDS=600

# Exit code при убийстве процесса по SIGTERM (128 + 15)
SIGTERM_EXIT_CODE=143

# Имя файла с результатами dry-run теста
RESULT_FILENAME="dry-run-result.json"

# Имя файла с инструкцией для тестирующего агента (внутри папки скилла)
TESTER_AGENT_PATH="agents/dry-run-tester.md"

# Имя главного файла скилла
SKILL_ENTRY_FILE="SKILL.md"

# --- Функции ---

# Вывод сообщения об ошибке в stderr
print_error() {
  echo "$1" >&2
}

# Проверка наличия обязательной программы в системе
check_dependency() {
  local tool_name="$1"
  local install_hint="$2"

  if ! command -v "$tool_name" > /dev/null 2>&1; then
    print_error "ERROR: ${tool_name} не найден. ${install_hint}"
    exit 3
  fi
}

# Преобразование относительного пути в абсолютный
resolve_absolute_path() {
  local file_path="$1"
  local dir_part
  local base_part

  dir_part=$(cd "$(dirname "$file_path")" && pwd)
  base_part=$(basename "$file_path")
  echo "${dir_part}/${base_part}"
}

# Проверка, что папка скилла содержит нужные файлы
validate_skill_folder() {
  local skill_folder="$1"

  if [ ! -d "$skill_folder" ]; then
    print_error "ERROR: Папка скилла не найдена: ${skill_folder}"
    exit 1
  fi

  if [ ! -f "${skill_folder}/${SKILL_ENTRY_FILE}" ]; then
    print_error "ERROR: Файл ${SKILL_ENTRY_FILE} не найден в папке скилла: ${skill_folder}"
    exit 1
  fi

  if [ ! -f "${skill_folder}/${TESTER_AGENT_PATH}" ]; then
    print_error "ERROR: Файл инструкции тестировщика не найден: ${skill_folder}/${TESTER_AGENT_PATH}"
    exit 1
  fi
}

# Проверка, что путь результатов — не файл
validate_results_path() {
  local dir_path="$1"

  if [ -f "$dir_path" ]; then
    print_error "ERROR: Путь для результатов существует, но это файл, а не папка: ${dir_path}"
    exit 2
  fi
}

# Создание директории с обработкой ошибки
create_directory() {
  local dir_path="$1"
  local error_message="$2"

  if ! mkdir -p "$dir_path"; then
    print_error "ERROR: ${error_message}: ${dir_path}"
    exit 2
  fi
}

# Формирование промпта для чистой Claude-сессии
# Claude получает инструкцию из dry-run-tester.md и путь к папке скилла
build_prompt() {
  local tester_instruction="$1"
  local skill_folder="$2"

  local prompt_text
  prompt_text="Ниже — инструкция для тестирования скилла. Папка скилла: ${skill_folder}

Прочитай файл ${skill_folder}/${SKILL_ENTRY_FILE}, затем прочитай все файлы, на которые он ссылается.
После этого выполни анализ по инструкции ниже.

ВАЖНО: ты только ЧИТАЕШЬ и АНАЛИЗИРУЕШЬ файлы. Ты НЕ запускаешь пайплайн, НЕ выполняешь код, НЕ создаёшь файлы.
Это мысленный прогон — ты проходишь по шагам и ищешь проблемы.

Результат выдай СТРОГО в формате JSON, описанном в инструкции. Никакого текста до или после JSON.

--- ИНСТРУКЦИЯ ---
${tester_instruction}"

  echo "$prompt_text"
}

# Извлечение JSON из вывода Claude (на случай, если есть текст вокруг JSON)
extract_json_from_output() {
  local raw_output_file="$1"
  local output_file="$2"

  # Пробуем найти JSON-блок между фигурными скобками (первая { до последней })
  # sed берёт от первой строки с { до последней строки с }
  if sed -n '/^[[:space:]]*{/,/^[[:space:]]*}/p' "$raw_output_file" > "$output_file" 2>/dev/null; then
    # Проверяем, что результат — валидный JSON
    if jq empty "$output_file" 2>/dev/null; then
      return 0
    fi
  fi

  # Если не удалось извлечь валидный JSON — возвращаем ошибку
  return 1
}

# Формирование fallback-отчёта, когда Claude не вернул валидный JSON
build_fallback_report() {
  local results_dir="$1"
  local timestamp="$2"
  local skill_folder="$3"
  local claude_exit_code="$4"
  local raw_output_file="$5"

  local raw_output_basename
  raw_output_basename=$(basename "$raw_output_file")

  jq -n \
    --arg test_name "dry-run" \
    --arg timestamp "$timestamp" \
    --argjson test_number 1 \
    --arg skill_folder "$skill_folder" \
    --argjson questions_count 0 \
    --argjson contradictions_count 0 \
    --arg parse_error "Claude не вернул валидный JSON. Сырой вывод сохранён в ${raw_output_basename}" \
    --argjson claude_exit_code "$claude_exit_code" \
    --arg claude_raw_output_file "$raw_output_basename" \
    '{
      test_name: $test_name,
      timestamp: $timestamp,
      test_number: $test_number,
      skill_folder: $skill_folder,
      questions_count: $questions_count,
      contradictions_count: $contradictions_count,
      questions: [],
      contradictions: [],
      missing_references: [],
      ambiguities: [],
      artifacts_created: [],
      quality_score: "N/A",
      parse_error: $parse_error,
      claude_exit_code: $claude_exit_code,
      claude_raw_output_file: $claude_raw_output_file,
      summary: {
        status: "FAIL",
        blocking_issues: 1,
        minor_issues: 0
      }
    }' > "${results_dir}/${RESULT_FILENAME}"
}

# Вывод итоговой сводки в stdout
print_summary() {
  local result_file="$1"
  local results_dir="$2"

  local status
  local blocking_issues
  local minor_issues

  status=$(jq -r '.summary.status // "UNKNOWN"' "$result_file")
  blocking_issues=$(jq -r '.summary.blocking_issues // 0' "$result_file")
  minor_issues=$(jq -r '.summary.minor_issues // 0' "$result_file")

  echo "dry-run-test: status=${status}, blocking=${blocking_issues}, minor=${minor_issues}"
  echo "Отчёт сохранён: ${results_dir}/${RESULT_FILENAME}"
}

# --- Основной алгоритм ---

# Шаг 1. Проверка зависимостей
check_dependency "jq" "Установка: brew install jq"
check_dependency "claude" "Установите Claude Code CLI"

# Шаг 2. Разбор и валидация аргументов
if [ "$#" -lt 2 ]; then
  print_error "Usage: $0 <skill-folder-path> <results-folder-path>"
  print_error "  skill-folder-path   — путь к папке скилла (содержит SKILL.md)"
  print_error "  results-folder-path — путь к папке для сохранения результатов"
  exit 1
fi

skill_folder_input="$1"
results_folder_input="$2"

# Преобразуем путь к папке скилла в абсолютный
validate_skill_folder "$skill_folder_input"
skill_folder=$(resolve_absolute_path "$skill_folder_input")

# Проверяем путь к результатам
validate_results_path "$results_folder_input"

# Шаг 3. Подготовка директории результатов
create_directory "$results_folder_input" "Не удалось создать папку результатов"
results_dir=$(resolve_absolute_path "$results_folder_input")

# Шаг 4. Генерация timestamp (ISO-8601 формат)
timestamp=$(date '+%Y-%m-%dT%H:%M:%S')

# Человекочитаемый timestamp для имён файлов
file_timestamp=$(date '+%d-%m-%H-%M')

# Шаг 5. Создание временной директории
tmp_dir=$(mktemp -d) || {
  print_error "ERROR: Не удалось создать временную директорию"
  exit 2
}
# Автоматическая очистка временных файлов при завершении
trap 'rm -rf "$tmp_dir"' EXIT INT TERM

# Шаг 6. Чтение инструкции для тестирующего агента
tester_instruction_file="${skill_folder}/${TESTER_AGENT_PATH}"
tester_instruction=$(cat "$tester_instruction_file")

# Шаг 7. Формирование промпта для Claude
build_prompt "$tester_instruction" "$skill_folder" > "$tmp_dir/prompt.txt"

# Шаг 8. Запуск Claude в чистой сессии
# --print (-p) — режим «один вопрос — один ответ», без интерактива
# --output-format text — текстовый вывод (не Markdown)
# env -u CLAUDECODE — убираем переменные текущей сессии Claude, чтобы контекст был чистым
echo "Запуск Claude для dry-run тестирования (таймаут: ${TIMEOUT_SECONDS}с)..."

env -u CLAUDECODE claude --print \
  --output-format text \
  < "$tmp_dir/prompt.txt" \
  > "$tmp_dir/raw_output.txt" \
  2> "$tmp_dir/claude_stderr.txt" &
claude_pid=$!

# Фоновый watchdog для таймаута
(
  sleep "$TIMEOUT_SECONDS"
  kill "$claude_pid" 2>/dev/null
) &
watchdog_pid=$!

# Ожидание завершения Claude
# set +e нужен, потому что wait возвращает exit code дочернего процесса,
# и при set -e ненулевой код завершения Claude прервёт скрипт
set +e
wait "$claude_pid" 2>/dev/null
claude_exit_code=$?
set -e

# Убийство watchdog (Claude завершился раньше таймаута)
kill "$watchdog_pid" 2>/dev/null
wait "$watchdog_pid" 2>/dev/null || true

# Шаг 9. Проверка результата Claude
if [ "$claude_exit_code" -eq "$SIGTERM_EXIT_CODE" ]; then
  print_error "WARNING: Claude превысил таймаут (${TIMEOUT_SECONDS}с), обрабатываем частичный вывод"
elif [ "$claude_exit_code" -ne 0 ]; then
  print_error "WARNING: Claude завершился с кодом ${claude_exit_code}"
fi

# Если Claude не выдал никакого вывода — дальше парсить нечего
if [ "$claude_exit_code" -ne 0 ] && [ ! -s "$tmp_dir/raw_output.txt" ]; then
  print_error "ERROR: Claude завершился с кодом ${claude_exit_code} и не выдал вывод"
  # Сохраняем stderr Claude для диагностики
  if [ -s "$tmp_dir/claude_stderr.txt" ]; then
    cp "$tmp_dir/claude_stderr.txt" "${results_dir}/${file_timestamp}-dry-run-stderr.txt"
    print_error "Stderr сохранён: ${results_dir}/${file_timestamp}-dry-run-stderr.txt"
  fi
  exit 4
fi

# Шаг 10. Сохранение сырого вывода Claude для диагностики
raw_output_saved="${results_dir}/${file_timestamp}-dry-run-raw-output.txt"
cp "$tmp_dir/raw_output.txt" "$raw_output_saved"

# Шаг 11. Извлечение JSON из вывода Claude
if extract_json_from_output "$tmp_dir/raw_output.txt" "$tmp_dir/parsed_result.json"; then
  # Добавляем метаинформацию к результату Claude
  jq \
    --arg claude_raw_output_file "$(basename "$raw_output_saved")" \
    --argjson claude_exit_code "$claude_exit_code" \
    '. + {claude_exit_code: $claude_exit_code, claude_raw_output_file: $claude_raw_output_file}' \
    "$tmp_dir/parsed_result.json" > "${results_dir}/${RESULT_FILENAME}"
else
  # Claude не вернул валидный JSON — создаём fallback-отчёт
  print_error "WARNING: Claude не вернул валидный JSON, создаём fallback-отчёт"
  build_fallback_report "$results_dir" "$timestamp" "$skill_folder" \
    "$claude_exit_code" "$raw_output_saved"
fi

# Шаг 12. Создание копии с таймстампом (чтобы история тестов не перезатиралась)
cp "${results_dir}/${RESULT_FILENAME}" "${results_dir}/${file_timestamp}-dry-run.json"

# Шаг 13. Вывод итоговой сводки
print_summary "${results_dir}/${RESULT_FILENAME}" "$results_dir"
echo "Копия с таймстампом: ${results_dir}/${file_timestamp}-dry-run.json"
echo "Сырой вывод Claude: ${raw_output_saved}"

# Очистка временных файлов — выполняется автоматически через trap EXIT
