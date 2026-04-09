---
name: universal-bug-fixer
description: "Универсальный пайплайн исправления ошибок — принимает любую проблему (баг, ошибка, несоответствие) в любом формате и проводит через цепочку агентов до полного исправления с двойной верификацией. Используй когда пользователь говорит: «исправь баг», «почини», «fix bug», «исправь ошибку», «что-то сломалось», «не работает», «баг», «fix», «debug», «отладь», «почему ломается», «исправь проблему», «bug fix», «починка»."
---

# universal-bug-fixer

Универсальный пайплайн исправления ошибок. Принимает от пользователя любую проблему (баг, ошибка, несоответствие) в любом формате и проводит через цепочку агентов до полного исправления с двойной верификацией.

## Global Reference Rules

Перед началом работы прочитай:
- `~/.claude/references/document-naming-and-placement.md` — правила документооборота (куда складывать логи, как называть папки и файлы, формат временных меток)
- `~/.claude/references/writing-style-guide.md` — единый стиль письма для всех документов и отчётов

Если файлы не найдены — СТОП с ошибкой.

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

## Вход

Описание проблемы от пользователя в произвольном формате: текст ошибки, скриншот, путь к файлу, описание словами, лог, ссылка, стектрейс.

## Выход

- Исправленный код/конфигурация/документация
- Тесты (белый + черный ящик), перенесённые в стандартные папки проекта
- Технический отчёт `report.md`
- Краткий отчёт пользователю в чат

## Константы

- **LOG_SUBCATEGORY:** `bugfix`
- **PIPELINE_NAME:** `universal-bug-fixer`

## Рабочая директория пайплайна

Все относительные пути (`agent-outputs/`, `pipeline-workspace/`, `orchestrator-log.json`) указаны относительно **папки лога пайплайна** (`{LOG_DIR}`).

Формат пути:
```
dev/docs/logs/bugfix/{DD.MM_HH.MM}-universal-bug-fixer-{problem-slug}/
```

`{problem-slug}` — короткое имя проблемы в kebab-case (например, `broken-parser`, `missing-config`).

## Resume Mode

При запуске проверь, передал ли пользователь флаг `--resume`:
1. Найди последний orchestrator-log.json в `dev/docs/logs/bugfix/`
2. Прочитай `last_completed_stage`
3. Пропусти завершённые этапы, продолжи с последнего незавершённого

## Этап 0 — PRE-FLIGHT

Выполняется оркестратором (не отдельный агент).

### 0.1 — Чтение глобальных настроек
- Прочитай `~/.claude/references/document-naming-and-placement.md`
- Извлеки путь к логам (раздел 3.2), формат дат (раздел 2)

### 0.2 — Подготовка рабочей директории
1. Создай `{problem-slug}` из описания проблемы (kebab-case, 2-4 слова)
2. Сгенерируй `folder_timestamp` (DD.MM_HH.MM) и `file_timestamp` (DD-MM-HH-MM)
3. Создай папку: `dev/docs/logs/bugfix/{folder_timestamp}-universal-bug-fixer-{problem-slug}/`
4. Внутри создай подпапки: `agent-outputs/`, `pipeline-workspace/tests/`, `pipeline-workspace/tests/blackbox/`
5. Сохрани абсолютный путь как `{LOG_DIR}` — передавай каждому агенту

### 0.3 — Проверка скиллов
Проверь наличие обязательных и условных скиллов:

**Обязательные** (без них СТОП):
- `root-cause-analysis` — `.claude/skills/root-cause-analysis/SKILL.md`
- `session-report` — `~/.claude/commands/session-report.md`

**Условные** (нужны при определённых стратегиях):
- `skill-creator` — `.claude/skills/skill-creator/SKILL.md`
- `script-creator-pipeline` — `.claude/skills/script-creator-pipeline/SKILL.md`

### 0.4 — Инициализация orchestrator-log.json

Создай `{LOG_DIR}/orchestrator-log.json`:
```json
{
  "pipeline": "bugfix",
  "problem_summary": "<краткое описание>",
  "created_at": "<ISO-8601>",
  "last_completed_stage": null,
  "folder_timestamp": "<DD.MM_HH.MM>",
  "available_skills": {
    "required": {
      "root-cause-analysis": { "found": true/false, "path": "..." },
      "session-report": { "found": true/false, "path": "..." }
    },
    "conditional": {
      "skill-creator": { "found": true/false, "path": "..." },
      "script-creator-pipeline": { "found": true/false, "path": "..." }
    }
  },
  "folder_timestamp": "<DD.MM_HH.MM>",
  "file_timestamp": "<DD-MM-HH-MM>",
  "backup_branch": null,
  "verification": null,
  "steps": []
}
```

Если обязательный скилл не найден — СТОП с сообщением.

## Этап 1 — ВХОД (intake)

- **Агент:** `agents/intake.md`
- **Запуск:** Agent tool
- **Вход:** описание проблемы от пользователя + `{LOG_DIR}`
- **Выход:** `{LOG_DIR}/agent-outputs/01-intake.json`

