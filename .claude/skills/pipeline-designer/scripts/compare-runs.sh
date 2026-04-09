#!/bin/bash
set -euo pipefail

# Гарантирует корректную работу строковых операций на любой системе
export LC_ALL=en_US.UTF-8

# --- Константы ---

# Фиксированное имя теста для отчёта
EVAL_TEST_NAME="eval-consistency"

# Фиксированный номер теста (в рамках фазы 7.4 — один тест сравнения)
EVAL_TEST_NUMBER=1

# Порог стабильности по умолчанию (80% метрик должны совпадать)
DEFAULT_THRESHOLD="0.80"

# Поле, которое исключается из сравнения (порядковый номер прогона)
EXCLUDED_FIELD="run"

# --- Проверка зависимостей ---

check_jq_installed() {
  if ! command -v jq > /dev/null 2>&1; then
    echo "ERROR: jq is required but not installed. Install with: brew install jq" >&2
    exit 3
  fi
}

# --- Разбор аргументов ---

parse_arguments() {
  local threshold_value="$DEFAULT_THRESHOLD"
  local -a file_paths=()

  while [ $# -gt 0 ]; do
    case "$1" in
      --threshold)
        # Проверяем, что после --threshold есть значение
        if [ $# -lt 2 ]; then
          echo "ERROR: --threshold requires a numeric value" >&2
          exit 2
        fi
        threshold_value="$2"
        shift 2
        ;;
      --*)
        echo "ERROR: Unknown option: $1" >&2
        exit 2
        ;;
      *)
        file_paths+=("$1")
        shift
        ;;
    esac
  done

  # Минимум 2 файла обязательны
  if [ "${#file_paths[@]}" -lt 2 ]; then
    echo "Usage: $0 [--threshold N] <metrics1.json> <metrics2.json> [metrics3.json ...]" >&2
    exit 2
  fi

  # Валидация threshold через jq
  if ! echo "$threshold_value" | jq -e 'type == "number" and . >= 0 and . <= 1' > /dev/null 2>&1; then
    echo "ERROR: Invalid threshold value: ${threshold_value}. Must be a number between 0 and 1" >&2
    exit 2
  fi

  # Возвращаем результат через глобальные переменные
  THRESHOLD="$threshold_value"
  FILES=("${file_paths[@]}")
}

# --- Валидация входных файлов ---

validate_input_files() {
  for file in "${FILES[@]}"; do
    if [ ! -f "$file" ]; then
      echo "ERROR: File not found: ${file}" >&2
      exit 2
    fi

    if [ "${file##*.}" != "json" ]; then
      echo "ERROR: Expected .json file, got: ${file}" >&2
      exit 2
    fi

    if [ ! -s "$file" ]; then
      echo "ERROR: File is empty: ${file}" >&2
      exit 2
    fi

    if [ ! -r "$file" ]; then
      echo "ERROR: File is not readable: ${file}" >&2
      exit 2
    fi

    if ! jq . < "$file" > /dev/null 2>&1; then
      echo "ERROR: Invalid JSON in file: ${file}" >&2
      exit 2
    fi
  done
}

# --- Создание временной директории ---

create_temp_directory() {
  tmp_dir=$(mktemp -d) || {
    echo "ERROR: Cannot create temporary directory" >&2
    exit 2
  }
  trap 'rm -rf "$tmp_dir"' EXIT INT TERM
}

# --- Извлечение списка метрик из первого файла ---

extract_metric_names() {
  local first_file="$1"
  jq -r "keys[] | select(. != \"${EXCLUDED_FIELD}\")" < "$first_file"
}

# --- Определение типа метрики по первому файлу ---

get_metric_type() {
  local metric_name="$1"
  local first_file="$2"
  jq -r --arg key "$metric_name" '.[$key] | type' < "$first_file"
}

# --- Извлечение значений метрики из всех файлов в JSONL ---

collect_metric_values() {
  local metric_name="$1"
  local values_file="$tmp_dir/values-${metric_name}.jsonl"

  for file in "${FILES[@]}"; do
    jq -c --arg key "$metric_name" '.[$key]' < "$file" >> "$values_file"
  done
}

# --- Сбор отсортированных значений для массивов в JSONL ---

collect_sorted_array_values() {
  local metric_name="$1"
  local sorted_file="$tmp_dir/sorted-values-${metric_name}.jsonl"

  for file in "${FILES[@]}"; do
    jq -c --arg key "$metric_name" '.[$key] | sort' < "$file" >> "$sorted_file"
  done
}

# --- Определение статуса стабильности ---

determine_stability_status() {
  local values_file="$1"
  local runs_count="$2"

  # Подсчёт: сколько раз встречается самое частое значение
  local max_count
  max_count=$(sort "$values_file" | uniq -c | sort -rn | head -1 | awk '{print $1}')

  if [ "$max_count" -eq "$runs_count" ]; then
    echo "stable"
  elif [ "$((max_count * 2))" -gt "$runs_count" ]; then
    # max_count > runs_count / 2 (строго больше половины)
    echo "almost_stable"
  else
    echo "unstable"
  fi
}

# --- Формирование JSON-строки результата для числовой метрики ---

build_metric_result_number() {
  local metric_name="$1"
  local values_file="$tmp_dir/values-${metric_name}.jsonl"
  local status="$2"

  # Собираем массив values из JSONL
  local values_json
  values_json=$(jq -s '.' < "$values_file")

  # Вычисляем spread (разница между максимальным и минимальным)
  local spread
  spread=$(jq -s 'max - min' < "$values_file")

  # Формируем JSON-объект для этой метрики
  jq -n \
    --arg key "$metric_name" \
    --argjson values "$values_json" \
    --arg status "$status" \
    --argjson spread "$spread" \
    '{"key": $key, "value": {"values": $values, "status": $status, "spread": $spread}}'
}

