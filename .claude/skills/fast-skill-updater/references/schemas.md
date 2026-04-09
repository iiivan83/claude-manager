# JSON-схемы — контракты данных между агентами

Этот документ определяет формат данных, которыми обмениваются агенты fast-skill-updater.
Каждый агент обязан выдавать чистый JSON (без markdown-обёрток) строго по своей схеме.

---

## analyzer_output

Результат работы анализатора (`agents/analyzer.md`). Передаётся планировщику как входные данные.

```json
{
  "skill_name": "имя скилла из frontmatter",
  "skill_path": "/полный/путь/к/директории/скилла",
  "skill_structure": {
    "files": [
      {
        "path": "SKILL.md",
        "purpose": "что делает этот файл — одним предложением",
        "dependencies": ["agents/analyzer.md", "references/schemas.md"],
        "will_change": true
      }
    ]
  },
  "user_request_summary": "краткое описание запроса пользователя своими словами",
  "impact_analysis": {
    "files_to_modify": ["SKILL.md", "agents/planner.md"],
    "files_to_create": [],
    "files_to_delete": [],
    "ripple_effects": [
      "Если меняем формат выхода analyzer — нужно обновить planner, который его читает"
    ]
  },
  "risks": [
    "Ссылка на agents/old-agent.md останется в SKILL.md после удаления агента"
  ],
  "notes": "дополнительные наблюдения, которые могут быть полезны планировщику"
}
```

**Поля:**
- **skill_name** — имя из frontmatter SKILL.md
- **skill_path** — полный путь к директории
- **skill_structure.files[]** — карта всех файлов скилла
  - `path` — путь относительно корня скилла
  - `purpose` — что делает файл
  - `dependencies` — на какие файлы ссылается
  - `will_change` — затронет ли этот файл обновление
- **user_request_summary** — суть запроса пользователя
- **impact_analysis** — что нужно сделать
  - `files_to_modify` — файлы для изменения
  - `files_to_create` — новые файлы (если нужны)
  - `files_to_delete` — файлы на удаление (если нужно)
  - `ripple_effects` — побочные эффекты, которые нужно учесть
- **risks** — потенциальные проблемы
- **notes** — всё остальное, что может помочь

---

## planner_output

Результат работы планировщика (`agents/planner.md`). Используется оркестратором для применения изменений и верификатором для проверки.

```json
{
  "plan_summary": "Одно предложение — что делает этот план целиком",
  "steps": [
    {
      "order": 1,
      "file": "SKILL.md",
      "action": "modify",
      "description": "Добавить новый этап пайплайна между анализом и реализацией",
      "details": "После секции '### Этап 1 — Анализ' добавить секцию '### Этап 2 — Планирование' с описанием..."
    },
    {
      "order": 2,
      "file": "agents/new-agent.md",
      "action": "create",
      "description": "Создать нового агента для этапа планирования",
      "details": "Файл должен содержать: персону, задачу, алгоритм, формат выхода..."
    }
  ],
  "verification_checklist": [
    "SKILL.md содержит валидный frontmatter с name и description",
    "Новый этап пайплайна ссылается на существующий файл агента",
    "Нумерация этапов последовательна и без пропусков",
    "Формат выхода нового агента совпадает с ожиданиями следующего этапа"
  ]
}
```

**Поля:**
- **plan_summary** — общее описание плана одним предложением
- **steps[]** — упорядоченный список шагов
  - `order` — порядковый номер (выполняются последовательно)
  - `file` — путь к файлу относительно корня скилла
  - `action` — `"modify"` | `"create"` | `"delete"`
  - `description` — что делаем и зачем
  - `details` — конкретные изменения (какие секции, какой текст, что добавить/удалить/заменить)
- **verification_checklist** — что нужно проверить после применения всех шагов

---

## verifier_output

Результат работы верификатора (`agents/verifier.md`). Показывается пользователю как итог.

```json
{
  "status": "passed",
  "plan_steps_verified": [
    {
      "order": 1,
      "status": "ok",
      "note": ""
    },
    {
      "order": 2,
      "status": "warning",
      "note": "Файл создан, но отступы отличаются от остальных агентов"
    }
  ],
  "integrity_checks": {
    "frontmatter_valid": true,
    "file_references_valid": true,
    "agent_consistency": true,
    "scripts_valid": true
  },
  "checklist_results": [
    {
      "item": "SKILL.md содержит валидный frontmatter",
      "status": "passed",
      "note": ""
    }
  ],
  "warnings": [
    "Отступы в agents/new-agent.md используют табы, а остальные агенты — пробелы"
  ],
  "summary": "Все изменения применены корректно, скилл целостен. Одно косметическое предупреждение."
}
```

**Поля:**
- **status** — итоговый вердикт: `"passed"` | `"failed"` | `"passed_with_warnings"`
- **plan_steps_verified[]** — проверка каждого шага плана
  - `order` — номер шага из плана
  - `status` — `"ok"` | `"failed"` | `"warning"`
  - `note` — пояснение (если не ok)
- **integrity_checks** — общие проверки целостности
  - `frontmatter_valid` — frontmatter SKILL.md валиден
  - `file_references_valid` — все ссылки на файлы рабочие
  - `agent_consistency` — форматы данных между агентами согласованы
  - `scripts_valid` — скрипты синтаксически корректны
- **checklist_results[]** — результаты по чеклисту из плана
  - `item` — текст пункта чеклиста
  - `status` — `"passed"` | `"failed"`
  - `note` — пояснение
- **warnings** — предупреждения (не блокируют, но стоит обратить внимание)
- **summary** — итоговое заключение одним-двумя предложениями
