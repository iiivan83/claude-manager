# Сессия 06-04: Универсализация скилла root-cause-analysis

## Резюме

Скилл `root-cause-analysis` переделан из проектоспецифичного (привязан к Claude Manager) в универсальный — работает в любом проекте. Убраны все захардкоженные пути, имена скиллов и "больные точки проекта", добавлена Фаза 0 (разведка проекта) и ссылки на глобальные референсы `~/.claude/references/`.

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `.claude/skills/root-cause-analysis/SKILL.md` | изменён | Полная переработка: 14 изменений по утверждённому плану (см. детали ниже) |

### Детали изменений в SKILL.md

**Добавлено:**
- Фаза 0 "Разведка проекта" (6 шагов): чтение CLAUDE.md, глобальных референсов `~/.claude/references/`, исследование структуры, обнаружение скиллов через glob, поиск индексов документации, запись карты проекта
- Поле `**Проект:**` в шаблоне отчёта
- 2 новых правила: "Фаза 0 не пропускается", "Глобальные стандарты — по ссылке"

**Убрано:**
- 15+ захардкоженных путей: `development/docs/session-reports/`, `development/docs/docs-index.md`, `development/docs/brd-user-journeys.md`, `development/docs/deployment-guide.md`, `development/docs/review-report_*.md`, `development/docs/review-checklists.md`, `development/docs/brd-validation-report_*.md`, `development/docs/testing/`, `development/docs/root-cause-reports`, `development/specs/`, `development/specs/realized/`, `src/claude_manager/`, `pipeline-state.json`, `watch_and_restart.sh`, `~/Library/LaunchAgents/com.ivan.claude-manager.plist`
- 16 захардкоженных имён скиллов: `implement-module`, `spec-module`, `brd-decompose`, `validate-brd`, `review-code`, `test-integration`, `test-e2e`, `create-user-test-scenarios`, `run-user-testing`, `create-doc`, `update-docs`, `update-project-docs`, `session-report`, `project-setup`, `update-skill`, `pipeline-run`
- Секция 4.5 "Больные точки проекта" (20 строк про watcher/handler, session_id, stream-json, message_splitter, fcntl.flock)
- Привязка "в проекте Claude Manager" из description
- Привязка стиля "на ты" — заменена на "адаптированным к стилю из CLAUDE.md"
- Проектоспецифичные примеры из секции 7.1.1 (`development/specs/{module}_spec.md`, `implement-module`)

**Заменено на динамику:**
- Все пути → "используя карту проекта из Фазы 0"
- Список скиллов → "используя карту скиллов из Фазы 0" + 5 универсальных категорий
- Путь сохранения отчёта → проверка `~/.claude/references/document-naming-and-placement.md`, fallback на папку документации проекта

**Не тронуто:**
- Методология: принцип "копай до дна", цепочка причин (мин. 3 звена), двойная верификация, верификация решений (ОДОБРЕНО/С ЗАМЕЧАНИЯМИ/ОТКЛОНЕНО), проверка архитектурной причины, теги `[ARCHITECTURE]→[DOC]→[CODE]→[SKILL]`
- Шаблон отчёта (структура разделов)
- Стиль отчёта (разговорный, без таблиц, сущности с пояснениями)

## Решения

- **Решение**: полная перезапись файла через Write вместо 14 отдельных Edit. **Причина**: изменения затрагивают почти все секции файла, отдельные Edit были бы ненадёжны из-за сдвига строк.
- **Решение**: скилл остаётся в `.claude/skills/` проекта, а не перемещается в `~/.claude/skill-templates/`. **Причина**: пользователь просил сделать скилл универсальным по содержанию, не менял его расположение.
- **Решение**: глобальные стандарты читаются по ссылке при каждом запуске, а не копируются в скилл. **Причина**: стандарты могут обновляться, скилл всегда получит актуальную версию.

## Контекст для следующей сессии

Скилл `root-cause-analysis` теперь универсальный. Два связанных скилла (`apply-root-cause-fixes`, `autofix-e2e`) всё ещё содержат захардкоженные пути — если нужна универсализация всей цепочки, их тоже нужно переделать по тому же принципу.

Глобальные референсы `~/.claude/references/` содержат 2 файла:
- `agent-document-triggers.md` — когда создавать ADR, Changelog, CLAUDE.md Update Log, BRD
- `document-naming-and-placement.md` — стандарт именования и размещения документов (`dev/docs/` структура)

Текущий проект (Claude Manager) использует `development/docs/` вместо `dev/docs/` — это расхождение с глобальным стандартом. Не исправлялось в этой сессии.
