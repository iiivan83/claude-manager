# Сессия 09-04: применение глобального стандарта структуры проекта

## Резюме

Применён глобальный стандарт `create-project-dev-structure` к Claude Manager: миграция `development/` → `dev/`, замена 10 скиллов эталонами из `~/.claude/skill-templates/`, добавление 3 новых, обновление CLAUDE.md. Готовность 13 из 15 задач (≈87%). Две задачи остановлены из-за перегрузки API Anthropic — не обновлены ссылки в 3-6 оставшихся скиллах и не сделана финальная верификация.

## Изменённые файлы

### Создано

| Файл | Что содержит |
|------|--------------|
| `.claude/skills/.backup-2026-04-09/` | Бэкап 10 старых локальных версий скиллов + `AGENTS.md.old`. Страховка перед заменой эталонами. 11 элементов. |
| `dev/docs/brd/` | Новая папка, в ней 3 BRD-файла (перенесены). |
| `dev/docs/adr/` | Новая папка, в ней `project_architecture.md` (перенесён). |
| `dev/docs/specs/realised/` | Новая папка (с британским написанием), 11 реализованных спек модулей. |
| `dev/docs/logs/root-cause-reports/` | Новая папка, 10 root-cause отчётов с подпапками `fix-process/`, `resolved/`. |
| `dev/docs/logs/testing/` | Новая папка, 8 файлов (планы и результаты E2E). |
| `dev/docs/session-reports/` | Уже мигрированная из `development/`, 5 подпапок по датам (28-03, 29-03, 30-03, 03-04, 06-04), 38 отчётов. |
| `dev/docs/changelog/` | Новая пустая папка. |
| `dev/docs/claude-md-updates/` | Новая пустая папка. |
| `dev/pipeline-workspace/` | Новая пустая папка. |
| `dev/docs/docs-index.md` | Живой индекс документации, 12171 байт. Группы: BRD, ADR, справочники, спеки, session-reports, logs, changelog, claude-md-updates. Упоминает реальные файлы. |
| `dev/docs/skill-index.md` | Живой индекс скиллов, 7562 байт. 33 скилла в 9 группах: эталонные пайплайны (8), анализ (3), работа с документами (2), разработка модулей (4), тестирование (5), пайплайны проекта (4), документация (3), генерация скриптов (2), рефакторинг (2). |
| `.claude/skills/fast-skill-updater/` | Новый эталонный скилл (быстрое обновление скиллов за 1 цикл). |
| `.claude/skills/feature-pipeline/` | Новый эталонный скилл (полный цикл доработки функционала). |
| `.claude/skills/universal-bug-fixer/` | Новый эталонный скилл (пайплайн исправления ошибок с двойной верификацией). |
| `dev/temp/old-docs-index.md` | Архивная копия старого `development/docs/docs-index.md`. |

### Изменено

