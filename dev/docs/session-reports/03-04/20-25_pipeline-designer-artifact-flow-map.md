# Сессия 03-04: добавление Artifact Flow Map в pipeline-designer

## Резюме

Добавлена 9-я обязательная секция "Artifact Flow Map" в скилл pipeline-designer. Секция требует от каждого будущего пайплайна полную карту движения документов — структуру хранения, реестр артефактов, цепочки трансформаций и спецификации форматов данных.

## Изменённые файлы

- `.claude/skills/pipeline-designer/agents/drafter.md` — изменён — добавлена Section 9 "Artifact Flow Map" с 4 блоками (Directory Structure, Artifact Registry, Transformation Chains, Data Format Specifications); заголовок "exactly 8" → "exactly 9"; в JSON-вывод добавлена запись `Artifact Flow Map` в `created_sections`
- `.claude/skills/pipeline-designer/agents/interviewer.md` — изменён — добавлен новый пункт в "What You Need to Find Out": **Artifacts** — вопрос про документы и файлы, создаваемые пайплайном (промежуточные и финальные)
- `.claude/skills/pipeline-designer/agents/completeness-verifier.md` — изменён — добавлен Requirement #13 "Artifact flow map complete" (проверяет полноту реестра артефактов, цепочек трансформаций и соответствие directory structure); максимум баллов обновлён N/24 → N/26
- `.claude/skills/pipeline-designer/scripts/structural-validator.sh` — изменён — добавлена функция `check_artifact_flow_map()` (проверка #13) ищет ключевые слова artifact flow/map/registry на английском и русском; TOTAL_CHECKS=12 → 13; добавлен `run_check "artifact-flow-map"` в список проверок
- `.claude/skills/pipeline-designer/SKILL.md` — изменён — "checks 12 mandatory elements" → "checks 13 mandatory elements"

## Решения

- **Решение**: добавить отдельный раздел в спецификацию пайплайна, а не расширять существующие. **Причина**: данные о движении документов были размазаны по Pipeline Schema (раздел 1), Stage Descriptions (раздел 4) и Logging Description (раздел 7) — единая карта в одном месте даёт глобальное видение без необходимости собирать информацию по кусочкам.
- **Решение**: структура Section 9 зеркалит то, как сам pipeline-designer описывает свои документы (Directory Layout, schemas.json, Input/Output). **Причина**: пользователь явно запросил "точно такой же как сам pipeline-designer".
- **Решение**: обновить structural-validator.sh и SKILL.md в дополнение к 3 агентам. **Причина**: без обновления валидатора новая секция не проверялась бы автоматически — замкнутость цикла (спрашиваем → пишем → проверяем) была бы нарушена.

## Контекст для следующей сессии

Pipeline-designer теперь требует 9 секций (было 8) и 13 структурных проверок (было 12). Все 5 файлов скилла обновлены консистентно. Скилл ещё не тестировался на реальном пайплайне после этих изменений — при первом запуске стоит проверить, что драфтер генерирует Section 9, а structural-validator корректно находит/не находит ключевые слова.
