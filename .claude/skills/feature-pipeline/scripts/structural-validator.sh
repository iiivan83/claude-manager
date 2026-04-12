#!/bin/bash
set -euo pipefail

# =============================================================================
# structural-validator.sh для feature-pipeline
#
# Пилотная версия валидатора для скилла feature-pipeline. В первой версии
# содержит ровно одну проверку — дисциплину оркестратора (check_orchestrator_discipline).
# Остальные проверки (preflight, line-buffered, durable iteration) добавляются
# отдельно, когда feature-pipeline до них дорастёт.
#
# Эталон функции — pipeline-implementer/scripts/structural-validator.sh (строка 947).
# Здесь функция скопирована дословно, чтобы обе реализации ловили нарушения
# одинаково и их не пришлось потом синхронизировать построчно.
#
# Использование:
#   ORCHESTRATOR_LOG_PATH=<путь к orchestrator-log.json> \
#     ./structural-validator.sh <путь к папке скилла>
#
# Первый аргумент — корень скилла (feature-pipeline/). Переменная окружения
# ORCHESTRATOR_LOG_PATH — путь к orchestrator-log.json последнего тестового
# прогона. Если переменная не задана или файл не существует, проверка
# пропускается с вердиктом NO_LOG и валидатор записывает PASS с пометкой.
# =============================================================================

# --- Аргументы ---
SKILL_PATH="${1:?Ошибка: укажи путь к папке скилла как первый аргумент}"

# --- Временная метка ---
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# --- Счётчики ---
CRITICAL_PASSED=0
CRITICAL_FAILED=0
WARNINGS=0

# --- Массивы результатов ---
CRITICAL_CHECKS="[]"
NON_CRITICAL_CHECKS="[]"

