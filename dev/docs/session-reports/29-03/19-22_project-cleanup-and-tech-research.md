# Сессия 29-03: Чистка проекта, технические эксперименты, синхронизация документов

## Резюме

Проведена полная ревизия проекта: найдены и исправлены все расхождения между документами (BRD, architecture doc, CLAUDE.md, docs-index). Экспериментально проверены два ключевых технических вопроса по Claude CLI (прямая отправка и режимы инструментов). Исправлены сломанные конфиги, пересоздан .venv на Python 3.13, созданы отсутствующие файлы.

## Изменённые файлы

- `pyproject.toml` — изменён. Зависимости: `anthropic` → `python-telegram-bot` + `python-dotenv`. `requires-python`: `>=3.9` → `>=3.13`
- `requirements.txt` — изменён. Зависимости: `anthropic` → `python-telegram-bot` + `python-dotenv`
- `.venv/` — пересоздан. Python 3.9 → Python 3.13.12, зависимости установлены
- `src/claude_manager/__main__.py` — создан. Позволяет запускать бота через `python -m claude_manager`
- `.env.example` — создан. Образец настроек с описанием всех параметров (TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS, CLAUDE_WORKING_DIR, IDLE_TIMEOUT_SECONDS)
- `watch_and_restart.sh` — переписан. Путь с удалённого симлинка `telegram-claude-bot/` на `src/claude_manager/`, запуск через `python3 -m claude_manager`
- `.gitignore` — изменён. Убрана ссылка на удалённый симлинк `telegram-claude-bot`, добавлены рабочие файлы бота (`bot.pid`, `bot.log`, `received_files/`, JSON-файлы, `.session`)
- `CLAUDE.md` — изменён. Секция "Техническая спецификация" → ссылки на BRD + architecture doc (FULL-TECHNICAL-SPEC.md удалён). Убрана "Очередь сообщений" из ключевых возможностей. Убрана "очереди" из описания бизнес-слоя
- `development/docs/project_architecture.md` — существенно изменён. 12 правок для синхронизации с BRD (подробности в секции "Решения")
- `development/docs/brd-user-journeys.md` — изменён. Убран закрытый "Открытый вопрос" о stdin, добавлено описание поведения CLI. Исправлено противоречие: состояние 2 убрано "первое сообщение" из точек входа. Убрана битая ссылка на FULL-TECHNICAL-SPEC.md
- `development/docs/brd-user-journeys.BEFORE.md` — изменён. Убрана битая ссылка на FULL-TECHNICAL-SPEC.md
- `development/docs/docs-index.md` — изменён. Удалена запись о FULL-TECHNICAL-SPEC.md, "очереди" → "прямая отправка"

## Выполненные команды

- `python3.13 -m venv .venv` — пересоздание виртуального окружения на Python 3.13
- `pip install -r requirements.txt` — установка правильных зависимостей
- `claude --help` — проверка доступных флагов CLI
- `python3 /tmp/test_stream.py` — эксперимент: отправка двух сообщений в Claude CLI подряд (результат: оба обработаны последовательно)
- `python3 /tmp/test_permissions.py` — эксперимент: `--allowedTools` + `--permission-mode dontAsk` (результат: не блокирует Bash)
- `python3 /tmp/test_tools.py` — эксперимент: `--tools "Read,Glob,Grep,Edit,Write"` (результат: Bash полностью скрыт от Claude)

## Решения

- **Прямая отправка вместо очереди.** Причина: эксперимент подтвердил — Claude CLI принимает второе сообщение через stdin пока обрабатывает первое, выстраивает внутреннюю очередь. Убрана вся логика `chat_pending_messages`, `_drain_message_queue()`, `is_busy` из architecture doc
- **`--tools` вместо `control_request`/`control_response`.** Причина: `--tools "Read,Glob,Grep,Edit,Write"` полностью скрывает запрещённые инструменты от Claude (не видит их). `--allowedTools` не подошёл — разрешает использование, а не скрывает. `--permission-mode dontAsk` автоматически подтверждает все разрешённые
- **Текстовые стоп-слова — мягкая остановка.** Причина: BRD CJM-08 описывает два способа: `/stop` = kill процесса, текст "стоп" = обычное сообщение Claude. Architecture doc ошибочно описывал стоп-слова как kill — исправлено
- **BRD — источник истины.** Причина: пользователь подтвердил. Все расхождения (15 vs 5 сессий, /new поведение, AllSessionsWatcher формат, received_files/) выровнены по BRD
- **FULL-TECHNICAL-SPEC.md не нужен отдельно.** Причина: BRD + architecture doc полностью покрывают его содержание. Все ссылки обновлены
- **system_prompt.md — артефакт старого бота.** Причина: найден в `su-main-master 2/telegram-claude-bot/` (одна строка "отвечай кратко"). Не от Anthropic, не системный файл. Упоминание удалено из architecture doc
- **Нет автосоздания сессии по первому сообщению.** Причина: противоречие внутри BRD — CJM-02 говорит "подсказка", состояние 2 говорило "первое сообщение". Пользователь подтвердил: подсказка, без автосоздания. BRD исправлен
- **Флаги запуска Claude CLI для двух режимов:**
  - `project_only`: `--tools "Read,Glob,Grep,Edit,Write" --permission-mode dontAsk`
  - `full`: `--permission-mode dontAsk` (без --tools)

## Незавершённое

- [ ] Оставшиеся открытые технические вопросы не обсуждены: `--include-partial-messages` (для прогресса), `--session-id` (для создания сессий с заранее заданным UUID), `--max-budget-usd` (лимит расходов)
- [ ] pipeline-spec.md содержит промпты для создания 11 скиллов + оркестратора — ни один не создан
- [ ] Промпт для скилла spec-pipeline написан (`development/temp-docs/prompt-for-spec-pipeline-skill.md`) но скилл не создан
- [ ] Код бота пустой — только заглушка main.py и __main__.py. Реализация не начата
- [ ] Тесты пустые — только `tests/__init__.py`

## Контекст для следующей сессии

**Состояние проекта:** вся документация синхронизирована и актуальна. Конфиги исправлены, .venv на правильном Python. Код пустой — реализация не начата.

**Техническая база подтверждена экспериментами:**
- Claude CLI с `--input-format stream-json` принимает множественные сообщения через stdin
- `--tools` скрывает инструменты от Claude (project_only режим)
- `--permission-mode dontAsk` автоматически подтверждает все инструменты

**Документы для реализации:**
- BRD: `development/docs/brd-user-journeys.md` — все пользовательские сценарии
- Архитектура: `development/docs/project_architecture.md` — модули, протокол, потоки данных
- Пайплайн: `development/specs/pipeline-spec.md` — порядок реализации (9 фаз)

**Следующий шаг по пайплайну:** Фаза 0 (project-setup) или создание скиллов пайплайна. Но можно и начать реализацию модулей напрямую по architecture doc — документация достаточно подробная.