| Файл | Что изменилось |
|------|----------------|
| `.gitignore` | Добавлен блок: `dev/`, `.claude/`, `!.claude/skills/`, `!.claude/skills/**`, `.DS_Store`. Умный паттерн с исключением для `.claude/skills/` — чтобы кастомные скиллы проекта сохранялись в git, а служебные настройки Claude Code игнорировались. |
| `.claude/skills/AGENTS.md` | Заменён на эталонную версию из команды `create-project-dev-structure.md` (шаг 3). 71 строка. Добавлены секции: `evals/` в структуре скилла, «Стиль письма», «Шаблоны документов», «Именование документов», маршруты для ADR/changelog/BRD/claude-md-updates. Лимит SKILL.md поднят с 500 до 1000 строк. |
| `CLAUDE.md` | Сохранён весь кастомный контент про Claude Manager. Добавлены 8 секций из эталона: «Глобальные референсы», «Скиллы», «Принципы разработки скиллов и пайплайнов», «Принцип enforcement-first», «CLI-архитектура пайплайнов», «Единый контракт данных», «Валидация скиллов», «Пропорциональность тестирования». Блок «Структура проекта» переписан с `development/` на `dev/` с новыми подпапками. Все ссылки в «Технической документации» и «Важных деталях» обновлены на новые пути. Итог: 255 строк (было 167, +88). |
| `.claude/skills/apply-root-cause-fixes/` | Удалена локальная версия, скопирован эталон из `~/.claude/skill-templates/`. |
| `.claude/skills/create-doc/` | Заменён на эталон. |
| `.claude/skills/pipeline-designer/` | Заменён на эталон. |
| `.claude/skills/pipeline-explorer/` | Заменён на эталон. |
| `.claude/skills/pipeline-implementer/` | Заменён на эталон. |
| `.claude/skills/root-cause-analysis/` | Заменён на эталон. |
| `.claude/skills/script-creator-pipeline/` | Заменён на эталон. |
| `.claude/skills/skill-creator/` | Заменён на эталон. |
| `.claude/skills/update-docs/` | Заменён на эталон. |
| `.claude/skills/update-skill/` | Заменён на эталон (локальный был 120 строк, эталон 456 — сильно обновлённая версия). |
| `.claude/skills/implement-module/SKILL.md` | Частично обновлены ссылки (description уже светит `dev/docs/specs/`), но тело файла ещё может содержать остатки `development/` — требует проверки. |

### Перенесено (git mv, сохранена история)

| Источник | Назначение | Файлов |
|----------|------------|--------|
| `development/docs/session-reports/` | `dev/docs/session-reports/` | 38 (5 подпапок DD-MM) |
| `development/docs/root-cause-reports/` | `dev/docs/logs/root-cause-reports/` | 10 |
| `development/docs/testing/` | `dev/docs/logs/testing/` | 8 |
| `development/specs/realized/` | `dev/docs/specs/realised/` (с переименованием) | 11 |
| `development/docs/brd-user-journeys.md` | `dev/docs/brd/brd-user-journeys.md` | 1 |
| `development/docs/brd-user-journeys.BEFORE.md` | `dev/docs/brd/brd-user-journeys.BEFORE.md` | 1 (untracked, простой mv) |
| `development/docs/brd-validation-report_29-03-23-47.md` | `dev/docs/brd/` | 1 |
| `development/docs/claude-cli-stream-json-protocol.md` | `dev/docs/` | 1 |
| `development/docs/deployment-guide.md` | `dev/docs/` | 1 |
| `development/docs/review-checklists.md` | `dev/docs/` | 1 |
| `development/docs/project_architecture.md` | `dev/docs/adr/project_architecture.md` | 1 (untracked, простой mv) |
| `development/docs/review-report_30-03.md` | `dev/docs/session-reports/30-03/review-report.md` | 1 |
| `development/docs/docs-index.md` | `dev/temp/old-docs-index.md` (архив) | 1 |
| `development/specs/module-dependency-graph.md` | `dev/docs/specs/` | 1 |
| `development/specs/pipeline-spec.md` | `dev/docs/specs/` | 1 |
| `development/script-specs/autofix-e2e-skill-prompt.md` | `dev/docs/specs/` | 1 (untracked, простой mv) |
| `development/temp-docs/prompt-for-spec-pipeline-skill.md` | `dev/temp/` | 1 |

Итого перенесено: 59 файлов (56 через `git mv` с историей, 3 простым `mv`).

### Удалено

- `development/` — папка целиком со всеми подпапками (включая `script-specs/realized/`, которая была пустой).

## Выполненные команды

