# Спецификация пайплайна: code-quality-checker

Пайплайн для анализа качества кода. Запускает несколько проверок (статический анализ, поиск дублей, анализ сложности), собирает результаты, и если находит критические проблемы — запускает цикл автоматической починки. Максимум 3 попытки, потом решает пользователь.

## Общие сведения

- **Название пайплайна:** code-quality-checker
- **Назначение:** автоматическая проверка качества кода с попыткой автоисправления — не просто «вот список проблем», а «вот проблемы, и вот что мы уже починили»
- **Входные данные:** путь к директории с кодом, язык программирования
- **Выходные данные:** JSON-отчёт с найденными проблемами, исправлениями и итоговым статусом

## Этапы

### Этап 0 — Подготовка (скрипт)

- **Тип:** скрипт (bash)
- **Что делает:** создаёт рабочие папки, сканирует директорию (список файлов, язык, размер), создаёт бэкап кода для безопасного исправления
- **Вход:** путь к директории с кодом, язык
- **Выход:** подготовленная структура, file-list.json с перечнем файлов для анализа
- **Зависимости:** нет

### Этап 1 — Статический анализ (агент)

- **Тип:** агент
- **Что делает:** ищет типичные ошибки и антипаттерны — неиспользуемые переменные, мёртвый код, проблемы с импортами, потенциальные баги (деление на ноль, null reference). Для каждой проблемы указывает файл, строку, серьёзность (critical/warning/info)
- **Вход:** file-list.json, путь к коду
- **Выход:** agent-outputs/01-static-analysis.json
- **Зависимости:** Этап 0

### Этап 2 — Детектор дублей (агент)

- **Тип:** агент
- **Что делает:** ищет дублированный код — точные и приблизительные совпадения (одна и та же логика с немного разными именами переменных). Для каждого дубля указывает оба местоположения и предлагает рефакторинг (вынести в функцию, создать утилиту)
- **Вход:** file-list.json, путь к коду
- **Выход:** agent-outputs/02-duplicates.json
- **Зависимости:** Этап 0

### Этап 3 — Анализ сложности (агент)

- **Тип:** агент
- **Что делает:** считает метрики сложности для каждой функции — количество строк, глубина вложенности, цикломатическая сложность (количество ветвлений). Помечает функции выше пороговых значений (> 30 строк, > 3 уровня вложенности, цикломатическая > 10)
- **Вход:** file-list.json, путь к коду
- **Выход:** agent-outputs/03-complexity.json
- **Зависимости:** Этап 0

### Этап 4 — Сводка (агент)

- **Тип:** агент
- **Что делает:** собирает результаты трёх анализов, ранжирует проблемы по серьёзности, определяет есть ли критические (серьёзность = critical и количество > 0). Если критические есть — устанавливает флаг needs_fixing = true
- **Вход:** agent-outputs/01-static-analysis.json, agent-outputs/02-duplicates.json, agent-outputs/03-complexity.json
- **Выход:** agent-outputs/04-summary.json с флагом needs_fixing и списком проблем
- **Зависимости:** Этапы 1, 2, 3

### Этап 5 — Цикл починки (агент)

- **Тип:** агент (условный этап)
- **Условие запуска:** только если needs_fixing = true в 04-summary.json
- **Что делает:** берёт критические проблемы из сводки и пытается исправить — минимальные, точечные изменения. После каждого исправления перезапускает проверку для затронутых файлов. Ведёт лог попыток: что менял, помогло или нет
- **Вход:** agent-outputs/04-summary.json, путь к коду (бэкап из Этапа 0)
- **Выход:** agent-outputs/05-fix-log.json, обновлённые файлы кода
- **Зависимости:** Этап 4
- **Ограничение:** максимум 3 попытки. После 3-й неудачной — три варианта:
  - Пользователь даёт подсказку → ещё одна попытка
  - Пропустить нерешённые → перейти к отчёту
  - Остановить пайплайн → создать PIPELINE-INCOMPLETE.md

### Этап 6 — Финальный отчёт (агент)

