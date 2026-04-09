#!/usr/bin/env bash
set -euo pipefail

# Локаль для корректной работы grep с кириллицей
export LC_ALL=en_US.UTF-8

# --- Константы ---

# Таймаут ожидания Claude (10 минут)
TIMEOUT_SECONDS=600

# Exit code при убийстве процесса по SIGTERM (128 + 15)
SIGTERM_EXIT_CODE=143

# --- Функции ---

# Вывод сообщения об ошибке в stderr
print_error() {
  echo "$1" >&2
}

# Проверка наличия обязательной зависимости
check_dependency() {
  local tool_name="$1"
  local install_hint="$2"

  if ! command -v "$tool_name" > /dev/null 2>&1; then
    print_error "ERROR: ${tool_name} is required but not installed. ${install_hint}"
    exit 3
  fi
}

# Валидация файла спецификации (аргумент $1)
validate_spec_file() {
  local file_path="$1"

  if [ ! -f "$file_path" ]; then
    print_error "ERROR: File not found: ${file_path}"
    exit 1
  fi

  if [ "${file_path##*.}" != "md" ]; then
    print_error "ERROR: Expected .md file, got: ${file_path}"
    exit 1
  fi

  if [ ! -s "$file_path" ]; then
    print_error "ERROR: File is empty: ${file_path}"
    exit 1
  fi

  if [ ! -r "$file_path" ]; then
    print_error "ERROR: File is not readable: ${file_path}"
    exit 1
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

# Валидация директории результатов (аргумент $2)
validate_results_dir() {
  local dir_path="$1"

  if [ -f "$dir_path" ]; then
    print_error "ERROR: Results path exists but is not a directory: ${dir_path}"
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

# Формирование промпта для Claude
build_prompt() {
  local spec_content="$1"
  local prompt_instruction
  prompt_instruction='Перед тобой спецификация пайплайна. Твоя задача — попробовать реализовать его шаг за шагом, опираясь ТОЛЬКО на информацию из спецификации.

Правила:
1. Если тебе что-то непонятно или не хватает информации для реализации — напиши строку, начинающуюся с "ВОПРОС: " и далее суть вопроса. Каждый вопрос — на отдельной строке.
2. Если видишь противоречие между разными частями спецификации — напиши строку, начинающуюся с "ПРОТИВОРЕЧИЕ: " и далее суть противоречия. Каждое противоречие — на отдельной строке.
3. Если в процессе реализации ты создаёшь файл (даже мысленно) — напиши строку, начинающуюся с "АРТЕФАКТ: " и далее имя файла. Каждый артефакт — на отдельной строке.
4. В самом конце напиши строку "ИТОГ: X вопросов, Y противоречий", где X и Y — числа.

Не задавай мне вопросов — просто отмечай всё, что непонятно, через маркеры выше.

Вот спецификация:'

  printf '%s\n\n%s' "$prompt_instruction" "$spec_content"
}

# Извлечение строк по маркеру из вывода Claude
extract_by_marker() {
  local raw_output_file="$1"
  local marker="$2"
  local output_file="$3"

  grep -i "${marker}:" "$raw_output_file" \
    | sed "s/.*${marker}:[[:space:]]*//" \
    | sed '/^$/d' \
    > "$output_file" || true
}

# Преобразование текстового файла в JSONL (каждая строка -> {"text": "..."})
convert_to_jsonl() {
  local input_file="$1"
  local output_file="$2"

  while IFS= read -r line; do
    [ -z "$line" ] && continue
    jq -n --arg text "$line" '{"text": $text}'
  done < "$input_file" > "$output_file"
}

# Запись JSON-отчёта
write_json_report() {
  local results_dir="$1"
  local timestamp="$2"
  local spec_file="$3"
  local questions_count="$4"
  local contradictions_count="$5"
  local quality_score="$6"
  local claude_exit_code="$7"
  local tmp_dir="$8"

  local report_path="${results_dir}/dry-run-result.json"

  if ! jq -n \
    --arg test_name "dry-run" \
    --arg timestamp "$timestamp" \
    --argjson test_number 1 \
    --arg spec_file "$spec_file" \
    --argjson questions_count "$questions_count" \
    --argjson contradictions_count "$contradictions_count" \
    --slurpfile questions "$tmp_dir/questions.jsonl" \
    --slurpfile contradictions "$tmp_dir/contradictions.jsonl" \
    --slurpfile artifacts "$tmp_dir/artifacts.json" \
    --arg quality_score "questions + contradictions = $quality_score (чем ближе к 0, тем лучше)" \
    --argjson claude_exit_code "$claude_exit_code" \
    --arg claude_raw_output_file "${timestamp}-dry-run-raw-output.txt" \
    '{
      test_name: $test_name,
      timestamp: $timestamp,
      test_number: $test_number,
      spec_file: $spec_file,
      questions_count: $questions_count,
      contradictions_count: $contradictions_count,
      questions: $questions,
      contradictions: $contradictions,
      artifacts_created: $artifacts[0],
      quality_score: $quality_score,
      claude_exit_code: $claude_exit_code,
      claude_raw_output_file: $claude_raw_output_file
    }' > "$report_path"; then
    print_error "ERROR: Cannot write report file: ${report_path}"
    exit 2
  fi
}

# Вывод сводки в stdout
print_summary() {
  local questions_count="$1"
  local contradictions_count="$2"
  local quality_score="$3"
  local results_dir="$4"
  local timestamp="$5"

  echo "dry-run-test: ${questions_count} questions, ${contradictions_count} contradictions (quality_score=${quality_score})"
  echo "Report saved: ${results_dir}/dry-run-result.json"
  echo "Copy saved: ${results_dir}/${timestamp}-dry-run.json"
}

# --- Основной алгоритм ---

# Шаг 1. Проверка зависимостей
check_dependency "jq" "Install with: brew install jq"
check_dependency "claude" "Install Claude Code first"

# Шаг 2. Разбор и валидация аргументов
if [ "$#" -lt 2 ]; then
  print_error "Usage: $0 <spec-file.md> <results-dir>"
  exit 1
fi

validate_spec_file "$1"
spec_file=$(resolve_absolute_path "$1")

validate_results_dir "$2"
results_dir="$2"

# Шаг 3. Подготовка директории результатов
create_directory "$results_dir" "Cannot create results directory"
create_directory "$results_dir/artifacts" "Cannot create results directory"

# Шаг 4. Генерация timestamp
timestamp=$(date '+%d-%m-%H-%M')

# Шаг 5. Создание временной директории
tmp_dir=$(mktemp -d) || {
  print_error "ERROR: Cannot create temporary directory"
  exit 2
}
trap 'rm -rf "$tmp_dir"' EXIT INT TERM

# Шаг 6. Формирование промпта для Claude
spec_content=$(cat "$spec_file")
build_prompt "$spec_content" > "$tmp_dir/prompt.txt"

# Шаг 7. Запуск Claude с чистым контекстом
env -u CLAUDECODE claude -p \
  --output-format text \
  --no-session-persistence \
  --system-prompt "Ты — опытный разработчик. Тебе дана спецификация пайплайна. Реализуй его шаг за шагом, строго следуя инструкциям в промпте." \
  --effort max \
  --bare \
  --tools "" \
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
# и при set -e ненулевой exit code Claude прервёт скрипт преждевременно
set +e
wait "$claude_pid" 2>/dev/null
claude_exit_code=$?
set -e

# Убийство watchdog (Claude завершился раньше таймаута)
kill "$watchdog_pid" 2>/dev/null
wait "$watchdog_pid" 2>/dev/null || true

# Шаг 8. Проверка результата Claude
if [ "$claude_exit_code" -eq "$SIGTERM_EXIT_CODE" ]; then
  print_error "WARNING: Claude exceeded timeout (${TIMEOUT_SECONDS}s), processing partial output"
elif [ "$claude_exit_code" -ne 0 ]; then
  print_error "WARNING: Claude exited with code ${claude_exit_code}"
fi

# Если Claude упал и не выдал вывод — дальше парсить нечего
if [ "$claude_exit_code" -ne 0 ] && [ ! -s "$tmp_dir/raw_output.txt" ]; then
  print_error "ERROR: Claude failed with exit code ${claude_exit_code} and produced no output"
  exit 4
fi

# Шаг 9. Сохранение сырого вывода Claude
cp "$tmp_dir/raw_output.txt" "$results_dir/${timestamp}-dry-run-raw-output.txt"

# Шаг 10. Парсинг вывода Claude
extract_by_marker "$tmp_dir/raw_output.txt" "ВОПРОС" "$tmp_dir/questions.txt"
extract_by_marker "$tmp_dir/raw_output.txt" "ПРОТИВОРЕЧИЕ" "$tmp_dir/contradictions.txt"
extract_by_marker "$tmp_dir/raw_output.txt" "АРТЕФАКТ" "$tmp_dir/artifacts.txt"

# Шаг 11. Подсчёт метрик
questions_count=$(wc -l < "$tmp_dir/questions.txt" | tr -d ' ')
contradictions_count=$(wc -l < "$tmp_dir/contradictions.txt" | tr -d ' ')
quality_score=$((questions_count + contradictions_count))

# Шаг 12. Формирование JSON-отчёта
# Подготовка данных для jq: текст -> JSONL
convert_to_jsonl "$tmp_dir/questions.txt" "$tmp_dir/questions.jsonl"
convert_to_jsonl "$tmp_dir/contradictions.txt" "$tmp_dir/contradictions.jsonl"

# Артефакты -> JSON-массив строк
jq -R -s 'split("\n") | map(select(length > 0))' < "$tmp_dir/artifacts.txt" > "$tmp_dir/artifacts.json"

write_json_report "$results_dir" "$timestamp" "$spec_file" \
  "$questions_count" "$contradictions_count" "$quality_score" \
  "$claude_exit_code" "$tmp_dir"

# Шаг 13. Создание копии с таймстампом
cp "$results_dir/dry-run-result.json" "$results_dir/${timestamp}-dry-run.json"

# Шаг 14. Вывод сводки в stdout
print_summary "$questions_count" "$contradictions_count" \
  "$quality_score" "$results_dir" "$timestamp"

# Шаг 15. Очистка временных файлов — выполняется автоматически через trap EXIT