# --- Формирование JSON-строки результата для метрики без spread ---

build_metric_result_no_spread() {
  local metric_name="$1"
  local values_file="$2"
  local status="$3"

  # Собираем массив values из JSONL
  local values_json
  values_json=$(jq -s '.' < "$values_file")

  # Формируем JSON-объект без spread
  jq -n \
    --arg key "$metric_name" \
    --argjson values "$values_json" \
    --arg status "$status" \
    '{"key": $key, "value": {"values": $values, "status": $status}}'
}

# --- Обработка одной метрики ---

process_metric() {
  local metric_name="$1"
  local first_file="$2"
  local runs_count="$3"

  local metric_type
  metric_type=$(get_metric_type "$metric_name" "$first_file")

  # Собираем значения из всех файлов
  collect_metric_values "$metric_name"

  local values_file="$tmp_dir/values-${metric_name}.jsonl"
  local status

  if [ "$metric_type" = "array" ]; then
    # Для массивов: сортируем элементы, сравниваем отсортированные версии
    collect_sorted_array_values "$metric_name"
    local sorted_file="$tmp_dir/sorted-values-${metric_name}.jsonl"
    status=$(determine_stability_status "$sorted_file" "$runs_count")
    # В values записываем отсортированные версии массивов
    build_metric_result_no_spread "$metric_name" "$sorted_file" "$status"
  elif [ "$metric_type" = "number" ]; then
    status=$(determine_stability_status "$values_file" "$runs_count")
    build_metric_result_number "$metric_name" "$status"
  else
    # boolean и string — без spread
    status=$(determine_stability_status "$values_file" "$runs_count")
    build_metric_result_no_spread "$metric_name" "$values_file" "$status"
  fi
}

# --- Формирование итогового JSON-отчёта ---

build_final_report() {
  local timestamp="$1"
  local runs_count="$2"
  local metrics_file="$tmp_dir/metrics.jsonl"

  # Подсчёт метрик по статусам
  local stable_count
  stable_count=$(jq -r '.value.status' < "$metrics_file" | grep -c '^stable$' || true)

  local almost_stable_count
  almost_stable_count=$(jq -r '.value.status' < "$metrics_file" | grep -c '^almost_stable$' || true)

  local unstable_count
  unstable_count=$(jq -r '.value.status' < "$metrics_file" | grep -c '^unstable$' || true)

  local total_metrics
  total_metrics=$((stable_count + almost_stable_count + unstable_count))

  # Вычисление stability_rate с округлением до 2 знаков
  local stability_rate
  stability_rate=$(jq -n --argjson stable "$stable_count" --argjson total "$total_metrics" \
    '($stable / $total * 100 | round) / 100')

  # Определение статуса PASS/FAIL
  local final_status
  final_status=$(jq -n --argjson rate "$stability_rate" --argjson threshold "$THRESHOLD" \
    'if $rate >= $threshold then "PASS" else "FAIL" end' -r)

  # Сборка объекта metrics из JSONL
  local metrics_object
  metrics_object=$(jq -s 'reduce .[] as $item ({}; .[$item.key] = $item.value)' < "$metrics_file")

  # Формирование итогового JSON
  jq -n \
    --arg test_name "$EVAL_TEST_NAME" \
    --arg timestamp "$timestamp" \
    --argjson test_number "$EVAL_TEST_NUMBER" \
    --argjson runs_count "$runs_count" \
    --argjson metrics "$metrics_object" \
    --argjson total_metrics "$total_metrics" \
    --argjson stable "$stable_count" \
    --argjson almost_stable "$almost_stable_count" \
    --argjson unstable "$unstable_count" \
    --argjson stability_rate "$stability_rate" \
    --argjson threshold "$THRESHOLD" \
    --arg status "$final_status" \
    '{
      "test_name": $test_name,
      "timestamp": $timestamp,
      "test_number": $test_number,
      "runs_count": $runs_count,
      "metrics": $metrics,
      "summary": {
        "total_metrics": $total_metrics,
        "stable": $stable,
        "almost_stable": $almost_stable,
        "unstable": $unstable,
        "stability_rate": $stability_rate,
        "threshold": $threshold,
        "status": $status
      }
    }'
}

# === Главная логика ===

main() {
  check_jq_installed
  parse_arguments "$@"
  validate_input_files
  create_temp_directory

  local timestamp
  timestamp=$(date '+%d-%m-%H-%M')

  local runs_count="${#FILES[@]}"
  local first_file="${FILES[0]}"
  local metrics_file="$tmp_dir/metrics.jsonl"

  # Извлекаем список метрик из первого файла
  local metric_names
  metric_names=$(extract_metric_names "$first_file")

  # Обрабатываем каждую метрику
  while IFS= read -r metric_name; do
    process_metric "$metric_name" "$first_file" "$runs_count" >> "$metrics_file"
  done <<< "$metric_names"

  # Формируем и выводим итоговый отчёт
  local report
  report=$(build_final_report "$timestamp" "$runs_count")
  echo "$report" | jq .

  # Определяем exit code по статусу
  local final_status
  final_status=$(echo "$report" | jq -r '.summary.status')
  if [ "$final_status" = "PASS" ]; then
    exit 0
  else
    exit 1
  fi
}

main "$@"