- `mkdir -p .claude/skills/.backup-2026-04-09` — папка под бэкап.
- `cp .claude/skills/AGENTS.md .claude/skills/.backup-2026-04-09/AGENTS.md.old` — сохранить старую версию правил перед заменой.
- Цикл `cp -R .claude/skills/$skill .claude/skills/.backup-2026-04-09/` для 10 скиллов — бэкап локальных версий.
- `mkdir -p dev/docs/{session-reports,specs/realised,logs,adr,changelog,brd,claude-md-updates} dev/pipeline-workspace` — создание стандартной структуры.
- `rmdir dev/docs/session-reports dev/docs/specs/realised` — удаление пустых целевых папок перед `git mv` целыми каталогами.
- Серия `git mv` для переноса 56 tracked файлов из `development/` в `dev/` (через агента A).
- Серия `mv` для 3 untracked файлов (`brd-user-journeys.BEFORE.md`, `project_architecture.md`, `autofix-e2e-skill-prompt.md`).
- `find development -type d -empty -delete && rmdir development` — удаление опустевшей старой структуры.
- Цикл `rm -rf .claude/skills/$skill && cp -R ~/.claude/skill-templates/$skill .claude/skills/$skill` для 10 дублирующихся скиллов (через агента B).
- `cp -R ~/.claude/skill-templates/{fast-skill-updater,feature-pipeline,universal-bug-fixer} .claude/skills/` — добавление 3 новых эталонных скиллов.

## Решения

- **Полная миграция `development/` → `dev/`, а не сосуществование**. Причина: пользователь выбрал рекомендованный вариант через `AskUserQuestion`. Сосуществование двух папок для документации — плохая практика, ведёт к путанице. Цена чистоты — переименование и обновление всех ссылок.

- **Замена всех 10 дублирующихся скиллов эталонами, с бэкапом**. Причина: пользователь выбрал этот вариант. Все локальные версии отличались от эталонов (update-skill 120 строк vs эталон 456 — почти в 4 раза). Бэкап в `.claude/skills/.backup-2026-04-09/` даёт возможность восстановить локальную кастомизацию если понадобится.

- **Оставить все 20 проектно-специфичных скиллов**. Причина: это уникальные скиллы Claude Manager (autofix-e2e, implement-module, pipeline-run, test-e2e, validate-brd и т.д.), не имеющие аналогов в эталоне. Пайплайн разработки бота без них сломается.

- **Объединить CLAUDE.md, а не заменить на эталон**. Причина: кастомный CLAUDE.md содержит критическую информацию о боте (архитектура, модули, протокол stream-json). Эталон — общий шаблон с плейсхолдерами. Объединение даёт и специфику, и общие секции стандарта (enforcement-first, CLI-архитектура, валидация).

- **Умный паттерн в `.gitignore`: игнорировать `.claude/`, но исключить `!.claude/skills/`**. Причина: эталонный `.gitignore` из команды предписывал просто `.claude/`, но в проекте разрабатываются 20 кастомных скиллов, которые должны быть в git. Паттерн `!.claude/skills/` + `!.claude/skills/**` сохраняет их версионирование, а остальные служебные файлы Claude Code (settings, кэш, worktrees) остаются игнорируемыми.

- **Использование `git mv` вместо `mv`** для переноса 56 файлов. Причина: сохранение истории в git blame. Для 38 сессионных отчётов и 11 реализованных спек это критично — иначе теряется возможность отследить когда и почему вносились изменения.

- **Делегирование работы агентам через Agent tool**. Причина: пользователь явно попросил «используй агентов, задача очень трудоёмкая» и «сам не делай». Защита основного контекста от сотен tool_use вызовов. Запуск волнами: сначала A (миграция) и B (замена скиллов) параллельно, потом C (ссылки), D (индексы), E (CLAUDE.md) параллельно. Финальная верификация — в основном контексте.

- **Карта замен `development/` → `dev/` от специфичных к общим**. Причина: простая замена `development/` на `dev/` не сработает — внутренняя структура изменилась. Например, `development/docs/brd-user-journeys.md` стало `dev/docs/brd/brd-user-journeys.md` (добавлена папка `brd/`), а `development/specs/config_spec.md` стало `dev/docs/specs/realised/config_spec.md` (добавлены `docs/` и `realised/`). Нужна упорядоченная карта, иначе общая замена ломает специфичные пути.

## Проблемы и решения

- **Проблема**: команда `create-project-dev-structure` использует `cp -Rn` (не перезаписывает), что не подходит для замены 10 скиллов. **Решение**: написал кастомный скрипт с `rm -rf && cp -R` для каждого из 10 конкретных скиллов, перед этим — бэкап в `.backup-2026-04-09/`.

