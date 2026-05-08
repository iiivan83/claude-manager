# Трекер делегированных рекомендаций

Рекомендации из root-cause отчётов, которые требуют отдельного исполнения (запуск скилла, ручная работа, отдельная сессия). Заполняется автоматически скиллом apply-root-cause-fixes.

Статусы: `pending` — ожидает, `done` — выполнено, `cancelled` — отменено (с причиной).

---

## Pending

- **Создать `check-phase-gate.py`** — скрипт упомянут 5 раз в `pipeline-run/SKILL.md`, но не существует. Запустить скилл `create-phase-gate-checker`.
  - Источник: `12-04_21-51_feature-pipeline-systematic-delegation-failure.md` (рекомендация 7)
  - Дата: 2026-04-12
  - Причина делегирования: не связано с корневой причиной текущего инцидента (feature-pipeline delegation failure), это задача для pipeline-run

## Done

(пусто)

## Cancelled

(пусто)
