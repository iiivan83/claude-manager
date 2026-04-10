---
name: feature-pipeline
description: >-
  Полный цикл доработки существующего функционала — от приёма задачи до коммита.
  Автоматический пайплайн: анализ задачи, спецификация, тест-план, реализация,
  тестирование (юнит/интеграция/E2E), 5-проходное ревью, документация, коммит.
  Используй когда пользователь говорит: «добавь фичу», «доработай», «реализуй задачу»,
  «feature pipeline», «добавь функциональность», «implement feature», «сделай доработку»,
  «feature request», «добавь возможность», «реализуй требование».
---

# Feature Pipeline

Полный цикл доработки существующего функционала — от приёма задачи до коммита. Автоматический пайплайн: анализ задачи, спецификация, тест-план, реализация, тестирование (юнит/интеграция/E2E), 5-проходное ревью, документация, коммит.

## Global Reference Rules

Перед началом работы прочитай:
- `~/.claude/references/document-naming-and-placement.md` — правила документооборота
- `~/.claude/references/writing-style-guide.md` — единый стиль письма для всех документов и отчётов

## Глубина мышления

Думай как опытный тех-лид, который лично отвечает за стабильность продакшена. Каждое решение
проходит через внутренний чек-лист:

- **Побочные эффекты** — что может сломаться в соседних модулях из-за этого изменения?
- **Граничные случаи** — как это поведёт себя на пустых данных, при конкурентном доступе,
  при неожиданном порядке вызовов?
- **Откат** — если изменение окажется неудачным, как быстро можно вернуть всё назад?
- **Альтернативы** — есть ли более простой способ добиться того же результата?
- **Достаточность** — это изменение действительно решает корневую причину, а не маскирует
  симптом?

Все агенты этого скилла работают с такой же глубиной — разбирают задачу по частям,
рассматривают альтернативы и перепроверяют выводы перед тем, как действовать.

## Входные данные

Пользователь описывает задачу в произвольной форме: промпт, описание, BRD.

## Константы

- `LOG_SUBCATEGORY`: `skills-modifications`
- `PIPELINE_NAME`: `feature-pipeline`
- `MAX_FIX_ATTEMPTS`: 3
- `MAX_ROLLBACKS`: 2
- `MAX_TEST_PLAN_ITERATIONS`: 3

## Resume Mode

При запуске проверь наличие `pipeline-state.json` в папке логов:
- Если найден и `status` не `completed` — предложи пользователю продолжить с последней незавершённой фазы
- При продолжении: пропусти завершённые фазы, начни с `current_phase`
- Все временные метки остаются прежними

## Pipeline Stages

### Фаза 0 — Приём задачи и классификация

**Соглашение о путях:** все относительные пути (`agent-outputs/`, `test-reports/`, `pipeline-state.json`) — подпапки/файлы внутри папки логов пайплайна. Полный пример: `dev/docs/logs/skills-modifications/{timestamp}-feature-pipeline-{task-name}/agent-outputs/00-feature-intake.json`.

1. Прочитай `~/.claude/references/document-naming-and-placement.md`
2. Сгенерируй `folder_timestamp` (DD.MM_HH.MM) — используется повсюду
3. Создай папку логов: `dev/docs/logs/skills-modifications/{timestamp}-feature-pipeline-{task-name}/`
4. Создай подпапку `agent-outputs/` внутри папки логов
5. Создай `orchestrator-log.json` в папке логов
6. Запусти агента `agents/feature-intake.md` (через Agent tool):
   - Передай задачу пользователя + путь к папке логов
   - Получи результат → запиши в `agent-outputs/00-feature-intake.json`
7. Создай `pipeline-state.json` в папке логов с начальным состоянием (из результата агента)
8. Запиши шаг в `orchestrator-log.json`

**Сканирование проектных скиллов:**
Перед каждой фазой сканируй `.claude/skills/`:
- **Исключи** `feature-pipeline` из сканирования (это сам пайплайн — self-reference)
- Прочитай `SKILL.md` каждого остального скилла
- Если скилл подходит для текущей фазы:
  - Нетестовые фазы (0-4, 8-9): проектный скилл ЗАМЕНЯЕТ внутреннего агента — вызови через CLI:
    ```bash
    source ~/.claude/cli-budgets.env 2>/dev/null || true
    env -u CLAUDECODE claude -p \
      --output-format text \
      --effort max \
      --dangerously-skip-permissions \
      --max-budget-usd "$BUDGET_NORMAL" \
      <<'PROMPT'
    Используй /{имя-проектного-скилла}.
    Задача: {описание задачи текущей фазы}
    Входные данные: прочитай {пути к agent-outputs предыдущих фаз}
    Папка логов: {LOG_DIR}
    PROMPT
    ```
  - Тестовые фазы (5-7): СОВМЕСТНАЯ работа внутреннего агента и проектного скилла — проектный скилл вызывается через CLI аналогично, результат передаётся внутреннему агенту
