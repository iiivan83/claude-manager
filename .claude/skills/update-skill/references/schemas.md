# Схемы данных update-skill

Форматы JSON-структур, которыми обмениваются агенты пайплайна. Каждый агент читает входные данные и записывает результат строго по описанным здесь контрактам.

---

## 01-reference-explorer.json

Результат работы `reference-explorer` — карта всех ссылок на целевой скилл.

```json
{
  "target_skill": "имя-скилла",
  "scanned_at": "ISO-8601",
  "references": [
    {
      "file": "путь/к/файлу",
      "type": "call | dependency | mention | self",
      "context": "строка или фрагмент, где найдена ссылка",
      "requires_update": true,
      "reason": "почему этот файл нужно обновить при изменении скилла"
    }
  ],
  "summary": {
    "total_references": 5,
    "files_requiring_update": 2,
    "by_type": {
      "call": 1,
      "dependency": 2,
      "mention": 1,
      "self": 1
    }
  }
}
```

**Поля:**
- **target_skill** — имя скилла, для которого искали ссылки
- **references[].file** — абсолютный путь к файлу со ссылкой
- **references[].type** — тип связи:
  - `call` — файл вызывает этот скилл (через Skill tool или CLI)
  - `dependency` — файл зависит от поведения скилла (например, описывает workflow, в котором скилл участвует)
  - `mention` — файл просто упоминает скилл (документация, комментарий)
  - `self` — скилл ссылается на самого себя
- **references[].requires_update** — нужно ли обновить этот файл при изменении скилла
- **references[].reason** — объяснение, почему обновление нужно (или не нужно)
- **summary** — агрегированная статистика

---

## 01-pattern-explorer.json

Результат работы `pattern-explorer` — собранные конвенции проекта.

```json
{
  "scanned_at": "ISO-8601",
  "skills_analyzed": ["skill-a", "skill-b", "skill-c"],
  "conventions": {
    "frontmatter": {
      "description_style": "подробное описание с триггерными фразами на русском и английском",
      "examples": ["фрагмент 1", "фрагмент 2"]
    },
    "sections": {
      "common_order": ["Global Reference Rules", "Input", "Constants", "Directory Layout", "Logging Format", "Resume Mode", "Pipeline Stages", "Reference Files"],
      "required": ["Global Reference Rules"],
      "optional": ["Constants", "Resume Mode"]
    },
    "tone": {
      "style": "объясняющий, дружелюбный",
      "language": "русский для документации, английский для идентификаторов",
      "examples": ["фрагмент"]
    },
    "error_handling": {
      "pattern": "описание подхода",
      "examples": ["фрагмент"]
    },
    "logging": {
      "pattern": "описание подхода",
      "examples": ["фрагмент"]
    }
  },
  "good_examples": [
    {
      "skill": "имя-скилла",
      "what": "что именно хорошо сделано",
      "fragment": "фрагмент текста"
    }
  ],
  "anti_patterns": [
    {
      "what": "что именно плохо",
      "why": "почему это плохо",
      "fragment": "фрагмент текста"
    }
  ]
}
```

**Поля:**
- **skills_analyzed** — какие скиллы были изучены
- **conventions** — найденные конвенции по категориям
- **good_examples** — конкретные удачные примеры из других скиллов
- **anti_patterns** — примеры того, чего делать не надо

---

## 02-change-planner.json

Результат работы `change-planner` — спецификация изменений.

```json
{
  "target_skill": "имя-скилла",
  "planned_at": "ISO-8601",
  "requested_changes": "описание изменений от пользователя",
  "changes": [
    {
      "section": "название секции или файла",
      "action": "modify | add | remove | rewrite",
      "description": "что именно меняется и почему",
      "before_summary": "краткое описание текущего состояния",
      "after_summary": "краткое описание ожидаемого результата"
    }
  ],
  "expectation_checklist": [
    {
      "id": 1,
      "text": "конкретный, проверяемый критерий",
      "scope": "new_only | both",
      "priority": "critical | important | nice_to_have",
      "tier": "structural | behavioral | result_quality"
    }
  ],
  "ripple_effects": [
    {
      "file": "путь/к/файлу",
      "impact": "описание — что может сломаться",
      "action_needed": "что нужно сделать"
    }
  ],
  "risks": [
    {
      "description": "описание риска",
      "mitigation": "как снизить"
    }
  ]
}
```

**Поля:**
- **changes** — список конкретных изменений с разбивкой по секциям
- **expectation_checklist** — критерии для сравнительного тестирования:
  - `scope: "new_only"` — проверяет только новую версию (новое поведение)
  - `scope: "both"` — должно работать в обеих версиях (регрессионный тест)
  - `priority: "critical"` — провал этого критерия = REJECTED
  - `tier` — уровень assertion по стандарту из `skill-testing-standard.md`: `structural` (файл существует), `behavioral` (процесс корректен), `result_quality` (результат качественный). Опционально, по умолчанию `"result_quality"`
