---
name: create-test-runner
description: >-
  Генерация bash-скрипта run-all-tests.sh для запуска всех тестов проекта.
  Создаёт dev/scripts/run-all-tests.sh — единый скрипт, который запускает
  юнит-тесты и интеграционные тесты, выводит форматированный отчёт и возвращает
  правильный exit code. Используй когда пользователь говорит «создай тест-раннер»,
  «create test runner», «сгенерируй run-all-tests», «нужен скрипт для запуска тестов»,
  «test runner script», «скрипт тестов», или при первоначальной настройке проекта,
  когда нужно наладить запуск тестов. Также используй если другой скилл или пайплайн
  требует наличие run-all-tests.sh. НЕ используй для запуска тестов напрямую — этот
  скилл СОЗДАЁТ скрипт, а не запускает его.
---

# Генерация скрипта run-all-tests.sh

**THINK HARD** на каждом шаге выполнения этого скилла. Используй максимальную глубину мышления при принятии каждого решения.

Ты создаёшь bash-скрипт `dev/scripts/run-all-tests.sh` — единую точку входа для запуска всех тестов проекта. Скрипт нужен другим скиллам пайплайна (implement-module, test-integration и др.) для быстрой проверки после изменений в коде. Без него каждый скилл был бы вынужден запускать тесты своими руками, дублируя логику и рискуя забыть какой-то набор тестов.

## Что делает скрипт

Скрипт последовательно запускает два набора тестов:

- **Юнит-тесты** — из папки `tests/`, исключая интеграционные и e2e-тесты. Это быстрые тесты, которые проверяют отдельные функции и модули в изоляции.
- **Интеграционные тесты** — из папки `tests/integration/`, если она существует. Эти тесты проверяют взаимодействие между модулями.

После запуска скрипт выводит форматированный отчёт с количеством пройденных и упавших тестов, и возвращает exit code: 0 если всё прошло, 1 если есть провалы.

## Шаг 1: Проверь текущее состояние

**THINK HARD** — прежде чем генерировать скрипт, пойми контекст.

1. Проверь, существует ли уже файл `dev/scripts/run-all-tests.sh`. Если да — предупреди пользователя, что файл будет перезаписан, и спроси подтверждение.
2. Проверь структуру папки `tests/` — какие подпапки и файлы тестов существуют. Это поможет убедиться, что скрипт покрывает все имеющиеся тесты.
3. Убедись, что папка `dev/scripts/` существует. Если нет — создай её.

## Шаг 2: Сгенерируй скрипт

**THINK HARD** — скрипт должен быть надёжным, понятным и правильно обрабатывать все случаи.

Создай файл `dev/scripts/run-all-tests.sh` со следующим содержимым:

```bash
#!/usr/bin/env bash
#
# run-all-tests.sh — запуск всех тестов проекта (юнит + интеграционные).
# Используется скиллами пайплайна для быстрой проверки после изменений.
#
# Использование: ./dev/scripts/run-all-tests.sh
# Exit codes: 0 — все тесты прошли, 1 — есть провалы.

set -euo pipefail

# Переход в корень проекта (скрипт может запускаться из любой директории)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Счётчики результатов
UNIT_PASSED=0
UNIT_FAILED=0
INTEGRATION_PASSED=0
INTEGRATION_FAILED=0
HAS_FAILURES=0

# Цвета для вывода (отключаются, если вывод не в терминал)
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[1;33m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    GREEN=''
    RED=''
    YELLOW=''
    BOLD=''
    RESET=''
fi

# Извлечение количества тестов из вывода pytest
parse_pytest_results() {
    local output="$1"
    local passed=0
    local failed=0

    # pytest выводит строку вида "X passed" и/или "X failed"
    passed=$(echo "$output" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "0")
    failed=$(echo "$output" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo "0")

    echo "$passed $failed"
}

# --- Юнит-тесты ---
echo -e "${BOLD}=== Юнит-тесты ===${RESET}"
echo ""

UNIT_OUTPUT=""
if UNIT_OUTPUT=$(python -m pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ -v 2>&1); then
    echo "$UNIT_OUTPUT"
else
    echo "$UNIT_OUTPUT"
    HAS_FAILURES=1
fi

read -r UNIT_PASSED UNIT_FAILED <<< "$(parse_pytest_results "$UNIT_OUTPUT")"

echo ""

# --- Интеграционные тесты ---
if [ -d "tests/integration" ]; then
    echo -e "${BOLD}=== Интеграционные тесты ===${RESET}"
    echo ""

    INTEGRATION_OUTPUT=""
    if INTEGRATION_OUTPUT=$(python -m pytest tests/integration/ -v 2>&1); then
        echo "$INTEGRATION_OUTPUT"
    else
        echo "$INTEGRATION_OUTPUT"
        HAS_FAILURES=1
    fi

    read -r INTEGRATION_PASSED INTEGRATION_FAILED <<< "$(parse_pytest_results "$INTEGRATION_OUTPUT")"

    echo ""
else
    echo -e "${YELLOW}Папка tests/integration/ не найдена — интеграционные тесты пропущены.${RESET}"
    echo ""
fi

# --- Итоговый отчёт ---
TOTAL_PASSED=$((UNIT_PASSED + INTEGRATION_PASSED))
TOTAL_FAILED=$((UNIT_FAILED + INTEGRATION_FAILED))
TOTAL=$((TOTAL_PASSED + TOTAL_FAILED))

echo -e "${BOLD}=== Итоговый отчёт ===${RESET}"
echo ""
echo -e "  Юнит-тесты:          ${GREEN}${UNIT_PASSED} прошло${RESET}  ${RED}${UNIT_FAILED} упало${RESET}"

if [ -d "tests/integration" ]; then
    echo -e "  Интеграционные тесты: ${GREEN}${INTEGRATION_PASSED} прошло${RESET}  ${RED}${INTEGRATION_FAILED} упало${RESET}"
fi

echo ""
echo -e "  ${BOLD}Всего: ${TOTAL} тестов — ${GREEN}${TOTAL_PASSED} прошло${RESET}, ${RED}${TOTAL_FAILED} упало${RESET}"
echo ""

if [ "$HAS_FAILURES" -eq 1 ]; then
    echo -e "  ${RED}${BOLD}РЕЗУЛЬТАТ: ЕСТЬ ПРОВАЛЫ${RESET}"
    exit 1
else
    echo -e "  ${GREEN}${BOLD}РЕЗУЛЬТАТ: ВСЕ ТЕСТЫ ПРОЙДЕНЫ${RESET}"
    exit 0
fi
```

## Шаг 3: Сделай скрипт исполняемым

После создания файла выполни:

```bash
chmod +x dev/scripts/run-all-tests.sh
```

## Шаг 4: Проверь синтаксис

**THINK HARD** — синтаксическая ошибка в скрипте сломает весь пайплайн.

Выполни проверку синтаксиса bash:

```bash
bash -n dev/scripts/run-all-tests.sh
```

Если проверка выявит ошибки — исправь их и повтори проверку. Не завершай работу, пока скрипт не пройдёт `bash -n` без ошибок.

## Шаг 5: Отчёт

Выведи итоговый отчёт:

```
## Результат

- **Создан:** dev/scripts/run-all-tests.sh
- **Права:** исполняемый (chmod +x)
- **Синтаксис:** проверен (bash -n)
- **Запуск:** ./dev/scripts/run-all-tests.sh

### Что делает скрипт
- Запускает юнит-тесты из tests/ (без integration/ и e2e/)
- Запускает интеграционные тесты из tests/integration/ (если папка есть)
- Выводит форматированный отчёт с количеством пройденных/упавших
- Возвращает exit code 0 (всё прошло) или 1 (есть провалы)
```
