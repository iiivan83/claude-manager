# Сессия 12-04: apply-root-cause-fixes — enforcement делегирования feature-pipeline

## Резюме

Применены 7 из 8 рекомендаций root-cause отчёта `12-04_21-51_feature-pipeline-systematic-delegation-failure.md`. Проблема: feature-pipeline трижды за 3 дня (10-04, 11-04, 12-04) отказывался делегировать «делательные» фазы агентам — оркестратор выполнял работу сам и создавал стабы-заглушки. Корневая причина устранена тройным enforcement.

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `.claude/skills/feature-pipeline/scripts/structural-validator.sh` | изменён | 5 замен `add_non_critical_check` → `add_critical_check` в блоке `check_orchestrator_discipline` (строки 343–356). Теперь нарушение дисциплины блокирует коммит |
| `.claude/skills/feature-pipeline/SKILL.md` | изменён | +блок «Инвариант: делегирование обязательно» перед Pipeline Stages (строка 57); +шаг 5.5 в фазе 10 — вызов structural-validator.sh с `ORCHESTRATOR_LOG_PATH` перед коммитом |
| `.claude/skills/feature-pipeline/agents/feature-finalizer.md` | изменён | +шаг 7.5 — вызов structural-validator.sh перед git commit |
| `~/.claude/references/skills-agents-rules.md` | изменён | +раздел 20 «Дисциплина делегирования оркестратора»: 20.1 оркестратор = координатор (не исполнитель), 20.2 внешний enforcement обязателен (non-critical = отсутствует) |
| `CLAUDE.md` | изменён | +1 строка в шпаргалке разделов: ссылка на раздел 20 skills-agents-rules.md |
| `.claude/skills/apply-root-cause-fixes/SKILL.md` | изменён | +подраздел «Запись в трекер делегированных» в фазе 2 (строка 261); +подраздел «Обновление трекера» в фазе 4 (строка 347) |
| `dev/docs/logs/pending-delegations.md` | создан | Трекер делегированных рекомендаций RCA. 1 запись в Pending: создание check-phase-gate.py (отклонена верификатором как не связанная с корневой причиной) |
| `dev/docs/logs/root-cause-fixes/12.04_22.37-apply-root-cause-fixes-feature-pipeline-systematic-delegation-failure/orchestrator-log.json` | создан | Лог пайплайна: 5 фаз, 8 рекомендаций, 7 принято, 1 отклонено, 7 успешно применено, 0 ошибок |
| `dev/docs/logs/root-cause-reports/realized/` | перемещены 5 файлов | Архивированы все отчёты по проблеме самоисполнения: `12-04_21-51_*`, `12-04_20-37_*`, `12-04_21-21_*`, `12-04_21-22_*`, `11-04_07-42_*` |

## Коммиты

- `e877dcb` — fix(root-cause): enforcement делегирования в feature-pipeline — critical mode, ИНВАРИАНТ, трекер

## Решения

- **Принципы в skills-agents-rules.md, не в CLAUDE.md**. Причина: CLAUDE.md (строка 211) прямо говорит «не дублируй, правь только референс». Верификатор поймал это и предложил корректный путь — раздел 20 в референсе, ссылка в шпаргалке CLAUDE.md.
- **check-phase-gate.py отклонён**. Причина: не связан с корневой причиной (проблема feature-pipeline, а не pipeline-run). Записан в pending-delegations.md как отдельная задача.
- **Рекомендации 2+5 объединены в одного агента**. Причина: обе затрагивают feature-pipeline/SKILL.md — во избежание конфликтов при параллельном редактировании.

## Контекст для следующей сессии

Тройной enforcement против самоисполнения оркестратора установлен:
1. Текстовой — ИНВАРИАНТ в SKILL.md feature-pipeline
2. Архитектурный — раздел 20 в skills-agents-rules.md
3. Внешний блокирующий — structural-validator.sh в critical-режиме, подключён к фазе 10 и финализатору

Трекер `dev/docs/logs/pending-delegations.md` содержит 1 отложенную задачу: создание `check-phase-gate.py` через скилл `create-phase-gate-checker`.

`~/.claude/references/skills-agents-rules.md` изменён (вне git) — раздел 20 действует глобально на все проекты.