- **ripple_effects** — файлы, которые могут пострадать от изменений
- **risks** — возможные проблемы и способы их избежать

---

## 03-conflict-checker.json

Результат работы `conflict-checker` — найденные конфликты.

```json
{
  "checked_at": "ISO-8601",
  "target_skill": "имя-скилла",
  "skills_checked": ["skill-a", "skill-b", "skill-c"],
  "conflicts": [
    {
      "type": "trigger_overlap | function_duplicate | behavior_conflict | boundary_mismatch",
      "severity": "critical | warning | info",
      "other_skill": "имя-конфликтующего-скилла",
      "description": "подробное описание конфликта",
      "evidence": {
        "target_fragment": "фрагмент из целевого скилла",
        "other_fragment": "фрагмент из другого скилла"
      },
      "suggestion": "как разрешить"
    }
  ],
  "summary": {
    "total_conflicts": 2,
    "critical": 0,
    "warnings": 1,
    "info": 1
  }
}
```

**Поля:**
- **conflicts[].type** — тип конфликта:
  - `trigger_overlap` — два скилла триггерятся на одну фразу
  - `function_duplicate` — два скилла делают одно и то же
  - `behavior_conflict` — workflow одного противоречит другому
  - `boundary_mismatch` — скилл A отсылает к B, но B больше не покрывает эту зону
- **conflicts[].severity** — серьёзность:
  - `critical` — ломает работу, нужно исправить до применения
  - `warning` — может вызвать проблемы, стоит обсудить
  - `info` — потенциальное пересечение, не критично
- **evidence** — конкретные фрагменты из обоих скиллов

---

## 04-update-evaluator.json

Результат работы `update-evaluator` — финальный вердикт.

```json
{
  "evaluated_at": "ISO-8601",
  "target_skill": "имя-скилла",
  "verdict": "ACCEPTED | ACCEPTED_WITH_REGRESSIONS | REJECTED",
  "checklist_results": [
    {
      "id": 1,
      "text": "критерий",
      "scope": "new_only | both",
      "priority": "critical | important | nice_to_have",
      "tier": "structural | behavioral | result_quality",
      "old_skill_passed": null,
      "new_skill_passed": true,
      "evidence": "описание"
    }
  ],
  "regressions": [
    {
      "checklist_id": 3,
      "text": "критерий, который прошёл в старой версии, но провалился в новой",
      "severity": "critical | minor",
      "evidence": "описание"
    }
  ],
  "summary": {
    "checklist_pass_rate": 0.9,
    "critical_passed": true,
    "regressions_count": 1,
    "old_skill_score": 0.7,
    "new_skill_score": 0.9,
    "delta": "+0.2"
  },
  "recommendation": "развёрнутое обоснование вердикта"
}
```

**Поля:**
- **verdict** — итоговое решение:
  - `ACCEPTED` — все критические пункты пройдены, регрессий нет
  - `ACCEPTED_WITH_REGRESSIONS` — чек-лист пройден, но есть мелкие регрессии
  - `REJECTED` — критические пункты не пройдены или серьёзные регрессии
- **checklist_results** — результат по каждому критерию из чек-листа
  - `old_skill_passed: null` — для критериев с `scope: "new_only"` (не проверяется для старой версии)
  - `tier` — уровень assertion: `structural`, `behavioral`, `result_quality`. Опционально, по умолчанию `"result_quality"`
- **regressions** — критерии, которые прошли в старой версии, но провалились в новой
- **recommendation** — развёрнутое объяснение, почему вынесен именно такой вердикт

---

## eval-tasks.json

Тестовые задания для сравнительного тестирования (Этап 4).

```json
{
  "skill_name": "имя-скилла",
  "created_at": "ISO-8601",
  "tasks": [
    {
      "id": 1,
      "prompt": "реалистичный промпт — задача для Claude",
      "description": "что эта задача проверяет",
      "expectations": [
        {
          "text": "конкретный, проверяемый критерий",
          "scope": "new_only | both",
          "tier": "structural | behavioral | result_quality"
        }
      ]
    }
  ]
}
```

**Поля:**
- **tasks[].prompt** — промпт, который получит CLI-агент
- **tasks[].expectations** — критерии проверки, взятые из чек-листа `change-planner`
- **tasks[].expectations[].scope** — `both` = проверять в обеих версиях, `new_only` = только в новой
- **tasks[].expectations[].tier** — уровень assertion: `structural`, `behavioral`, `result_quality`. Опционально, по умолчанию `"result_quality"`

---

## orchestrator-log.json

Журнал оркестратора — запись всех операций пайплайна.

```json
{
  "pipeline": "update-skill",
  "created_at": "ISO-8601",
  "target_skill": "имя-скилла",
  "requested_changes": "описание изменений от пользователя",
  "steps": [
    {
      "step_number": 1,
      "stage": "research",
      "agent": "reference-explorer",
      "start_time": "ISO-8601",
      "end_time": "ISO-8601",
      "success": true,
      "output": "agent-outputs/01-reference-explorer.json",
      "comment": ""
    }
  ]
}
```