- **Проблема**: пустые целевые папки `dev/docs/session-reports/` и `dev/docs/specs/realised/` мешали `git mv` целыми каталогами. **Решение**: `rmdir` этих папок перед `git mv` (безопасная операция, `rmdir` падает если папка не пуста).

- **Проблема**: 3 файла (`brd-user-journeys.BEFORE.md`, `project_architecture.md`, `autofix-e2e-skill-prompt.md`) были untracked — `git mv` не сработал с ошибкой `fatal: not under version control`. **Решение**: использован обычный `mv` для этих файлов. История в git для них и так отсутствовала, так что потери нет.

- **Проблема**: первый агент C (обновление ссылок) упал с `API Error: 529 Overloaded` после 73 tool_use и 275 секунд работы. **Решение**: проведена диагностика через Grep и bash grep — выяснилось, что агент успел обновить большую часть файлов, но не все.

- **Проблема**: `Grep` tool через `output_mode="files_with_matches"` с паттерном `development/` вернул «No files found», хотя bash `grep -r` нашёл 20+ совпадений. **Причина**: новый `.gitignore` игнорирует `.claude/` с исключением `!.claude/skills/`, и ripgrep под капотом Grep tool не заходит в эту папку несмотря на исключение (видимо, ограничение двухуровневой логики «игнорировать + исключить подпапку»). **Решение**: для верификации использовать bash `grep -rn "development/"` с явным `--exclude-dir` вместо Grep tool. **Важно для будущих сессий**: после любого изменения `.gitignore` с исключениями — перепроверять результаты Grep tool через альтернативный путь.

- **Проблема**: при попытке перезапустить агента-зачистителя на оставшиеся 3-6 файлов API опять вернул `529 Overloaded` — уже на первом tool_use (10 секунд). **Попытка №2** через 1-2 минуты — снова `529` на 7-м tool_use. **Решение**: остановлен по просьбе пользователя, сформирован отчёт о состоянии. Работу продолжит следующая сессия.

- **Проблема**: линтер/пользователь слегка подправил CLAUDE.md после агента E (уточнил структуру блока `dev/`). **Решение**: не откатывать — это осознанное улучшение. Учтено в отчёте.

## Незавершённое

- [ ] **Task #8: Обновление ссылок `development/` → `dev/` в оставшихся файлах скиллов.**
  Известные проблемные файлы (подтверждено diagnostic grep и system-reminder с устаревшими описаниями):
  - `.claude/skills/make-script-spec/SKILL.md` — 11 мест: `development/docs/docs-index.md`, `development/docs/target-folder-structure.md`, `development/docs/session-reports/DD-MM/`, `development/docs/root-cause-reports/`, `development/script-specs/`, `development/temp-docs/`, `development/docs/`, `/development/script-specs`.
  - `.claude/skills/create-test-runner/SKILL.md` — ~8 мест: `development/scripts/run-all-tests.sh`, `./development/scripts/run-all-tests.sh`, `development/scripts/` — и в description, и в коде генерируемого скрипта.
  - `.claude/skills/create-phase-gate-checker/SKILL.md` — description ещё светит `development/scripts/check-phase-gate.py` (судя по system-reminder).
  - `.claude/skills/run-user-testing/SKILL.md` — description светит `development/docs/testing/user-test-scenarios_*.md`.
  - `.claude/skills/session-report/SKILL.md` — description светит `development/docs/session-reports/`. Плюс в SKILL.md прямо написано «Папка для сохранения: `development/docs/session-reports/DD-MM/`» — **очень важный баг**, потому что из-за этого скилла этот отчёт мог бы сохраниться не туда. Сейчас сработало только потому что в аргументах явно передан правильный путь.
  - `.claude/skills/implement-module/SKILL.md` — description уже `dev/docs/specs/`, но тело файла может содержать остатки. Требует проверки.
  - Возможны другие файлы — нужен полный `grep -rln "development/"` с полным выводом (без head -20 который был в диагностике).

