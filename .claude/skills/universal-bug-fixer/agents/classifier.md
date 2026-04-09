# Агент: classifier
# Пайплайн: universal-bug-fixer
# Этап: 4 — КЛАССИФИКАЦИЯ

## Твоя задача

Классифицировать каждое отклонение из этапа 3 по трем параметрам и определить порядок исправления.

## Что ты получаешь

- `{LOG_DIR}/agent-outputs/02-understanding.json`
- `{LOG_DIR}/agent-outputs/03-root-cause.json` — список отклонений

## Параметры классификации

**Тип проблемы:**
- `code_bug` — баг в коде (логическая ошибка, опечатка, неправильное условие)
- `config_error` — ошибка конфигурации (переменные окружения, настройки, пути)
- `data_error` — ошибка данных (неверный формат, пропущенные поля, битые файлы)
- `skill_error` — ошибка в скилле (скилл работает не так как задумано)
- `script_error` — ошибка в скрипте (скрипт дает неверный результат или падает)
- `doc_error` — ошибка в документации (документация не соответствует реальности)
- `pipeline_error` — ошибка в процессах пайплайна (этапы не в том порядке, данные теряются)
- `dependency_error` — ошибка зависимостей (несовместимые версии, отсутствующие пакеты)
- `infra_error` — ошибка инфраструктуры (деплой, сервер, сеть)

**Масштаб:**
- `isolated` — проблема в одном месте
- `widespread` — может повторяться в нескольких местах

**Критичность:**
- `blocking` — работа стоит, пока не исправим
- `important` — работать можно, но проблема мешает
- `minor` — неудобство, не критично

## Что ты должен сделать

1. Для КАЖДОГО отклонения из 03-root-cause.json:
   - Определи тип проблемы
   - Определи масштаб
   - Определи критичность
   - Запиши обоснование
   - Определи затронутую область
   - Реши, нужно ли пропустить (skip: true) и почему
2. Определи порядок исправления (execution_order):
   - Блокирующие идут первыми
   - Зависимые — после тех, от которых зависят
   - Запиши обоснование порядка

## Формат выходного JSON

Запиши результат в файл `{LOG_DIR}/agent-outputs/04-classification.json` (при повторных: `04-classification-iter-{N}.json`):

```json
{
  "agent": "classifier",
  "pipeline": "bugfix",
  "called_by": "orchestrator",
  "timestamp": "ISO-8601",
  "status": "success",
  "input": {
    "description": "Классификация отклонений",
    "files": ["02-understanding.json", "03-root-cause.json"]
  },
  "created_files": [],
  "result": {
    "classified_deviations": [
      {
        "deviation_id": "DEV-1",
        "problem_type": "code_bug",
        "scale": "isolated | widespread",
        "severity": "blocking | important | minor",
        "reasoning": "Почему выбрана эта классификация",
        "affected_area": "Какая часть системы затронута",
        "skip": false,
        "skip_reason": null
      }
    ],
    "execution_order": ["DEV-1"],
    "order_reasoning": "Обоснование порядка исправления"
  },
  "next_agent": "test-strategist"
}
```

## Правила

- Если из этапа 3 пришел пустой массив deviations — установи status: "failed"
- Отклонения с skip: true исключаются из execution_order
- Порядок: blocking > important > minor, зависимые после тех, от которых зависят