- **Лимит промпта:** не более 7000 символов. Передавай пути к файлам, а не содержимое.
- **После CLI-вызова:** проверь exit code (0 = успех). Если ошибка или пустой ответ — откатись к внутреннему агенту (fallback).
- **Валидация выхода проектного скилла:** после вызова проверь, что результат содержит обязательные поля из `references/schemas.json` для этой фазы. Если обязательные поля отсутствуют — откатись к внутреннему агенту (fallback)

### Фаза 1 — Исследование и анализ влияния

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-impact-analysis.md` (через Agent tool)
3. Агент сам запускает 3 параллельных суб-агента (код, спеки, тесты)
4. Получи `agent-outputs/01-impact-analysis.json`
5. Если суб-агент упал — продолжай с неполными данными
6. Обнови `pipeline-state.json`

### Фаза 2 — Спецификация изменений

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-spec.md` (через Agent tool)
3. Если это повторный проход (после отката) — передай `rollback_feedback` из `pipeline-state.json`
4. Получи `agent-outputs/02-feature-spec.json`
5. Обнови `pipeline-state.json`

### Фаза 3 + 3.5 — Тест-план и верификация

**Цикл (максимум MAX_TEST_PLAN_ITERATIONS итераций):**

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-test-plan.md` → `agent-outputs/03-test-plan.json`
3. Запусти агента `agents/feature-test-plan-review.md` → `agent-outputs/03-5-test-plan-review.json`
4. Если `verdict` = `approved` → переходи к Фазе 4
5. Если `verdict` = `rejected` → передай замечания обратно в Фазу 3, повтори
6. Если исчерпаны итерации → эскалация (откат или остановка)

### Фаза 4 — Реализация

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-implement.md` (через Agent tool)
3. Агент сам координирует создание каждого изменения отдельным суб-агентом
4. После каждого изменения — прогон юнит-тестов внутри агента
5. Получи `agent-outputs/04-implementation.json`
6. Если `status` = `failed` → запусти **цикл исправления** (см. ниже)
7. Если суб-агент упал (crash) → сразу в откат
8. Обнови state

### Фаза 5 — Юнит-тестирование

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-unit-test.md` (через Agent tool)
3. Агент использует `scripts/run-unit-tests.sh`
4. Получи `agent-outputs/05-unit-test.json`
5. Если `needs_rollback` = true → откат
6. Обнови state

**Маршрутизация после Фазы 5:**
- Если `scale` = `small` → пропусти Фазы 6, 7, переходи к Фазе 8
- Если `scale` = `medium` или `large` → переходи к Фазе 6

### Фаза 6 — Интеграционное тестирование

**Условие:** `scale` >= `medium`. При `small` — пропускается.

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-integration-test.md` (через Agent tool)
3. Агент использует `scripts/run-integration-tests.sh`
4. Получи `agent-outputs/06-integration-test.json`
5. Если `needs_rollback` = true → откат
6. Обнови state

### Фаза 7 — E2E тестирование

**Условие:** `scale` >= `medium`. При `small` — пропускается.

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-e2e-test.md` (через Agent tool)
3. Агент использует `scripts/run-e2e-tests.sh`
4. Получи `agent-outputs/07-e2e-test.json`
5. Если `needs_rollback` = true → откат
6. Обнови state

### Фаза 8 — Ревью (5 проходов)

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-review.md` (через Agent tool)
3. Агент сам запускает 5 параллельных суб-агентов ревью
4. При нахождении проблем — автоисправление + повторный прогон тестов + повторное ревью
5. Получи `agent-outputs/08-review.json`
6. Если `needs_rollback` = true → откат
7. Если суб-агент упал (crash) → сразу в откат
8. Обнови state