# Добавить результат критической проверки (любой FAIL = общий FAIL).
add_critical_check() {
  local check_name="$1"
  local status="$2"
  local details="$3"

  CRITICAL_CHECKS=$(echo "$CRITICAL_CHECKS" | python3 -c "
import sys, json
checks = json.load(sys.stdin)
checks.append({'check': '$check_name', 'status': '$status', 'details': '''$details'''})
print(json.dumps(checks))
")

  if [ "$status" = "PASS" ]; then
    CRITICAL_PASSED=$((CRITICAL_PASSED + 1))
  else
    CRITICAL_FAILED=$((CRITICAL_FAILED + 1))
  fi
}

# Добавить результат некритической проверки (FAIL = предупреждение, не роняет прогон).
add_non_critical_check() {
  local check_name="$1"
  local status="$2"
  local details="$3"

  NON_CRITICAL_CHECKS=$(echo "$NON_CRITICAL_CHECKS" | python3 -c "
import sys, json
checks = json.load(sys.stdin)
checks.append({'check': '$check_name', 'status': '$status', 'details': '''$details'''})
print(json.dumps(checks))
")

  if [ "$status" = "FAIL" ]; then
    WARNINGS=$((WARNINGS + 1))
  fi
}

# =============================================================================
# КРИТИЧЕСКАЯ ПРОВЕРКА: SKILL.md существует
# =============================================================================
# Минимальная проверка корня скилла — чтобы валидатор не отчитался PASS
# на абсолютно пустой папке и не дал ложного успокоения.
if [ -f "${SKILL_PATH}/SKILL.md" ]; then
  add_critical_check "skill_md_exists" "PASS" "SKILL.md найден"
else
  add_critical_check "skill_md_exists" "FAIL" "SKILL.md не найден в ${SKILL_PATH}"
fi

# =============================================================================
# ПРОВЕРКА ДИСЦИПЛИНЫ ОРКЕСТРАТОРА
# =============================================================================
# Ищет три признака нарушения дисциплины оркестратора по файлу orchestrator-log.json
# тестового прогона скилла. Проверяет:
#   1) самовыполнение после fail — когда тот же агент в той же фазе отчитывается
#      complete сразу после fail, без rollback или нового start между ними;
#   2) запрещённые формулировки в полях details и comment («делаю сам»,
#      «продолжаю без делегирования», «в обход CLI» и т.п.);
#   3) фазы со статусом completed в pipeline-state.json, рядом с которыми в
#      agent-outputs/ нет соответствующего JSON-файла или он не содержит
#      status=success.
#
# Если переменная не задана или файл по указанному пути не существует —
# проверка пропускается с вердиктом NO_LOG (и валидатор записывает PASS
# с соответствующей пометкой). Это сделано по тому же принципу, что и
# остальные опциональные проверки: валидатор не должен падать на скиллах,
# у которых просто нет тестового прогона.
#
# TODO(через неделю после 2026-04-10): перевести из warning в critical.
check_orchestrator_discipline() {
  local orchestrator_log_path="$1"
  python3 - "$orchestrator_log_path" <<'PY'
import json
import os
import re
import sys
from glob import glob

orchestrator_log_path = sys.argv[1]

# Запрещённые формулировки в полях details и comment. Регистронезависимый
# поиск — поэтому все строки в нижнем регистре. Список вынесен в именованную
# константу, чтобы править его в одном месте, а не искать по коду.
FORBIDDEN_SUBSTRINGS = [
    "делаю сам",              # "делаю сам"
    "продолжаю без делегирования",  # "продолжаю без делегирования"
    "работаю напрямую",                          # "работаю напрямую"
    "читаю файлы сам",                                      # "читаю файлы сам"
    "делать сам",                                                               # "делать сам"
    "работаю сам",                                                         # "работаю сам"
    "не делегирую",                                                   # "не делегирую"
    "в обход cli",                                                                             # "в обход CLI"
]

# Если файла лога нет — это не ошибка, а отсутствие артефакта.
# Записываем NO_LOG и выходим без кода ошибки: вызывающий код
# запишет это как PASS с пометкой «нет тестового прогона».
if not orchestrator_log_path or not os.path.isfile(orchestrator_log_path):
    print("NO_LOG")
    sys.exit(0)

try:
    with open(orchestrator_log_path, "r", encoding="utf-8") as log_file:
        log_data = json.load(log_file)
except (OSError, json.JSONDecodeError) as load_error:
    # Битый лог — печатаем явную причину и падаем.
    print(f"FAIL | parse_error | {load_error}")
    sys.exit(1)

# В проекте orchestrator-log.json бывает двух форм:
#   (а) плоский список шагов: {"steps": [...]}
#   (б) обёртка с метаданными: {"pipeline": "...", "steps": [...]}
# Нас интересует именно массив steps — берём его, если он есть.
steps_list = log_data.get("steps") if isinstance(log_data, dict) else None
if not isinstance(steps_list, list):
    # Лог есть, но формат неожиданный — пропускаем проверку, чтобы не
    # падать на старых логах без поля steps. Это осознанная уступка
    # обратной совместимости: старые скиллы писали лог другим образом.
    print("NO_STEPS")
    sys.exit(0)

# -----------------------------------------------------------------------------
# Признак 1: самовыполнение после fail без rollback или нового start
# -----------------------------------------------------------------------------
# Идём по шагам по порядку. Для каждой пары (agent, phase) отслеживаем
# «последнее состояние» — был ли там недавно fail, и что произошло после.
# Как только после fail приходит complete от того же агента в той же фазе,
# и между ними не было rollback или нового start — это нарушение.
def find_self_completion_after_fail(steps):
    # Ключ = (agent, phase). Значение — список действий в хронологии,
    # ограничиваемся последним окном с момента fail до следующего rollback/start.
    open_fails = {}
    for step_index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        agent = step.get("agent")
        phase = step.get("phase")
        if action is None or agent is None or phase is None:
            continue
        key = (agent, phase)
        if action == "fail":
            # Открываем окно fail — запоминаем индекс и номер шага.
            open_fails[key] = {
                "fail_step_index": step_index,
                "fail_step_number": step.get("step_number"),
            }
            continue
        if key not in open_fails:
            continue
        # Окно открыто — проверяем, что произошло дальше.
        if action in ("rollback", "start"):
            # Всё по правилам: либо откат, либо перезапуск. Закрываем окно.
            open_fails.pop(key, None)
            continue
        if action == "complete":
            # Нарушение: complete после fail без rollback или нового start.
            return {
                "fail_step_number": open_fails[key]["fail_step_number"],
                "complete_step_number": step.get("step_number"),
                "agent": agent,
                "phase": phase,
            }
    return None

violation_self_complete = find_self_completion_after_fail(steps_list)
if violation_self_complete is not None:
    print(
        "FAIL | signal_1_self_completion_after_fail | "
        f"agent={violation_self_complete['agent']} "
        f"phase={violation_self_complete['phase']} "
        f"fail_step={violation_self_complete['fail_step_number']} "
        f"complete_step={violation_self_complete['complete_step_number']}"
    )
    sys.exit(1)

# -----------------------------------------------------------------------------
# Признак 2: запрещённые подстроки в details или comment
# -----------------------------------------------------------------------------
# Регистронезависимый поиск — приводим и подстроки, и текст шага к lower().
def find_forbidden_substring_in_steps(steps):
    for step in steps:
        if not isinstance(step, dict):
            continue
        details_value = step.get("details", "") or ""
        comment_value = step.get("comment", "") or ""
        # Собираем текст шага в один буфер через конкатенацию — это совместимо
        # с любой версией Python. Многострочные f-строки с реальным переводом
        # строки допустимы только начиная с Python 3.12 и ломают анализ.
        combined_text_lower = (str(details_value) + " " + str(comment_value)).lower()
        for forbidden in FORBIDDEN_SUBSTRINGS:
            if forbidden in combined_text_lower:
                return {
                    "step_number": step.get("step_number"),
                    "agent": step.get("agent"),
                    "phase": step.get("phase"),
                    "matched_substring": forbidden,
                }
    return None

violation_forbidden_text = find_forbidden_substring_in_steps(steps_list)
if violation_forbidden_text is not None:
    print(
        "FAIL | signal_2_forbidden_substring | "
        f"step={violation_forbidden_text['step_number']} "
        f"agent={violation_forbidden_text['agent']} "
        f"phase={violation_forbidden_text['phase']} "
        f"substring={violation_forbidden_text['matched_substring']!r}"
    )
    sys.exit(1)

# -----------------------------------------------------------------------------
# Признак 3: фаза completed в pipeline-state.json без валидного agent-output
# -----------------------------------------------------------------------------
# Если рядом с логом лежит pipeline-state.json — значит, скилл использует
# продвинутую схему состояния фаз, и мы её проверяем. Если файла нет —
# молча пропускаем эту ветку, чтобы не ломать старые скиллы.
log_directory = os.path.dirname(orchestrator_log_path)
pipeline_state_path = os.path.join(log_directory, "pipeline-state.json")

if os.path.isfile(pipeline_state_path):
    try:
        with open(pipeline_state_path, "r", encoding="utf-8") as state_file:
            pipeline_state = json.load(state_file)
    except (OSError, json.JSONDecodeError) as state_error:
        print(f"FAIL | signal_3_pipeline_state_parse_error | {state_error}")
        sys.exit(1)

    # В pipeline-state.json ожидаем структуру {"phases": {"phase_name": {"status": "...", ...}, ...}}
    # или {"phases": [{"name": "...", "status": "..."}, ...]} — поддерживаем оба.
    phases_raw = pipeline_state.get("phases") if isinstance(pipeline_state, dict) else None

    def iter_phases(raw):
        if isinstance(raw, dict):
            for phase_name, phase_body in raw.items():
                if isinstance(phase_body, dict):
                    yield phase_name, phase_body
        elif isinstance(raw, list):
            for phase_body in raw:
                if isinstance(phase_body, dict):
                    phase_name = phase_body.get("name") or phase_body.get("phase") or ""
                    yield phase_name, phase_body

    agent_outputs_directory = os.path.join(log_directory, "agent-outputs")

    def find_agent_output_file(phase_name, phase_body):
        """Ищет agent-outputs/NN-*.json по номеру фазы или по имени агента."""
        if not os.path.isdir(agent_outputs_directory):
            return None
        phase_number = phase_body.get("number") or phase_body.get("step_number")
        candidates = []
        if phase_number is not None:
            # Номер фазы — шаблон "NN-*.json".
            candidates.extend(glob(os.path.join(agent_outputs_directory, f"{int(phase_number):02d}-*.json")))
        # Запасной путь — по имени фазы (оно часто совпадает с именем агента).
        if phase_name:
            safe_name = re.escape(phase_name)
            for candidate in glob(os.path.join(agent_outputs_directory, "*.json")):
                if re.search(safe_name, os.path.basename(candidate), re.IGNORECASE):
                    candidates.append(candidate)
        return candidates[0] if candidates else None

    for phase_name, phase_body in iter_phases(phases_raw):
        if phase_body.get("status") != "completed":
            continue
        output_file = find_agent_output_file(phase_name, phase_body)
        if output_file is None:
            print(
                "FAIL | signal_3_completed_without_agent_output | "
                f"phase={phase_name} reason=agent_output_file_not_found"
            )
            sys.exit(1)
        try:
            with open(output_file, "r", encoding="utf-8") as output_fh:
                agent_output = json.load(output_fh)
        except (OSError, json.JSONDecodeError) as output_error:
            print(
                "FAIL | signal_3_completed_without_agent_output | "
                f"phase={phase_name} reason=parse_error details={output_error}"
            )
            sys.exit(1)
        if not isinstance(agent_output, dict) or agent_output.get("status") != "success":
            actual_status = agent_output.get("status") if isinstance(agent_output, dict) else "<not a dict>"
            print(
                "FAIL | signal_3_completed_without_agent_output | "
                f"phase={phase_name} reason=status_not_success actual={actual_status}"
            )
            sys.exit(1)

print("OK: orchestrator discipline check passed")
PY
}

# Путь к orchestrator-log.json последнего тестового прогона передаётся через
# переменную окружения ORCHESTRATOR_LOG_PATH. Если не задано — пропускаем
# проверку, чтобы не ломать валидатор на скиллах без тестового прогона.
if [ -n "${ORCHESTRATOR_LOG_PATH:-}" ]; then
  discipline_verdict=$(check_orchestrator_discipline "$ORCHESTRATOR_LOG_PATH" 2>&1 || true)
  # Первое слово вердикта — статус (OK, FAIL, NO_LOG, NO_STEPS).
  discipline_first_token=$(echo "$discipline_verdict" | awk '{print $1}')
  case "$discipline_first_token" in
    OK:)
      add_critical_check "orchestrator_discipline" "PASS" "orchestrator_discipline: лог прогона ${ORCHESTRATOR_LOG_PATH} прошёл все три проверки (самовыполнение после fail, запрещённые подстроки, фазы completed без agent-output)"
      ;;
    NO_LOG|NO_STEPS)
      add_critical_check "orchestrator_discipline" "PASS" "orchestrator_discipline: проверка пропущена — ${discipline_verdict}. Это не ошибка: у скилла нет тестового прогона или лог в старом формате без поля steps."
      ;;
    FAIL)
      add_critical_check "orchestrator_discipline" "FAIL" "orchestrator_discipline: нарушение дисциплины оркестратора в логе ${ORCHESTRATOR_LOG_PATH}. Вердикт: ${discipline_verdict}. См. принцип enforcement-first и спецификацию 10.04_20.35-orchestrator-discipline-standard.md (рекомендация 5)."
      ;;
    *)
      add_critical_check "orchestrator_discipline" "FAIL" "orchestrator_discipline: неожиданный вердикт проверки — ${discipline_verdict}. Ожидались OK / NO_LOG / NO_STEPS / FAIL."
      ;;
  esac
