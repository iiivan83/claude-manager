# Bot Transport Handler Split Design

## Цель

Сильно уменьшить `src/claude_manager/bot.py` без изменения поведения Telegram-бота.
Файл должен перестать быть местом, где одновременно живут команды, входящие
сообщения, startup-логика, watcher callbacks и регистрация handlers.

Практический результат: `bot.py` остаётся тонкой точкой сборки приложения, а
реальная логика Telegram-сценариев переезжает в небольшие модули по
ответственностям.

## Контекст

`bot.py` — транспортный слой Telegram-бота. Он принимает команды и сообщения из
Telegram, проверяет доступ, вызывает нижние модули и отправляет пользователю
ответы.

Сейчас файл вырос до 979 строк и содержит 15 публичных top-level функций. Это
выше проектного stop-порога в 700 строк и выше порога 10 публичных функций, при
котором нужно проверять модуль на роль god-module. В этом случае файл реально
смешивает несколько ответственностей:

- настройку `Application` из `python-telegram-bot`;
- startup через `post_init`;
- команды `/new`, `/sessions`, `/agent`, `/stop`, `/all`, `/restart`;
- обработку текстовых сообщений, фото и документов;
- silence mode;
- watcher callbacks для текущего проекта и all-project режима;
- регистрацию Telegram handlers;
- совместимые re-export-ы проектных handlers.

В проекте уже есть успешный пример такого разреза: `telegram_project_handlers.py`
забрал `/projects`, `/pN` и `/<project>s<session>`, а `bot.py` оставил
совместимые имена для тестов и старых импортов. Новый разрез должен продолжить
этот стиль.

## Выбранный подход

Использовать facade + доменные handler-модули.

Facade — это тонкий файл, который остаётся публичной точкой входа, но не хранит
основную бизнес-логику. Для этой задачи facade остаётся `bot.py`.

Почему этот подход выбран:

- даёт сильное уменьшение `bot.py`, а не косметический перенос нескольких
  функций;
- не создаёт новый большой `telegram_handlers.py`;
- сохраняет привычную архитектуру проекта;
- позволяет переносить код по сценариям и проверять каждый шаг отдельным gate;
- не требует менять нижние слои: `process_manager`, `claude_interaction`,
  `session_manager`, `project_manager`.

Отклонённые варианты:

- **Один общий `telegram_handlers.py`** — быстро уменьшает `bot.py`, но создаёт
  новый большой файл с теми же смешанными ответственностями.
- **Новый dispatcher/framework поверх `python-telegram-bot`** — может быть
  красивым, но это уже архитектурная перестройка, а не безопасный refactor.
- **Точечный перенос только 1-2 команд** — слишком слабый эффект для файла,
  который уже выше stop-порога.

## Границы модулей

### `bot.py`

Остаётся точкой сборки Telegram-приложения.

Отвечает только за:

- создание `Application` в `setup_bot`;
- сохранение `_application` для обратной совместимости;
- инициализацию модулей, которым нужен доступ к Telegram `Application`;
- регистрацию handlers в `_register_handlers`;
- подключение `_global_error_handler`;
- re-export старых имён, которые уже импортируют тесты и соседние модули.

`bot.py` не должен содержать реализацию пользовательских сценариев, кроме
минимального wiring-кода.

### `telegram_agent_handlers.py`

Отвечает за выбор CLI-агента.

Содержит:

- `handle_agent`;
- `handle_agent_callback`;
- построение inline-клавиатуры backend-ов;
- парсинг callback data вида `agent:<backend>`;
- текст подтверждения переключения backend-а.

Модуль использует `coding_agent_backend`, `current_backend_registry`,
`daily_session_registry` и `session_manager`, но не должен знать про
`process_manager`.

### `telegram_session_handlers.py`

Отвечает за команды, которые управляют сессиями и режимами мониторинга.

Содержит:

- `handle_new`;
- `handle_sessions`;
- `handle_all`;
- `handle_switch_session`;
- `handle_stop`.

Это единственный новый handler-модуль, которому разрешено напрямую импортировать
`process_manager`. Причина: `/stop` по смыслу останавливает CLI-процесс, и этот
контракт уже существует в текущем `bot.py`.

Граница с параллельным refactor `process_manager.py`:

- не менять сигнатуры `process_manager.has_process`;
- не менять сигнатуры `process_manager.is_busy`;
- не менять сигнатуру `process_manager.stop_process`;
- не переносить логику `/stop` в `process_manager`;
- не импортировать `process_state` напрямую.

### `telegram_input_handlers.py`

Отвечает за входящие пользовательские данные.

Содержит:

- `handle_message`;
- `_handle_single_photo`;
- `handle_photo`;
- `handle_document`;
- `_reply_anchor_kwargs`;
- выбор warning-текста для local monitoring и global `/all` mode.

Модуль вызывает `claude_interaction.send_to_claude_and_respond` и
`telegram_file_downloader.download_and_save_file`. Он не должен форматировать
ответы Claude/Codex: это остаётся задачей `telegram_response_delivery.py`.

### `telegram_lifecycle_handlers.py`

Отвечает за lifecycle Telegram-приложения и служебные команды.

Содержит:

- `post_init`;
- `_notify_restart_complete`;
- `_watcher_callback`;
- `_all_projects_watcher_callback`;
- `_get_current_session_async`;
- `handle_restart`;
- `handle_silence_on`;
- `handle_silence_off`;

Модуль запускает `session_watcher.start` и `all_projects_monitor.start`, но не
реализует polling-логику watchers. Watcher modules остаются владельцами чтения
сессий.

### `telegram_project_handlers.py`

Остаётся как есть.

Модуль уже владеет:

- `/projects`;
- `/pN`;
- `/<project>s<session>`;
- переключением проекта;
- доставкой pending messages после переключения.

Новый refactor не должен переписывать этот модуль без отдельной причины.

### `telegram_response_delivery.py`

Остаётся владельцем доставки ответов в Telegram.

Модуль уже отвечает за:

- заголовки сессий;
- markdown/html подготовку;
- файловые маркеры `[SEND_FILE:path]` и `[SHOW_FILE:path]`;
- silence mode для промежуточных ответов;
- reply anchors для watcher и прямых ответов.

Нельзя складывать новую handler-логику в этот модуль. Он уже близко к warning
порогу 300 строк и должен оставаться delivery-модулем, а не вторым `bot.py`.

## Общие зависимости и application access

Новые handler-модули должны использовать один из двух существующих паттернов:

- получать Telegram `Application` через init-callback, как
  `telegram_project_handlers.py`;
- получать `Application` через явную инициализацию, как
  `telegram_response_delivery.py`.

Предпочтительный вариант для новых handler-модулей — init-callback:

- `bot.py` остаётся владельцем `_application`;
- handler-модуль не импортирует `bot.py`;
- тесты могут подставлять mock application через callback;
- риск циклических импортов ниже.

Доступ пользователя проверяется через общий access checker. На первом шаге можно
оставить `_check_access` в `bot.py` и передавать её в handler-модули как
callback. Если после разреза это станет неудобно, отдельным маленьким шагом
можно вынести access checker в `telegram_access.py`.

## Совместимость

Публичные Telegram handler имена должны остаться доступными через
`claude_manager.bot`.

Совместимые имена:

- `handle_new`;
- `handle_sessions`;
- `handle_agent`;
- `handle_agent_callback`;
- `handle_stop`;
- `handle_all`;
- `handle_switch_session`;
- `handle_message`;
- `handle_photo`;
- `handle_document`;
- `handle_restart`;
- `handle_silence_on`;
- `handle_silence_off`;
- `post_init`;
- `setup_bot`;
- `_check_access`.

Также `bot.py` должен продолжить re-export проектных constants из
`telegram_project_handlers.py`, потому что текущие тесты импортируют их из
`claude_manager.bot`.

Совместимость важнее чистоты первого diff. Удаление re-export-ов допустимо
только отдельным будущим refactor после обновления всех потребителей.

## Data Flow

### Текстовое сообщение

До разреза:

1. `bot.py.handle_message` проверяет доступ.
2. `bot.py.handle_message` проверяет monitoring mode и silence text commands.
3. `bot.py.handle_message` вызывает `claude_interaction.send_to_claude_and_respond`.

После разреза:

1. `bot.py` регистрирует `telegram_input_handlers.handle_message`.
2. `telegram_input_handlers.handle_message` выполняет те же проверки.
3. `telegram_input_handlers.handle_message` вызывает тот же
   `claude_interaction.send_to_claude_and_respond`.

Поведение не меняется.

### `/stop`

До разреза:

1. `bot.py.handle_stop` находит активную сессию.
2. Проверяет `process_manager.has_process` и `process_manager.is_busy`.
3. Вызывает `process_manager.stop_process`.
4. Чистит reply anchor.

После разреза:

1. `bot.py` регистрирует `telegram_session_handlers.handle_stop`.
2. `telegram_session_handlers.handle_stop` выполняет тот же алгоритм.

Контракт `process_manager` не меняется.

### Startup

До разреза:

1. `setup_bot` создаёт `Application`.
2. `post_init` чистит старые файлы, грузит state, ставит команды и запускает
   watchers.

После разреза:

1. `setup_bot` остаётся в `bot.py`.
2. `post_init` переезжает в `telegram_lifecycle_handlers.py`.
3. `bot.py` передаёт `post_init` в `ApplicationBuilder`.

Startup-поведение не меняется.

## Implementation Strategy

Реализацию нужно делать после checkpoint-коммита текущего refactor
`process_manager.py` или в отдельном worktree. Текущий документ можно держать в
`main`, но кодовые правки `bot.py` не должны идти одновременно с изменениями
`process_manager.py` в одном checkout.

Рекомендуемая последовательность:

1. Создать новые handler-модули с минимальными callback-интерфейсами.
2. Перенести agent handlers и их тесты.
3. Перенести session handlers, включая `/stop`, без изменения вызовов
   `process_manager`.
4. Перенести input handlers для текста, фото и документов.
5. Перенести lifecycle handlers.
6. Оставить в `bot.py` facade, handler registration и re-export-ы.
7. Разделить `tests/test_bot.py` на focused-тесты новых модулей.
8. Оставить маленький `tests/test_bot.py` для setup/registration/re-export
   совместимости.

Каждый перенос должен быть механическим: сначала перемещение существующего
поведения, затем тесты. Улучшения логики, новые тексты и изменение UX не входят
в этот refactor.

## Testing

Минимальный Telegram gate после каждого крупного шага:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_project_switch_handlers_behavior.py tests/test_reply_anchor_input_candidates.py -q
```

Широкий orchestration gate перед завершением:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_claude_interaction.py tests/test_process_manager.py -q
```

Полный suite без E2E перед финальным утверждением:

```bash
.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q
```

E2E через Telethon не обязателен для механического refactor, если unit и
integration gates подтверждают отсутствие изменения поведения. E2E нужен, если
в ходе разреза менялась регистрация Telegram handlers или startup sequence так,
что unit-тесты не покрывают реальный polling path.

## Size Guard

Перед первой кодовой правкой нужно зафиксировать:

- строки и публичные top-level функции `src/claude_manager/bot.py`;
- строки и публичные top-level функции каждого нового `.py` файла после
  создания;
- строки и публичные top-level функции после каждого крупного переноса.

Ожидаемый результат:

- `bot.py` уменьшается примерно с 979 строк до 180-280 строк;
- каждый новый handler-модуль остаётся ниже 300 строк;
- если новый модуль подходит к 300 строкам, его нужно разбить до merge;
- если `bot.py` остаётся выше 500 строк после всех переносов, refactor не
  достиг цели и требует пересмотра границ.

## Риски

- Перенос `_application` access может создать циклический импорт, если handler
  module начнёт импортировать `bot.py`.
- Разделение `tests/test_bot.py` может скрыть регрессию регистрации handlers,
  если не оставить отдельные tests на `setup_bot`.
- `/stop` может стать нестабильным, если одновременно менять `process_manager`
  API. Поэтому этот refactor запрещает менять process-manager contract.
- `telegram_response_delivery.py` может снова начать разрастаться, если туда
  перенести handler-логику. Это запрещено границами дизайна.
- Большой механический diff трудно ревьюить. Поэтому переносы должны идти
  небольшими шагами с gate после каждого шага.

## Out of Scope

- Изменение поведения Telegram-команд.
- Изменение текстов сообщений пользователю.
- Изменение `process_manager.py`, `process_state.py` или их API.
- Изменение `claude_interaction.py`, кроме случаев, когда тесты выявят уже
  существующую callback-зависимость, которую невозможно сохранить иначе.
- Переписывание `telegram_project_handlers.py`.
- Переписывание `telegram_response_delivery.py`.
- Удаление re-export-ов из `bot.py`.
- E2E-изменения через реальный Telegram.

## Acceptance Criteria

Refactor считается успешным, когда:

- `bot.py` остаётся тонкой точкой сборки и ниже 300 строк;
- все старые handler имена доступны через `claude_manager.bot`;
- Telegram commands зарегистрированы в том же порядке, где порядок важен;
- `/stop` продолжает использовать public contract `process_manager`;
- `telegram_response_delivery.py` не получил handler-логику;
- focused-тесты новых handler-модулей покрывают перенесённое поведение;
- `tests/test_bot.py` проверяет setup, registration и re-export compatibility;
- широкий orchestration gate проходит;
- полный suite без E2E проходит перед финальным завершением.

## Self-Review

Проверка спецификации:

- Нет требований менять поведение пользователя.
- Нет требований менять `process_manager` API.
- Граница с параллельным refactor `process_manager.py` явно зафиксирована.
- Новый большой `telegram_handlers.py` запрещён.
- Указаны целевые размеры файлов и size guard.
- Указаны gates для проверки поведения.
- Нет markdown-таблиц.