### Фаза 9 — Документация

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-docs.md` (через Agent tool)
3. Агент сам запускает 4 параллельных документалиста + 1 последний (docs-index)
4. Получи `agent-outputs/09-docs.json`
5. Если суб-агент упал — продолжай без его документа
6. Обнови state

### Фаза 10 — Финальная проверка и коммит

1. Запиши шаг в `orchestrator-log.json` (ДО запуска агента)
2. Запусти агента `agents/feature-finalizer.md` (через Agent tool). Если финализатор правит файлы внутри `.claude/skills/` из CLI-подпроцесса, он использует штатные шаблоны X.1/X.2 из корневого `CLAUDE.md` (раздел «Запись в `.claude/skills/` из CLI-подпроцессов»). Подробности — в `agents/feature-finalizer.md`.
2. Агент использует `scripts/final-check.sh`
3. **Финальная петля качества** (максимум 3 итерации):
   - Полный прогон всех тестов
   - Полное ревью (5 проходов)
   - Если проблемы → исправление → повтор цикла
4. Получи `agent-outputs/10-finalizer.json`
5. Если `needs_rollback` = true → откат
6. Если всё ОК:
   - Git commit (агент делает)
   - Предложить пушнуть (но НЕ пушить автоматически)
   - Запустить сессионный отчёт (обязательно для пайплайнов, правило из AGENTS.md). Промпт агенту:
     ```
     Используй /session-report.
     Контекст: feature-pipeline завершён.
     Задача: {краткое описание задачи из Фазы 0}
     Папка логов: {LOG_DIR}
     ```
7. Обнови state → `completed`

## Цикл исправления (Fix Cycle)

Применяется на фазах: 4, 5, 6, 7, 8, 10.

```
Попытка 1 → Ошибка → Исправление → Попытка 2 → Ошибка → Исправление → Попытка 3 → Ошибка → ОТКАТ
```

- Максимум MAX_FIX_ATTEMPTS попыток на каждой фазе
- Каждая попытка: агент анализирует ошибку → исправляет код/тест → повторяет фазу
- Счётчик `fix_cycles` записывается в `pipeline-state.json` для каждой фазы

## Механика отката (Rollback)

При провале после MAX_FIX_ATTEMPTS попыток на ЛЮБОЙ тестовой/ревью фазе:

1. **Создать резервную ветку:** `git checkout -b backup/feature-pipeline-{timestamp}-attempt-{N}`
2. **Вернуться на рабочую ветку:** `git checkout {original-branch}`
3. **Git reset:** `git reset --hard {commit-before-phase-4}` — удалить все изменения кода до состояния перед Фазой 4
4. **Обратная связь:** сформировать описание — что пробовали, почему не сработало
5. **Записать в state:** добавить в `rollback_feedback`, увеличить `rollbacks_to_spec`, записать ветку в `backup_branches`
6. **Сбросить фазы:** все фазы начиная с 2 → `pending`
7. **Возврат на Фазу 2:** пайплайн идёт заново с Фазы 2

**Максимум MAX_ROLLBACKS откатов.** После второго отката — СТОП.

### Структурированная сводка при остановке

Показать пользователю:
- **Задача:** описание из Фазы 0
- **Попытка 1:** подход → на какой фазе и почему провал
- **Попытка 2:** подход → на какой фазе и почему провал
- **Рекомендация:** что попробовать вручную, какие резервные ветки содержат наработки

### Что НЕ откатывается

- Фаза 0 (задача не меняется)
- Фаза 1 (анализ влияния остаётся)
- Логи пайплайна (всё сохраняется)
- `pipeline-state.json` (обновляется, не сбрасывается)

## Cleanup / Abort

При аварийном завершении:
1. Записать `"status": "stopped"` в `orchestrator-log.json`
2. Обновить `pipeline-state.json` с текущим состоянием
3. Показать пользователю: что успешно завершено, что не удалось, где лежат логи

## Logging

**Паттерн логирования:** запись в orchestrator-log.json делается ДО начала каждого этапа (не после). Это позволяет видеть, на каком этапе пайплайн прервался.

Каждый шаг ОБЯЗАН записываться в `orchestrator-log.json` ДО начала работы этапа. Без записи шаг считается невыполненным.

Формат записи:
```json
{
  "timestamp": "ISO-8601",
  "phase": "номер фазы",
  "agent": "имя агента",
  "action": "start | complete | fail | rollback | fix_cycle",
  "details": "описание",
  "output_file": "agent-outputs/NN-agent-name.json",
  "fix_cycle_attempt": 0,
  "duration_ms": 0
}
```

## Reference Files

- `agents/feature-intake.md` — приём задачи (Фаза 0)
- `agents/feature-impact-analysis.md` — анализ влияния (Фаза 1, координатор + 3 суб-агента)
- `agents/feature-spec.md` — спецификация изменений (Фаза 2)
- `agents/feature-test-plan.md` — тест-план (Фаза 3)
- `agents/feature-test-plan-review.md` — верификация тест-плана (Фаза 3.5)
- `agents/feature-implement.md` — реализация (Фаза 4, координатор)
- `agents/feature-unit-test.md` — юнит-тесты (Фаза 5)
- `agents/feature-integration-test.md` — интеграционные тесты (Фаза 6)
- `agents/feature-e2e-test.md` — E2E тесты (Фаза 7)
- `agents/feature-review.md` — ревью (Фаза 8, координатор + 5 суб-агентов)
- `agents/feature-review-quality.md` — качество кода
- `agents/feature-review-security.md` — безопасность
- `agents/feature-review-architecture.md` — архитектура
- `agents/feature-review-spec-compliance.md` — соответствие спецификации
- `agents/feature-review-regression.md` — регрессия
- `agents/feature-docs.md` — документация (Фаза 9, координатор + 5 суб-агентов)
- `agents/feature-docs-changelog.md` — changelog
- `agents/feature-docs-adr.md` — ADR
- `agents/feature-docs-claude-md.md` — CLAUDE.md
- `agents/feature-docs-brd.md` — BRD
- `agents/feature-docs-index.md` — docs-index
- `agents/feature-finalizer.md` — финальная проверка (Фаза 10)
- `scripts/run-unit-tests.sh` — запуск юнит-тестов
- `scripts/run-integration-tests.sh` — запуск интеграционных тестов
- `scripts/run-e2e-tests.sh` — запуск E2E тестов
- `scripts/final-check.sh` — финальная валидация
- `references/schemas.json` — JSON-схемы всех структур данных
- `references/scale-matrix.md` — матрица масштабов