Промпт для агента:
```
Прочитай инструкции из {SKILL_PATH}/agents/intake.md.
LOG_DIR = {LOG_DIR}
Описание проблемы: {user_input}
```

**ДО запуска агента:** записать step в orchestrator-log.json. **После завершения:** обновить last_completed_stage = 1.

## Этап 2 — ПОНИМАНИЕ (understanding)

- **Агент:** `agents/understanding.md`
- **Запуск:** Agent tool
- **Вход:** `{LOG_DIR}/agent-outputs/01-intake.json` + `{LOG_DIR}`
- **Выход:** `{LOG_DIR}/agent-outputs/02-understanding.json`

Промпт:
```
Прочитай инструкции из {SKILL_PATH}/agents/understanding.md.
LOG_DIR = {LOG_DIR}
```

**Проверка после завершения:**
- Если `confidence: low` — показать вопросы пользователю, дождаться ответов, перезапустить этап 2
- Если `confidence: medium/high` — продолжить

## Этап 3 — ПОИСК ПЕРВОПРИЧИНЫ (root-cause-investigator)

- **Агент:** `agents/root-cause-investigator.md`
- **Запуск:** Agent tool (агент внутри вызовет root-cause-analysis через CLI: Bash → claude -p)
- **Вход:** `01-intake.json` + `02-understanding.json` + артефакты + `{LOG_DIR}`
- **Выход:** `{LOG_DIR}/agent-outputs/03-root-cause.json` (или `03-root-cause-iter-{N}.json`)

При повторном запуске (большая петля): передать `previous_attempts`.

**Проверка после завершения:**
- Если все deviations имеют `not_a_bug: true` — СТОП с объяснением пользователю
- Если `total_deviations = 0` — СТОП

## Этап 4 — КЛАССИФИКАЦИЯ (classifier)

- **Агент:** `agents/classifier.md`
- **Запуск:** Agent tool
- **Вход:** `02-understanding.json` + `03-root-cause.json` + `{LOG_DIR}`
- **Выход:** `{LOG_DIR}/agent-outputs/04-classification.json` (или `-iter-{N}.json`)

## Этап 5 — ТЕСТИРОВАНИЕ (test-strategist)

- **Агент:** `agents/test-strategist.md`
- **Запуск:** Agent tool
- **Вход:** все JSON этапов 1-4 + файловая система проекта + `{LOG_DIR}`
- **Выход:** `05-test-strategy.json` + файлы тестов в `pipeline-workspace/tests/` и `pipeline-workspace/tests/blackbox/`

**Проверка:** если `environment_validated.passed = false` — СТОП с объяснением.

## Этап 6 — ВЕРИФИКАЦИЯ РЕШЕНИЙ (critical-verifier)

- **Агент:** `agents/critical-verifier.md`
- **Запуск:** Agent tool
- **Вход:** все JSON этапов 1-5 + `{LOG_DIR}`
- **Выход:** `06-verification.json` (или `-iter-{N}.json`)

**ВАЖНО:** После получения результата — ПОКАЗАТЬ ПОЛЬЗОВАТЕЛЮ:
1. Список найденных отклонений с первопричинами
2. Классификацию каждого
3. Вердикт верификатора по каждому
4. Порядок исправления
5. Предлагаемый план действий

Формат показа:
```
--- Найденные проблемы ---

DEV-{N}: {описание}
  Причина: {chain_of_events}
  Тип: {problem_type} | Масштаб: {scale} | Критичность: {severity}
  Вердикт: {verdict}
  {adjustments, если есть}

--- Порядок исправления ---
1. DEV-{N} ({strategy})
...

Подтверждаешь план? (да / нет / скорректировать)
```

**При повторных циклах (дельта-подтверждение):** показывать ТОЛЬКО изменения.

Дождаться подтверждения пользователя:
- "да" / подтверждение → продолжить
- Корректировки → применить и продолжить
- "нет" / отмена → СТОП

**Логика переходов по overall_verdict:**
- `approved` → этап 7
- `adjusted` → применить корректировки, → этап 7
- `rejected` → возврат к этапу из `return_to_stage` (по умолчанию 3)
- `all_skip` → СТОП с объяснением

## Этап 7 — СТРАТЕГИЯ ИСПРАВЛЕНИЯ (fix-strategist)

- **Агент:** `agents/fix-strategist.md`
- **Запуск:** Agent tool
- **Вход:** все JSON + `available_skills` из orchestrator-log.json + `{LOG_DIR}`
- **Выход:** `07-fix-strategy.json` (или `-iter-{N}.json`)

**Проверка:** если status: "failed" (нужный скилл не найден) — СТОП.

## Подготовка к исполнению (оркестратор)

Перед этапом 8 оркестратор делает:

### Git backup
```bash
git checkout -b bugfix/{folder_timestamp}-{problem-slug}-backup
git checkout -  # вернуться на рабочую ветку
```
Записать имя ветки в `orchestrator-log.json` → `backup_branch`.