- [ ] **Task #15: Финальная верификация.**
  Остаётся сделать:
  - Полный `grep -rn "development/"` с корректными `--exclude-dir` — должен вернуть 0 строк (кроме `.backup-2026-04-09/`, `brd-user-journeys.BEFORE.md`, `old-docs-index.md`, `session-reports/`, `root-cause-reports/`).
  - Запуск тестов `python -m pytest tests/ -v` — проверить что миграция не сломала тесты (некоторые тесты могли ссылаться на пути `development/`).
  - Перезапуск бота согласно правилу памяти `feedback_restart_bot` — убедиться что `watch_and_restart.sh` или LaunchAgent подхватит изменения.
  - Проверка работы ключевых скиллов после обновления ссылок (тест-запуск `session-report`, `implement-module`, `create-test-runner`).

## Контекст для следующей сессии

### Общее состояние проекта

- Папка `development/` полностью удалена.
- Вся структура `dev/` на месте (22 подпапки), заполнена файлами.
- Ровно 33 скилла в `.claude/skills/`: 20 проектно-специфичных (Claude Manager) + 13 эталонных (глобальный стандарт).
- Бэкап старых скиллов — `.claude/skills/.backup-2026-04-09/` (11 элементов, 10 скиллов + `AGENTS.md.old`).
- `CLAUDE.md` в корне — 255 строк, сохранён кастомный контент + 8 секций стандарта.
- `.gitignore` — обновлён с умным паттерном для `.claude/skills/`.
- Индексы `dev/docs/docs-index.md` и `dev/docs/skill-index.md` — созданы, отражают реальное содержимое.
- `AGENTS.md` в `.claude/skills/` — эталонная версия (71 строка).

### Ключевые решения пользователя в этой сессии

Через инструмент `AskUserQuestion` пользователь выбрал 4 варианта:

1. **Папка `development/`** → полная миграция в `dev/` по новой структуре с обновлением всех ссылок.
2. **10 дублирующихся скиллов** → заменить все эталонами, локальные — в бэкап.
3. **20 проектно-специфичных скиллов** → оставить все.
4. **`CLAUDE.md`** → объединить с эталоном (сохранить кастомное + добавить секции стандарта).

Эти решения определяют все последующие действия. Если в будущих сессиях возникнут сомнения — помни что пользователь согласился на максимально чистый (но трудоёмкий) вариант.

### Что делать дальше

**Приоритет 1 — завершить Task 8:**

1. Дождаться когда API Anthropic восстановится (попытка 529 была в ~19-10, восстановление обычно 2-5 минут).
2. Запустить агента на узкую задачу с конкретным списком файлов. Задача должна быть короткой (меньше 20 tool_use), чтобы не нарваться на повторный overload.
3. Карта замен (от специфичных к общим, применять в этом порядке):
   ```
   development/docs/docs-index.md           → dev/docs/docs-index.md
   development/docs/brd-user-journeys       → dev/docs/brd/brd-user-journeys
   development/docs/claude-cli-stream-json-protocol → dev/docs/claude-cli-stream-json-protocol
   development/docs/deployment-guide        → dev/docs/deployment-guide
   development/docs/project_architecture    → dev/docs/adr/project_architecture
   development/docs/review-checklists       → dev/docs/review-checklists
   development/docs/session-reports/        → dev/docs/session-reports/
   development/docs/root-cause-reports/     → dev/docs/logs/root-cause-reports/
   development/docs/testing/                → dev/docs/logs/testing/
   development/specs/realized/              → dev/docs/specs/realised/
   development/specs/module-dependency-graph → dev/docs/specs/module-dependency-graph
   development/specs/pipeline-spec          → dev/docs/specs/pipeline-spec
   development/specs/                        → dev/docs/specs/
   development/script-specs/                → dev/docs/specs/
   development/scripts/                      → dev/scripts/
   development/temp-docs/                    → dev/temp/
   development/docs/                         → dev/docs/
   development/                              → dev/
   ```