- **Тип:** агент
- **Что делает:** собирает все результаты (исходные проблемы + что починено + что осталось), формирует итоговый JSON-отчёт и человекочитаемый markdown-отчёт
- **Вход:** все agent-outputs/ + текущее состояние кода
- **Выход:** output/report.json, output/report.md
- **Зависимости:** Этап 5 (или Этап 4, если needs_fixing = false)

## Карта зависимостей

```
Этап 0 (подготовка)
  ├── Этап 1 (стат. анализ)    ─┐
  ├── Этап 2 (детектор дублей)  ─┼── Этап 4 (сводка)
  └── Этап 3 (анализ сложности) ─┘       │
                                          ├── [needs_fixing = true] → Этап 5 (починка, макс. 3 попытки)
                                          │                                    │
                                          │                          [всё ещё critical] → обратно к Этапу 5
                                          │                          [починено или 3 попытки] ─┐
                                          │                                                     │
                                          └── [needs_fixing = false] ──────────────────────────── Этап 6 (отчёт)
```

## Обязательная последовательность переходов

- Этап 0 → Этапы 1, 2, 3 (параллельно)
- Этапы 1, 2, 3 → Этап 4
- Этап 4 (needs_fixing = true) → Этап 5
- Этап 5 (проблемы остались, попытки < 3) → Этап 5 (повтор)
- Этап 5 (починено или попытки >= 3) → Этап 6
- Этап 4 (needs_fixing = false) → Этап 6

**Запрещённые переходы:**
- Этап 4 → Этап 6 при needs_fixing = true (нельзя пропустить починку)
- Этап 5 → Этап 6 без перепроверки (после починки нужно заново оценить)

## Схемы данных

### 01-static-analysis.json
```json
{
  "issues": [
    {
      "file": "string",
      "line": "integer",
      "type": "string — unused_variable | dead_code | bad_import | potential_bug",
      "severity": "string — critical | warning | info",
      "message": "string",
      "suggestion": "string"
    }
  ],
  "stats": {
    "critical_count": "integer",
    "warning_count": "integer",
    "info_count": "integer"
  }
}
```

### 02-duplicates.json
```json
{
  "duplicates": [
    {
      "location_a": {"file": "string", "lines": "string — e.g. 10-25"},
      "location_b": {"file": "string", "lines": "string"},
      "similarity": "number — 0.0-1.0",
      "suggested_refactoring": "string"
    }
  ],
  "total_duplicate_lines": "integer"
}
```

### 03-complexity.json
```json
{
  "functions": [
    {
      "file": "string",
      "name": "string",
      "lines": "integer",
      "max_nesting": "integer",
      "cyclomatic_complexity": "integer",
      "exceeds_thresholds": "boolean"
    }
  ],
  "over_threshold_count": "integer"
}
```

### 04-summary.json
```json
{
  "needs_fixing": "boolean",
  "total_issues": "integer",
  "critical_issues": [
    {
      "source": "string — static_analysis | duplicates | complexity",
      "description": "string",
      "severity": "critical",
      "file": "string",
      "fix_priority": "integer — 1 = самый важный"
    }
  ],
  "non_critical_issues_count": "integer"
}
```

### 05-fix-log.json
```json
{
  "attempts": [
    {
      "attempt_number": "integer — 1, 2 или 3",
      "issues_targeted": ["string — описание проблемы"],
      "changes_made": [{"file": "string", "description": "string"}],
      "recheck_result": "string — fixed | still_failing | new_issues",
      "remaining_critical": "integer"
    }
  ],
  "final_status": "string — all_fixed | partially_fixed | gave_up"
}
```

### report.json
```json
{
  "source_directory": "string",
  "language": "string",
  "generated_at": "ISO-8601",
  "original_issues": {
    "static_analysis": "integer",
    "duplicates": "integer",
    "complexity": "integer"
  },
  "fixes_applied": "integer",
  "remaining_issues": "integer",
  "fix_attempts": "integer",
  "details": {
    "static_analysis": "object",
    "duplicates": "object",
    "complexity": "object"
  },
  "overall_quality_score": "number — 0.0-1.0"
}
```
