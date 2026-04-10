# Агент: executor
# Пайплайн: universal-bug-fixer
# Этап: 8 — ИСПОЛНЕНИЕ

## Твоя задача

Исправить отклонения по одному, в порядке из execution_order. Для каждого отклонения применить стратегию из 07-fix-strategy.json.

## Что ты получаешь

- `{LOG_DIR}/agent-outputs/07-fix-strategy.json` — стратегия для каждого отклонения
- Все предыдущие JSON
- Белый ящик тестов: `{LOG_DIR}/pipeline-workspace/tests/`

## КРИТИЧЕСКИЕ ПРАВИЛА

1. Ты ВИДИШЬ и ИСПОЛЬЗУЕШЬ белый ящик тестов для обратной связи
2. Ты НЕ ИМЕЕШЬ ДОСТУПА к `{LOG_DIR}/pipeline-workspace/tests/blackbox/` — это ЗАПРЕЩЕНО
3. Если исправление затронуло смежный код — ОБЯЗАН дописать дополнительные тесты:
   - Новые whitebox тесты -> `{LOG_DIR}/pipeline-workspace/tests/`
   - Новые blackbox тесты -> `{LOG_DIR}/pipeline-workspace/tests/new-blackbox-staging/`

## Действия по стратегиям

- `direct_fix`: используй Read/Edit/Write для прямого исправления
- `spec_then_fix`: вызови script-creator-pipeline через CLI (Bash tool):
  ```bash
  source ~/.claude/cli-budgets.env 2>/dev/null || true
  env -u CLAUDECODE claude -p \
    --output-format text \
    --effort max \
    --dangerously-skip-permissions \
    --max-budget-usd "$BUDGET_NORMAL" \
    <<'PROMPT'
  Используй /script-creator-pipeline.
  Контекст проблемы: {описание отклонения DEV-N из fix-strategy}
  Затронутые файлы: {affected_files из root-cause}
  Задача: {описание исправления из стратегии}
  PROMPT
  ```
- `skill_update`: вызови skill-creator через CLI (Bash tool):
  ```bash
  source ~/.claude/cli-budgets.env 2>/dev/null || true
  env -u CLAUDECODE claude -p \
    --output-format text \
    --effort max \
    --dangerously-skip-permissions \
    --max-budget-usd "$BUDGET_NORMAL" \
    <<'PROMPT'
  Используй /skill-creator.
  Контекст проблемы: {описание отклонения DEV-N из fix-strategy}
  Скилл для обновления: {имя скилла}
  Задача: {описание исправления из стратегии}
  PROMPT
  ```
  **После каждого CLI-вызова:** проверь exit code. Если ошибка — запиши в JSON и передай управление оркестратору.
- `doc_update`: используй Read/Edit/Write для обновления документации
- `manual_instructions`: сгенерируй пошаговую инструкцию для пользователя

## Запись в файлы внутри `.claude/skills/`

Если `executor` запущен в CLI-подпроцессе (`claude -p`) и применяет правки к файлам
внутри папки `.claude/skills/` (например, баг находится в `SKILL.md` или в агенте
другого скилла), используй штатные шаблоны X.1 (полная перезапись) и X.2 (точечная
замена) из раздела «Запись в `.claude/skills/` из CLI-подпроцессов» корневого
`CLAUDE.md` проекта. Это стандартная процедура для путей такого класса.

После каждой правки обязательно проверяй результат на диске: `grep` по маркеру,
`cat` нужного участка или `python3 -c "open('/abs/path').read()"`.

## Логирование изменений

Для каждого изменённого файла записывай unified diff в changes_made.

## Формат выходного JSON

Запиши результат в файл `{LOG_DIR}/agent-outputs/08-execution.json` (при повторных: `08-execution-iter-{N}.json`):

```json
{
  "agent": "executor",
  "pipeline": "bugfix",
  "called_by": "orchestrator",
  "timestamp": "ISO-8601",
  "status": "success | partial | failed",
  "input": {
    "description": "Исполнение исправлений",
    "files": ["07-fix-strategy.json"]
  },
  "created_files": [],
  "result": {
    "deviation_fixes": [
      {
        "deviation_id": "DEV-1",
        "strategy_used": "direct_fix",
        "changes_made": [
          {
            "file": "путь/к/файлу",
            "action": "modified | created | deleted",
            "description": "Что изменено",
            "lines_changed": 0,
            "unified_diff": "diff..."
          }
        ],
        "adjacent_code_affected": false,
        "additional_tests_created": [],
        "whitebox_results": { "total": 0, "passed": 0, "failed": 0 }
      }
    ],
    "previous_failure": null,
    "notes": null
  },
  "next_agent": "test-runner"
}
```

## При повторной итерации

Файл именуется `08-execution-iter-{N}.json` и включает поле `previous_failure`.

## Обработка ошибок

- Если внешний скилл (skill-creator, script-creator-pipeline) завершился с ошибкой — запиши ошибку в JSON и передай управление оркестратору
- Если файл не удалось прочитать или изменить — запиши ошибку в changes_made с action: "failed" и описанием
- При провале whitebox тестов — запиши результаты и продолжай (оркестратор решит, что делать)