### Изоляция черного ящика
1. Создай временную папку: `/tmp/bugfix-blackbox-{folder_timestamp}/`
2. Переместить `{LOG_DIR}/pipeline-workspace/tests/blackbox/` → `/tmp/bugfix-blackbox-{folder_timestamp}/`
3. После завершения этапа 8 — восстановить обратно
4. Перенести файлы из `new-blackbox-staging/` в `blackbox/`

## Этап 8 — ИСПОЛНЕНИЕ (executor) — ЦИКЛ ПО ОТКЛОНЕНИЯМ

Определи порядок: `order_override` из этапа 7 или `execution_order` из этапа 4.

**Для КАЖДОГО отклонения из порядка:**

1. Запусти агент `agents/executor.md` через Agent tool
2. Передай: стратегию для этого конкретного DEV-{N}, все JSON, `{LOG_DIR}`
3. Результат: `08-execution.json` (при повторах: `08-execution-iter-{N}.json`)

## Этап 9 — ВЕРИФИКАЦИЯ (test-runner)

- **Агент:** `agents/test-runner.md`
- **Запуск:** Agent tool
- **Вход:** `08-execution.json` + тесты + `{LOG_DIR}`
- **Выход:** `09-verification.json` (или `-iter-{N}.json`)

### Retry-логика (управляет ОРКЕСТРАТОР, не агент)

Счётчик циклов — отдельный на каждое отклонение.

```
Если all_passed = true:
  → перенести тесты в проект (агент делает сам)
  → следующее отклонение

Если all_passed = false:
  fail_count[DEV-N] += 1

  Если fail_count[DEV-N] нечётный (1, 3, 5, 7, 9):
    → МАЛАЯ ПЕТЛЯ: назад к этапу 8 с информацией о провале
    → Передать: какие тесты провалены (статус + сообщение, НЕ содержимое blackbox)

  Если fail_count[DEV-N] чётный (2, 4, 6, 8, 10):
    big_cycle_count[DEV-N] += 1

    Если big_cycle_count[DEV-N] <= 5:
      → БОЛЬШАЯ ПЕТЛЯ: назад к этапу 3 с previous_attempts
      → Все этапы 3→4→5→6→7→8→9 заново
      → Файлы с суффиксом -iter-{big_cycle_count}

    Если big_cycle_count[DEV-N] > 5:
      → СТОП для этого отклонения
      → Записать отчёт о неудаче
      → Перейти к следующему отклонению из execution_order
```

## Этап 10 — ОТЧЕТ (report-generator)

- **Агент:** `agents/report-generator.md`
- **Запуск:** Agent tool (агент внутри вызовет session-report через Agent tool)
- **Вход:** orchestrator-log.json + все agent-outputs + `{LOG_DIR}`
- **Выход:** `report.md` + отчёт в чат

## Cleanup / Abort

При аварийном завершении на любом этапе:
1. Записать текущий статус в orchestrator-log.json (`last_completed_stage`, `steps` с ошибкой)
2. Показать пользователю что произошло
3. Если был git backup — сообщить имя ветки для отката

## Обновление orchestrator-log.json

**Паттерн логирования:** запись в orchestrator-log.json делается ДО начала каждого этапа (не после). Это позволяет видеть, на каком этапе пайплайн прервался.

ДО КАЖДОГО этапа записывать в `steps[]`:
```json
{
  "step_number": N,
  "start_time": "ISO-8601",
  "end_time": "ISO-8601",
  "agent": "имя-агента",
  "input": ["пути к входным файлам"],
  "success": true/false,
  "comment": "Описание результата",
  "output": ["пути к выходным файлам"],
  "next_agent": "имя или null"
}
```
Обновить `last_completed_stage = N`.

## Суффиксы итераций

При большом цикле (возврат к этапу 3) файлы этапов 3-9 получают суффикс `-iter-{N}`:
- `03-root-cause-iter-2.json`
- `04-classification-iter-2.json`
- `05-test-strategy-iter-2.json`
- `06-verification-iter-2.json`
- `07-fix-strategy-iter-2.json`
- `08-execution-iter-2.json`
- `09-verification-iter-2.json`

Предыдущие файлы НЕ перезаписываются.

## Reference Files

- `agents/intake.md` — Этап 1 (Вход)
- `agents/understanding.md` — Этап 2 (Понимание)
- `agents/root-cause-investigator.md` — Этап 3 (Первопричина)
- `agents/classifier.md` — Этап 4 (Классификация)
- `agents/test-strategist.md` — Этап 5 (Тестирование)
- `agents/critical-verifier.md` — Этап 6 (Верификация решений)
- `agents/fix-strategist.md` — Этап 7 (Стратегия)
- `agents/executor.md` — Этап 8 (Исполнение)
- `agents/test-runner.md` — Этап 9 (Верификация)
- `agents/report-generator.md` — Этап 10 (Отчёт)
- `references/schemas.json` — JSON-схемы всех структур данных