else
  add_critical_check "orchestrator_discipline" "PASS" "orchestrator_discipline: переменная ORCHESTRATOR_LOG_PATH не задана — проверка пропущена. Чтобы включить, передай путь к orchestrator-log.json последнего прогона в этой переменной окружения."
fi


# =============================================================================
# ПРОВЕРКА ТИХИХ МАРКЕРОВ FALLBACK (warning)
# =============================================================================
# Ищет в файлах скилла (.md, .py, .sh) паттерны, которые сигнализируют, что
# разработчик или агент молча пропустил часть реализации. Три класса маркеров:
#
#   (а) # noqa: F401 на импортах — импорт, помеченный как «неиспользуемый, не
#       ругайтесь». Это признак, что реальная работа с библиотекой осталась
#       на бумаге: её импортировали «на будущее» или как заглушку.
#
#   (б) Запрещённые фразы в комментариях и строках — полный список в массиве
#       FORBIDDEN_FALLBACK_PATTERNS. Типичные примеры: «runtime-верификация»,
#       «полная реализация отложена», «достаточно проверить», «not_implemented»,
#       «TODO: implement later», «fallback на пустые», «SKIP с reason=»,
#       «simplified check».
#
#   (в) Неиспользуемые параметры функций — упрощённая grep-версия: параметр
#       встречается только в сигнатуре def f(param) и ни разу в теле функции.
#       Это даёт ложные срабатывания на легитимных случаях (унаследованные
#       сигнатуры, параметры под будущее использование). TODO: перевести на
#       AST-анализ через vulture или pyflakes при следующей итерации — там
#       без ложных срабатываний.
#
# White-list: если в той же строке или на строке выше есть комментарий
# «# structural-validator: allow-fallback-marker — объяснение», маркер
# пропускается. Используется, когда паттерн реально оправдан (например,
# в тесте, который специально проверяет запрещённые паттерны).
#
# Защита от самотриггеринга: файл structural-validator.sh исключается из
# сканирования целиком — иначе он находил бы собственный массив запрещённых
# паттернов и падал сам на себе. Это простое и очевидное решение, без
# контекстной эвристики с проверкой кавычек.
#
# Принцип тот же, что у durable iteration и preflight: предыдущие проверки
# ловят ошибки структуры файлов, а эта ловит ошибки намерения — что автор
# кода не оставил молчащих «TODO потом разберусь» в критичных местах.
# См. root-cause отчёт dev/docs/root-cause-reports/10-04_21-21_update-skill-plan-permits-silent-fallback.md
# (рекомендация R5) и принцип enforcement-first в CLAUDE.md.
#
# TODO(через неделю после 2026-04-10): перевести из warning в critical.
check_no_silent_fallback_markers() {
  local skill_dir="$1"

  # Список запрещённых паттернов. Все строки — литеральные (не regex), поиск
  # идёт через grep -F. Править список — в одном месте.
  local forbidden_fallback_patterns=(
    "# noqa: F401"
    "runtime-верификация"
    "runtime-verification"
    "полная реализация отложена"
    "достаточно проверить"
    "not_implemented"
    "TODO: implement later"
    "fallback на пустые"
    "SKIP с reason="
    "not implemented"
    "simplified check"
  )

  # Собираем список файлов для сканирования: .md, .py, .sh внутри папки скилла,
  # исключая сам structural-validator.sh (защита от самотриггеринга на массиве
  # запрещённых паттернов) и папку .git (если вдруг есть).
  local files_to_scan
  files_to_scan=$(find "$skill_dir" -type f \( -name "*.md" -o -name "*.py" -o -name "*.sh" \) \
    ! -name "structural-validator.sh" \
    ! -path "*/.git/*" 2>/dev/null)

  if [ -z "$files_to_scan" ]; then
    add_non_critical_check "no_silent_fallback_markers" "PASS" \
      "no_silent_fallback_markers: в скилле нет .md/.py/.sh файлов для сканирования (кроме самого валидатора)"
    return
  fi

  # Аккумулятор нарушений — каждое нарушение добавляется отдельной строкой.
  local violations_found=""
  local violations_count=0

  for fallback_pattern in "${forbidden_fallback_patterns[@]}"; do
    # grep -F — литеральный поиск (не regex), -n — номера строк, -H — имя файла.
    # Игнорируем код выхода 1 (grep возвращает его, если ничего не нашёл).
    local grep_output
    grep_output=$(echo "$files_to_scan" | xargs grep -FnH -- "$fallback_pattern" 2>/dev/null || true)
    [ -z "$grep_output" ] && continue

    # Идём по каждой найденной строке и проверяем white-list.
    while IFS= read -r grep_match_line; do
      [ -z "$grep_match_line" ] && continue

      # Формат строки от grep -FnH: <путь>:<номер>:<текст>
      local matched_file
      local matched_line_number
      matched_file=$(echo "$grep_match_line" | cut -d: -f1)
      matched_line_number=$(echo "$grep_match_line" | cut -d: -f2)

      # Проверка white-list: маркер в той же строке?
      if echo "$grep_match_line" | grep -Fq "structural-validator: allow-fallback-marker"; then
        continue
      fi

      # Проверка white-list: маркер на предыдущей строке?
      if [ -n "$matched_line_number" ] && [ "$matched_line_number" -gt 1 ] 2>/dev/null; then
        local previous_line_number=$((matched_line_number - 1))
        local previous_line_text
        previous_line_text=$(sed -n "${previous_line_number}p" "$matched_file" 2>/dev/null || true)
        if echo "$previous_line_text" | grep -Fq "structural-validator: allow-fallback-marker"; then
          continue
        fi
      fi

      # Нарушение подтверждено — добавляем в аккумулятор.
      violations_count=$((violations_count + 1))
      violations_found="${violations_found}
  - pattern='${fallback_pattern}' at ${matched_file}:${matched_line_number}"
    done <<< "$grep_output"
  done

  if [ "$violations_count" -eq 0 ]; then
    add_non_critical_check "no_silent_fallback_markers" "PASS" \
      "no_silent_fallback_markers: в файлах скилла не найдено тихих маркеров fallback (${#forbidden_fallback_patterns[@]} паттернов проверено)"
  else
    add_non_critical_check "no_silent_fallback_markers" "FAIL" \
      "no_silent_fallback_markers: найдено ${violations_count} тихих маркеров fallback в файлах скилла. Эти паттерны сигнализируют, что разработчик или агент молча пропустил часть реализации. Если маркер действительно оправдан — добавь в той же или предыдущей строке комментарий '# structural-validator: allow-fallback-marker — объяснение'. Список нарушений:${violations_found}. См. root-cause отчёт dev/docs/root-cause-reports/10-04_21-21_update-skill-plan-permits-silent-fallback.md (R5) и принцип enforcement-first в CLAUDE.md."
  fi
}