4. Полный поиск оставшихся файлов:
   ```bash
   cd /Users/ivan/Desktop/claude-sandbox/claude_manager
   grep -rln "development/" \
     --include="*.md" --include="*.py" --include="*.sh" --include="*.json" --include="*.toml" --include="*.txt" \
     --exclude-dir=".git" --exclude-dir=".venv" --exclude-dir="venv" --exclude-dir="__pycache__" \
     --exclude-dir=".backup-2026-04-09" --exclude-dir="session-reports" --exclude-dir="root-cause-reports" \
     --exclude-dir="dist" --exclude-dir="build" \
     --exclude="old-docs-index.md" --exclude="brd-user-journeys.BEFORE.md"
   ```
5. **НЕ использовать `Grep` tool с `output_mode="files_with_matches"`** для проверки — он ложно возвращает «No files found» из-за взаимодействия с новым `.gitignore`. Использовать bash `grep -r`.

**Приоритет 2 — починить сам скилл `session-report`:**

В `.claude/skills/session-report/SKILL.md` прямо в тексте инструкций написано:

> Папка для сохранения: `development/docs/session-reports/DD-MM/`

Этот скилл сейчас сохранил бы отчёт в несуществующую папку, если бы я не передал в args подсказку про правильный путь. **Это критический баг**, и скилл надо обновить в первую очередь. Замена: `dev/docs/session-reports/DD-MM/`.

**Приоритет 3 — Task 15 финальная верификация:**

1. После Task 8 — полный grep, должно быть 0 вхождений в целевых областях.
2. `python -m pytest tests/ -v` — полный прогон тестов. Если какие-то тесты упадут из-за изменения путей — точечно починить.
3. Перезапустить бота: `launchctl unload ~/Library/LaunchAgents/com.ivan.claude-manager.plist && launchctl load ~/Library/LaunchAgents/com.ivan.claude-manager.plist` или через `watch_and_restart.sh`.
4. Протестировать ключевые скиллы вручную, хотя бы `session-report` и `create-doc` — убедиться что они знают новые пути.

### Важные технические нюансы

- **Grep tool ложно не находит совпадения в `.claude/skills/`** из-за обновлённого `.gitignore`. Всегда использовать bash `grep -r` для верификации.
- **`.claude/skills/` отслеживается в git** благодаря умному паттерну `!.claude/skills/` в `.gitignore`, но сама папка `.claude/` — нет. Если в будущем понадобится закоммитить что-то кроме скиллов в `.claude/`, нужно ещё одно исключение.
- **История файлов в `dev/` сохранена** через `git mv` для 56 из 59 перенесённых файлов. 3 файла были untracked (перенесены простым `mv`) — их истории в git и так нет.
- **Эталонные скиллы часто обновляются** в `~/.claude/skill-templates/`. Для синхронизации можно использовать новый скилл `update-skill` (теперь эталонная версия 456 строк, поддерживает сравнительное тестирование old vs new).
- **API overload 529** — известная проблема при долгих цепочках tool_use в агентах. Снижает вероятность: запуск агентов в background через `run_in_background: true`, и деление больших задач на узкие подзадачи (не более 20 tool_use каждая).

### Структура текущих задач

- ✅ Task #1-7, #9-14: выполнены (13 задач)
- 🔶 Task #8 (обновление ссылок): **in_progress**, пометить в начале следующей сессии как продолжение
- 🔶 Task #15 (финальная верификация): **in_progress**, ждёт завершения Task #8

### Что нельзя терять

- Бэкап `.claude/skills/.backup-2026-04-09/` — содержит локальные кастомизации старых скиллов. Пока работа не завершена и всё не проверено, **не удалять**. После успешной верификации можно решать — оставить как архив или убрать.
- Все сессионные отчёты в `dev/docs/session-reports/` — историческая память проекта, не переписывать. В них много информации о пройденных этапах.
- `dev/temp/old-docs-index.md` — архив старого индекса, может понадобиться для сравнения.
- `dev/docs/brd/brd-user-journeys.BEFORE.md` — архив старой версии BRD.