# Запускаем проверку прямо сейчас — в отличие от orchestrator_discipline,
# ей не нужен внешний лог прогона, достаточно текущих файлов скилла.
check_no_silent_fallback_markers "$SKILL_PATH"


# =============================================================================
# ФОРМИРОВАНИЕ ИТОГОВОГО ОТЧЁТА
# =============================================================================

# Определяем общий статус
if [ "$CRITICAL_FAILED" -eq 0 ]; then
  OVERALL_STATUS="PASS"
else
  OVERALL_STATUS="FAIL"
fi

# Формируем JSON-отчёт
REPORT=$(python3 -c "
import json

report = {
    'test_name': 'structural-validation',
    'skill': 'feature-pipeline',
    'timestamp': '${TIMESTAMP}',
    'critical_checks': ${CRITICAL_CHECKS},
    'non_critical_checks': ${NON_CRITICAL_CHECKS},
    'summary': {
        'critical_passed': ${CRITICAL_PASSED},
        'critical_failed': ${CRITICAL_FAILED},
        'warnings': ${WARNINGS},
        'status': '${OVERALL_STATUS}'
    }
}

print(json.dumps(report, indent=2, ensure_ascii=False))
")

# Выводим отчёт
echo "$REPORT"

# Код возврата: 0 если PASS, 1 если FAIL
if [ "$OVERALL_STATUS" = "FAIL" ]; then
  exit 1
fi
