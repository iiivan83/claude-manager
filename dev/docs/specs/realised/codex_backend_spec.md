# Спецификация модуля: codex_backend

Дата: 06-05-2026
Слой: 1 (зависит только от `coding_agent_backend` — слой 0)
Файл: `src/claude_manager/codex_backend.py`

**Родительская спека:** `dev/docs/specs/coding_agent_backend_spec.md` — определяет абстрактный интерфейс `CodingAgentBackend`, общие DTO (`UnifiedEvent`, `SessionFileInfo`, `SessionMessage`, `SessionFileSnapshot`, `TerminalStatus`, `StopSignalStep`, `StopStrategy`), исключения (`BackendError`, `BackendBinaryNotFoundError`, `BackendProtocolError`), фабрику `get_backend`.

**Парные спеки:**
- `dev/docs/specs/claude_code_backend_spec.md` — реализация интерфейса для Claude Code CLI (первая ветка Adapter pattern)
- `dev/docs/specs/current_backend_registry_spec.md` — персистентное хранилище выбранного бэкенда

## Актуализация 30-05-2026: operational index для горячего листинга

Codex хранит rollout-файлы глобально в `~/.codex/sessions/YYYY/MM/DD/`, а принадлежность проекту лежит внутри записи `session_meta.payload.cwd`. Поэтому прямой operational-листинг проекта раньше был дорогим: код открывал много чужих JSONL-файлов, чтобы найти несколько нужных.

Для горячих путей (`/pN`, `session_watcher.reset_state`, `poll_once`, pending-сбор) добавлен модуль `codex_session_index.py`. Он держит in-memory карту `project_dir -> list[SessionFileInfo]` за operational lookback-окно и хранит только лёгкие метаданные: `session_id`, `file_path`, `project_dir`, `last_modified_at`. Preview не строится, потому что горячим путям нужен не UI-текст, а быстрый список файлов.

Контракт теперь такой:

- `list_all_session_file_infos_for_project(..., lookback_days=N)` делегирует в `codex_session_index.list_project_session_file_infos(...)`.
- `lookback_days=None` сохраняет legacy full scan для совместимости.
- `list_session_file_infos_for_project(...)` для `/sessions` остаётся без operational index, потому что ему нужен preview и лимит свежих сессий.
- `config.OPERATIONAL_SESSION_LOOKBACK_DAYS = 4`: сегодня и три предыдущих дня. Повторные обращения в этом окне не перечитывают `session_meta` всех rollout-файлов, пока не изменилась подпись date-директорий или не истёк safety TTL индекса.

Индекс не является источником содержимого сообщений. Summary, preview, pending-дельта и watcher snapshot всё ещё читают реальные файлы через backend reader.

## Версия Codex CLI

Эта спека описывает **кастомную сборку Codex CLI v0.128.0**, исходники которой лежат в `~/.codex/custom-codex-rust-v0.128.0/codex-rs/`. Все флаги (`--json`, `--dangerously-bypass-approvals-and-sandbox`, `--skip-git-repo-check`, `-C/--cd`), имена событий stdout (`thread.started`, `turn.completed`, `item.completed`, `turn.failed`), формат файла сессии (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` с adjacently-tagged JSON), имена подтипов в `event_msg` (`task_started`, `task_complete`, `agent_message`, `token_count`) и поведение сигналов остановки (`SIGINT` → `TurnInterrupt`) подтверждены чтением Rust-исходников именно этой сборки и эмпирическими запусками реального CLI.

**При установке другой версии или сборки Codex CLI** (другая мажорная версия, публичный upstream OpenAI Codex CLI, форк) флаги, имена событий, структура файла сессии и обработка сигналов могут отличаться. Конкретные риски:

- Флаг `--skip-git-repo-check` или `--dangerously-bypass-approvals-and-sandbox` может называться иначе или отсутствовать
- `codex exec resume` как подкоманда может отсутствовать или иметь другую сигнатуру (например, требовать `-C/--cd` или принимать промпт через stdin, а не как позиционный аргумент)
- Имена `RolloutItem.payload.type` (`task_complete`, `agent_message`, `token_count`) могут быть переименованы — это сломает `is_turn_terminal_session_record` и парсинг сообщений
- Реакция на `SIGINT` может отличаться от описанной (отсутствовать или вести себя иначе) — это сломает корректную запись `TurnInterrupt` в файл сессии и сценарий `/stop`

**Обязательные шаги при апгрейде/смене сборки Codex CLI:**
1. Прогнать контрактные тесты (`tests/integration/test_codex_backend_contracts.py`) — они построены на эмпирической проверке реального CLI и валятся первыми при расхождении контракта.
2. При падении контрактных тестов — **сначала обновить эту спеку** (привести в соответствие с новой сборкой), затем — реализацию `codex_backend.py`. Не править реализацию без обновления спеки: спека — источник истины контракта, реализация её следствие.
3. Зафиксировать новую версию Codex CLI в первой строке этого блока («сборка vXXX»), указать, что именно изменилось в флагах/событиях/сигналах.

## Назначение

Конкретная реализация абстрактного интерфейса `CodingAgentBackend` для Codex CLI (`codex` v0.128.0+). Класс `CodexBackend` инкапсулирует всю «локальную правду» о работе с Codex CLI: формат подкоманды `codex exec` и `codex exec resume`, набор флагов для headless-режима, структура потоковых событий `--json` в stdout (`thread.started`, `turn.completed`, `item.completed` и т. д.), формат файла сессии `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` с adjacently-tagged JSON-структурой, фильтрация сессий по проекту через поле `payload.cwd` записи `session_meta`.

Модуль **не запускает subprocess сам** — это делает `process_manager`. Не управляет лайфтаймом процессов, не реализует ретраи. Его задача — отвечать на вопросы вызывающей стороны: «какие аргументы команды для запуска?», «как закодировать сообщение в stdin?», «как распарсить строку из stdout?», «где лежат файлы сессий проекта?». Это чистый адаптер между внешним инструментом (Codex CLI) и внутренним контрактом (`CodingAgentBackend`).

В отличие от `ClaudeCodeBackend`, у этого модуля **нет существующего рабочего кода в проекте** для адаптации — Codex CLI впервые поддерживается ботом. Все контракты с инструментом получены из исходников Rust-проекта `~/.codex/custom-codex-rust-v0.128.0/codex-rs/` (точные ссылки — в разделе «Контракты с внешними системами») и подтверждены эмпирически (запуск реального `codex exec`, чтение реального файла сессии).

## Расхождения с концепцией от 06-05-2026

В концептуальной сессии 06-05 (`dev/docs/session-reports/06-05/06-38_codex-backend-concept-planning.md`) было зафиксировано несколько технических деталей, которые в этой спецификации уточнены или скорректированы. Каждое расхождение — техническое, не семантическое.

- **stdin Codex — пустые байты `b""`, не plain text промпт.** В концепции сказано «Stdin — plain text (НЕ JSON, в отличие от Claude)». На самом деле Codex принимает промпт как **позиционный аргумент команды** (`codex exec ... <prompt>`), а не через stdin. stdin используется Codex'ом только для чтения промпта при значении аргумента `-` или при пайпе (`echo "x" | codex exec`). Бот формирует команду со встроенным промптом, поэтому `encode_user_message_for_cli_stdin` возвращает `b""`. Это согласуется с родительской спекой `coding_agent_backend_spec.md:140-159`: «Если бэкенду не нужен stdin (промпт уже в args через `compose_subprocess_command_args`) — возвращает `b""`».

- **Resume не принимает `-C/--cd`.** В концепции команда resume записана как `codex exec resume <session_id> <prompt> --json`. Эмпирически (`codex exec resume --help`) у `resume` **нет** глобального флага `-C/--cd` — он не помечен `global = true` в Rust-коде (`exec/src/cli.rs:160-258`). Рабочая директория задаётся через параметр `cwd=` функции `asyncio.create_subprocess_exec` на уровне `process_manager` — Codex resume читает cwd процесса, а не флага. В сигнатуре `compose_subprocess_command_args` параметр `cwd` сохраняется (контракт интерфейса), но игнорируется методом для resume и используется только для новой сессии (`-C <cwd>`).

- **Подтипы `event_msg`: `task_started` / `task_complete`, не `turn_started` / `turn_complete`.** В концепции 06-38 упоминается «`turn.completed` (финальное)» как имя события в файле сессии. Эмпирически (`~/.codex/sessions/.../rollout-*.jsonl`) и по исходникам (`protocol/src/protocol.rs:1351-1357`) в файле сессии используется serde-rename: Rust-вариант `TurnStarted` сериализуется в JSON как `"task_started"`, `TurnComplete` — как `"task_complete"`. В **stdout `--json`** же используются `"turn.started"` / `"turn.completed"` (другая структура — `ThreadEvent`, `exec/src/exec_events.rs:13-36`). Спека различает эти два контекста явно: stdout (читается `parse_stdout_line_into_event`) и файл сессии на диске (читается `read_messages_from_session_file`).

- **Поле текста ассистента — разное в stdout и файле сессии.** В stdout `item.completed` с `item.type == "agent_message"` имеет поле `item.text` (struct `AgentMessageItem`, `exec/src/exec_events.rs:134-137`). В файле сессии запись `event_msg.payload.type == "agent_message"` имеет поле `message` (struct `AgentMessageEvent`, `protocol/src/protocol.rs:2283-2289`). Это разные struct-ы, и спека учитывает оба.

- **Lookback-окно при сканировании сессий — 30 дней, не 2.** В первой версии родительской спеки было сказано «обойти `~/.codex/sessions/YYYY/MM/DD/` за последние 2 дня». Это значение неоправданно маленькое для пользователя бота: список сессий через `/sessions` показывает не больше 15 свежих, и при активной работе пользователь может за неделю-две накопить десятки сессий, из которых будут выбраны 15 самых новых. Спека увеличивает окно до 30 дней (константа `LOOKBACK_DAYS_FOR_SESSION_LISTING`). При нагрузке 100 сессий/день это 3000 файлов на проход — приемлемая стоимость одного `/sessions` (десятки миллисекунд через `asyncio.to_thread`). Текущая родительская спека уже фиксирует lookback как backend-specific контракт, а точное значение оставляет реализации.

- **Метод `is_turn_terminal_session_record` поднят в родительский интерфейс.** В первой версии этой спеки `CodexBackend.is_turn_terminal_session_record(record)` был Codex-only расширением: метод объявлялся только в Codex-бэкенде, а `session_watcher` обязан был вызывать его через `isinstance(backend, CodexBackend)` или `backend.name == BackendName.CODEX`. После уточнения ревью 06-05 решение пересмотрено: метод поднят в `CodingAgentBackend` как абстрактный, и теперь его реализуют ОБА бэкенда. Реализация для Claude — тривиальная (`record.get("type") == "result"`, см. `claude_code_backend_spec.md`), для Codex — нетривиальная (подтип `event_msg.payload.type == "task_complete"`, как было раньше). Это устраняет ветвление по `backend.name` в потребителе: `session_watcher` остаётся полностью backend-агностичным, и добавление третьего бэкенда не потребует править watcher. **Причина:** уточнение по итогам ревью 06-05 — нарушение принципа подмены Лисков (LSP) в потребителе.

- **Дублирование helper-функций с `claude_code_backend`.** Несколько внутренних функций (`_parse_jsonl_string_lines`, `_read_file_lines_blocking`, `_sort_paths_by_mtime_descending`) идентичны соответствующим функциям в `claude_code_backend.py`. Спека сознательно описывает их как переносимые **копированием**, а не как импорт из общего модуля. Причина: задача спеки — зафиксировать поведение модуля изолированно, а выделение общих helper-ов — задача рефакторинга, которая принимается на этапе реализации (не блокирует написание спеки). При реализации возможны два варианта: (а) скопировать функции в `codex_backend.py`, (б) выделить в `_jsonl_helpers.py` и импортировать в обоих бэкендах. Решение — на усмотрение разработчика; ни один не противоречит спеке.

- **`event_types_meaning_cli_is_busy()` возвращает четыре значения, не одно.** В первой версии родительской спеки было задекларировано: «Для Codex — `frozenset({"event_msg"})` с дополнительной проверкой подтипа в `read_messages_from_session_file` (исключения: подтипы `task_complete`, `token_count` означают окончание)». Эта спека возвращает `frozenset({"event_msg", "response_item", "turn_context", "compacted"})` — все типы записей `RolloutItem` кроме `session_meta`. Причина: если watcher увидит, что последняя запись в файле — `response_item` (например, ассистент только что записал ответ как `output_text`, но `task_complete` ещё не пришёл), старая формулировка считала это «не busy» и помечала сообщения финальными — что **неверно**, потому что после `response_item` ещё не пришёл `event_msg.payload.type == "task_complete"`, и turn не завершён. Также утверждение «`token_count` означает окончание» неверно по эмпирике (`token_count` приходит и во время, и после turn-а — это служебное обновление статистики, не маркер). Корректное определение завершённости turn-а — только через подтип `task_complete` в `event_msg`, что инкапсулировано в методе `is_turn_terminal_session_record`. Текущая родительская спека уже направляет watcher на `SessionFileSnapshot.is_turn_active`, чтобы потребитель не разбирал эти подтипы вручную.

- **Канонический источник текста сообщений в файле сессии — `response_item`, не `event_msg`.** В первой версии родительской спеки было задекларировано: «Для `type == "event_msg"` с подтипом `agent_message` — это финальный ответ ассистента (`role="assistant"` в SessionMessage)». Эта спека читает сообщения **только из `response_item`** записей (с `payload.type == "message"` и `payload.role in {"user", "assistant"}`), а `event_msg.payload.type == "agent_message"` пропускает как дубликат. Причины:
  - **Симметрия с Claude.** Claude хранит сообщения в записях `type ∈ {"user", "assistant"}` с полем `message.content` (массив content-блоков). Codex хранит то же — в `response_item.payload.content` с `[{type: "input_text", text: ...}]` или `[{type: "output_text", text: ...}]`. Один паттерн чтения для обоих бэкендов сокращает мысленную нагрузку.
  - **Для пользовательских сообщений `event_msg.user_message` содержит только финальный текст**, без структурированных content-блоков. Если пользователь когда-нибудь начнёт отправлять multi-modal ввод (текст + изображение в одном сообщении), `response_item` это поддержит, а `event_msg.user_message` нет. Не закладывать future-incompatible выбор.
  - **`event_msg.agent_message` — wire-протокольная обёртка для трансляции событий в реальном времени**, а `response_item` — каноническая запись истории. По эмпирике (см. файл `~/.codex/sessions/2026/05/06/rollout-*.jsonl`) оба формата дублируют друг друга по содержанию текста; выбор любого даёт идентичный результат для текущих use-case-ов, но `response_item` — корректнее семантически.

  Текущая родительская спека уже фиксирует `response_item` как канонический источник текста Codex-сообщений в файле сессии.

- **Явная обработка `turn.failed` через `is_error_event` / `read_error_text_from_event` / `read_terminal_status_from_event`.** В первой версии спеки `turn.failed` обрабатывался косвенно: «`read_assistant_text_from_event` вернёт `None`, так что `process_manager` увидит „turn закончился без текста ассистента“ и применит свою логику ретрая». Это неверно: пустой ответ может прийти и при штатном завершении (модель ничего не сказала, но turn успешен), и `process_manager` не сможет различить два сценария. Из-за этого пользователь получит пустой ответ вместо ретрая или внятной ошибки, а текст из `turn.failed.error.message` потеряется. Спека использует три расширения родительского интерфейса (которые также присутствуют в `claude_code_backend_spec.md` для симметрии): `is_error_event(event) -> bool`, `read_error_text_from_event(event) -> str | None`, `read_terminal_status_from_event(event) -> TerminalStatus | None`. `process_manager` строит решение о retry по **явному флагу `is_error=True`**, а не по «пустому тексту»: при `turn.failed` `is_error_event` возвращает `True`, `read_error_text_from_event` возвращает строку из `event["error"]["message"]`, `read_terminal_status_from_event` возвращает `TerminalStatus.FAILED`. Текущая родительская спека уже объявляет эти методы как абстрактные в `CodingAgentBackend` и определяет enum `TerminalStatus`. См. раздел «Публичный API → is_error_event / read_error_text_from_event / read_terminal_status_from_event» и «Алгоритм работы → жизненный цикл turn-а». **Причина:** ревью 06-05, недоделка №3.

- **Snapshot-контракт чтения файла сессии: `read_session_file_snapshot` с DTO `SessionFileSnapshot`.** Старый метод `read_messages_from_session_file` возвращал `list[SessionMessage]` — этого недостаточно для `session_watcher` (`session_watcher` отслеживает счётчик сырых JSONL-строк для дельта-чтения, последнюю запись для проверки активности turn-а, и факт активного turn-а отдельно от списка сообщений). Спека использует метод `read_session_file_snapshot(file_path) -> SessionFileSnapshot` с полями `messages`, `raw_record_count`, `last_record`, `is_turn_active`. **Поле `is_turn_active`** для Codex равно `False` тогда и только тогда, когда последняя валидная запись файла — `event_msg` с подтипом `task_complete` (надёжный маркер штатного завершения turn-а) **либо** `event_msg` с подтипом из множества «терминал-неудача» (`error`, `turn_aborted`), если такая запись действительно присутствует на конце файла. **`token_count` НЕ считается финальным маркером** — это служебное обновление статистики, оно приходит и до, и после `task_complete`. **Если последняя запись — `response_item` с `phase == "final_answer"`**, но `task_complete` ещё не записан — `is_turn_active = True` (turn ещё в процессе шатдауна, watcher не должен помечать сообщения финальными). Метод `read_messages_from_session_file` сохраняется для `session_reader` (там snapshot-поля не нужны) и реализуется как удобный wrapper: `(await read_session_file_snapshot(...)).messages`. Текущая родительская спека уже объявляет DTO `SessionFileSnapshot` и абстрактный метод `read_session_file_snapshot`. **Причина:** ревью 06-05, недоделка №2.

- **Стратегия остановки `/stop`: SIGINT-first, не SIGTERM-эквивалент.** В первой версии спеки `/stop` описывался как «`process.terminate()` (SIGTERM, эквивалентно SIGINT по эффекту — оба прерывают turn и инициируют shutdown)». Это неверно: эмпирически (`exec/src/lib.rs:741-843`) Codex CLI обрабатывает **именно SIGINT** через `tokio::signal::ctrl_c()` — после получения SIGINT отправляет на сервер `ClientRequest::TurnInterrupt`, дописывает в файл сессии запись прерывания и инициирует штатный shutdown. SIGTERM не идёт через тот же handler — Tokio runtime отрабатывает его как «обычное завершение», без отправки `TurnInterrupt`, и в JSONL-файле может не оказаться записи о штатном прерывании, что ломает `session_watcher` (тот не увидит маркера завершения turn-а и оставит сообщения в «промежуточном» состоянии). Спека использует метод `get_stop_strategy() -> StopStrategy` и DTO `StopSignalStep` для описания многошаговой эскалации. Для Codex стратегия — **SIGINT → таймаут → SIGTERM → таймаут → SIGKILL**: первый сигнал даёт Codex время на штатный `TurnInterrupt`, второй (SIGTERM) — fallback при зависшем shutdown-handler-е, третий (SIGKILL) — гарантия завершения процесса. Конкретные таймауты — в разделе «Константы». Текущая родительская спека уже объявляет DTO `StopStrategy`/`StopSignalStep` и абстрактный метод `get_stop_strategy()`. **Причина:** ревью 06-05, недоделка №4.

- **CJM-08 (`/stop`): модуль участвует, не пропускается.** В первой версии спеки было: «модуль НЕ участвует, остановка унифицирована на уровне `process_manager` через `process.terminate()`». После добавления `get_stop_strategy()` это противоречит реальному поведению Codex (см. предыдущий пункт). `process_manager` берёт стратегию у бэкенда и применяет её — модуль участвует через возврат `StopStrategy` со списком `StopSignalStep`. **Причина:** ревью 06-05, недоделка №4.

- **Изображения в текущей версии — путь в `prompt_text`, флаг `-i/--image` не используется.** Эмпирически проверено через `codex exec resume --help`: у Codex CLI есть параметр `-i/--image`, который добавляет изображения как separate-файлы перед промптом. Текущая реализация бота сохраняет файл на диск, кладёт абсолютный путь прямо в текст сообщения, и Codex (как и Claude) сам распознаёт путь и вызывает встроенный инструмент чтения (`view_image`). Это **осознанное ограничение первой версии** — путь в тексте уже работает для существующего потока бота (модуль `claude_interaction` использует тот же приём для Claude), и переход на `-i` потребовал бы дополнительной логики копирования файла в формате, который Codex принимает. Контрактный тест `test_codex_view_image_path_in_prompt_text` проверяет, что путь в `prompt_text` действительно срабатывает (Codex вызывает `view_image` без флага `-i`). **Критерий перехода на `-i/--image`:** если в production-логе обнаружится, что Codex стабильно (≥3 раз за неделю) пропускает путь к изображению в тексте промпта, или если пользователь начнёт отправлять multi-modal сообщения с несколькими изображениями подряд, где порядок и группировка важны — переключиться на `-i` и обновить `compose_subprocess_command_args` (передавать пути как `["-i", path1, "-i", path2, ...]` перед `prompt_text`). До тех пор `image_paths` остаётся в сигнатуре, но игнорируется реализацией. **Причина:** ревью 06-05, недоделка №10.

## Обслуживаемые сценарии

Сам модуль не обслуживает CJM напрямую (он — инфраструктура для других модулей), но без его методов не работают:

- **CJM-02 (текстовое сообщение)** — `compose_subprocess_command_args`, `encode_user_message_for_cli_stdin`, `parse_stdout_line_into_event`, `is_turn_complete_event`, `read_session_id_from_event`, `read_assistant_text_from_event`, `read_progress_text_from_event`, `is_error_event`, `read_error_text_from_event`, `read_terminal_status_from_event`, `text_markers_indicating_empty_response` используются `process_manager` для запуска Codex CLI и извлечения ответа. На событии `turn.failed` методы-сигнализаторы возвращают `is_error_event == True`, текст ошибки извлекается через `read_error_text_from_event` (`event["error"]["message"]`), терминальный статус — `TerminalStatus.FAILED`. Это даёт `process_manager` явный маркер для retry или внятной ошибки пользователю — без неоднозначных эвристик «пустой текст значит ошибка»
- **CJM-03 (фото или файл)** — путь к файлу включён в `prompt_text` модулем `claude_interaction` ровно так же, как для Claude. Codex видит путь в тексте промпта и сам вызывает встроенный инструмент `view_image` (capability check на стороне Codex; модуль не делает предварительной проверки модели). Альтернативный механизм через флаг `-i path` в команде существует и поддерживается Codex CLI (`shared_options.rs:9-17`), но **в первой версии этого бэкенда не используется** — это осознанное ограничение, обоснование и критерий перехода на `-i` — в разделе «Расхождения с концепцией от 06-05-2026», в пункте про изображения
- **CJM-04 (`/new`)** — `compose_subprocess_command_args(session_id=None, ...)` формирует команду `codex exec ... <prompt>` без подкоманды `resume`
- **CJM-05 (`/sessions`)** — `locate_session_files_directory_for_project`, `list_session_files_for_project`, `read_messages_from_session_file` используются `session_reader`/потребителем для чтения метаданных сессий с диска
- **CJM-06 (`/N`)** — `compose_subprocess_command_args(session_id=<id>, ...)` формирует команду `codex exec resume <session_id> ... <prompt>`
- **CJM-07 (`/all`)** — `list_all_session_files_for_project`, `read_session_file_snapshot` (плюс `read_messages_from_session_file` для совместимости), `event_types_meaning_cli_is_busy`, `text_markers_indicating_empty_response`, `is_turn_terminal_session_record` используются `session_watcher` для слежения за файлами в реальном времени. Snapshot-DTO `SessionFileSnapshot` (`messages`, `raw_record_count`, `last_record`, `is_turn_active`) даёт watcher-у дельта-чтение по `raw_record_count` и однозначное определение «turn закончен» через `is_turn_active`. Концепция 06-38 предусматривает **две независимые инстанции watcher** (одну на бэкенд) — этот модуль предоставляет нужные методы для своей инстанции
- **CJM-08 (`/stop`)** — модуль **участвует** через метод `get_stop_strategy() -> StopStrategy`. Стратегия остановки Codex — многошаговая эскалация: SIGINT (даёт Codex время на штатный `TurnInterrupt` и запись маркера прерывания в JSONL) → таймаут → SIGTERM (fallback на случай зависшего shutdown-handler-а) → таймаут → SIGKILL (гарантия завершения процесса). Это отличается от Claude, у которого `get_stop_strategy()` возвращает только пару SIGTERM → SIGKILL без промежуточного SIGINT (Claude обрабатывает SIGINT и SIGTERM одинаково). У Codex нет subcommand-а или флага для прерывания turn-а извне — единственный способ остановить — сигналы. У Codex также нет синтетического маркера типа `"No response requested."` — `text_markers_indicating_empty_response()` возвращает пустое множество

Также модуль обслуживает новый сценарий, который будет добавлен в BRD при реализации фичи переключения бэкенда:

- **CJM-NEW (`/agent`)** — фабрика `get_backend(BackendName.CODEX)` (определена в `coding_agent_backend`, не в этой спеке) возвращает singleton-инстанс `CodexBackend`. Модуль предоставляет свойства `name` и `display_name` для UI команды `/agent` (inline-клавиатура с двумя вариантами).

На момент написания спецификации CJM-NEW в `dev/docs/brd/brd-user-journeys.md` отсутствует — он будет добавлен отдельной задачей перед реализацией модуля.

## Публичный API

### Класс `CodexBackend(CodingAgentBackend)`

Реализация абстрактного интерфейса для Codex CLI. Без хранимого состояния (stateless) — все методы работают со своими аргументами, не читают и не пишут поля экземпляра. Singleton-инстанс создаётся фабрикой `get_backend(BackendName.CODEX)` из `coding_agent_backend`.

```python
class CodexBackend(CodingAgentBackend):
    """Adapter pattern для Codex CLI. Реализует все 18 методов и 2 свойства интерфейса."""
```

#### Свойство `name`

```python
@property
def name(self) -> BackendName:
    return BackendName.CODEX
```

Возвращает идентификатор бэкенда. Используется потребителями для записи в `daily_session_registry`, для сравнения в фабрике, для логов.

#### Свойство `display_name`

```python
@property
def display_name(self) -> str:
    return BACKEND_DISPLAY_NAME_CODEX  # "⚡ Codex"
```

Возвращает человекочитаемое имя бэкенда с эмодзи. Используется в UI Telegram: командах `/agent`, `/sessions`, формате ретрая (`#N Ошибка ⚡ Codex, повтор X/10`).

#### `compose_subprocess_command_args(session_id, cwd, prompt_text, image_paths) -> list[str]`

```python
def compose_subprocess_command_args(
    self,
    session_id: str | None,
    cwd: str,
    prompt_text: str,
    image_paths: list[str],
) -> list[str]: ...
```

Формирует список аргументов командной строки для запуска subprocess (включая бинарник `codex` как `args[0]`).

**Аргументы:**
- `session_id` (`str | None`) — UUID сессии (`thread_id`) для resume или `None` для новой сессии
- `cwd` (`str`) — рабочая директория проекта. Используется **только** для новой сессии — добавляется как флаг `-C <cwd>`. Для resume игнорируется, потому что `codex exec resume` не имеет глобального флага `-C/--cd` (`exec/src/cli.rs:160-258`); рабочая директория задаётся через параметр `cwd=` функции `asyncio.create_subprocess_exec`, что делает потребитель `process_manager`
- `prompt_text` (`str`) — текст пользовательского сообщения. **В отличие от Claude — включается в команду как последний позиционный аргумент**, а не идёт через stdin. Может содержать любые символы; `subprocess.create_exec(*args, ...)` передаёт аргументы массивом, без shell-интерполяции, поэтому экранирование не нужно
- `image_paths` (`list[str]`) — пути к изображениям. **Игнорируются методом** в текущей реализации — путь к файлу уже включён в `prompt_text` модулем `claude_interaction`, Codex видит путь в тексте и сам вызывает встроенный инструмент `view_image`. Аргумент сохранён в сигнатуре для совместимости с родительским интерфейсом и для возможного будущего использования флага `-i <path>` (он поддерживается Codex CLI через `shared_options.rs:9-17`, но в боте не задействован)

**Возвращает:** `list[str]`. Точный состав:

Для **новой сессии** (`session_id is None`):
```
[binary_path,
 "exec",
 "--json",
 "--dangerously-bypass-approvals-and-sandbox",
 "--skip-git-repo-check",
 "-C", cwd,
 prompt_text]
```

Для **resume** (`session_id is not None`):
```
[binary_path,
 "exec",
 "resume",
 session_id,
 "--json",
 "--dangerously-bypass-approvals-and-sandbox",
 "--skip-git-repo-check",
 prompt_text]
```

Без `--skip-git-repo-check` Codex отказывается работать в не-git директориях. Без `--dangerously-bypass-approvals-and-sandbox` (alias `--yolo`) Codex запросит интерактивное подтверждение перед каждым изменением — это ломает headless-режим. `--json` обязателен — без него Codex выводит человекочитаемый текст в stdout, который не парсится протоколом.

**Исключения:**
- `BackendBinaryNotFoundError` — бинарник `codex` не найден в `PATH`. Проверка lazy: происходит при первом вызове метода, а не при импорте модуля (см. раздел «Обработка ошибок»)

#### `encode_user_message_for_cli_stdin(prompt_text, image_paths) -> bytes`

```python
def encode_user_message_for_cli_stdin(
    self,
    prompt_text: str,
    image_paths: list[str],
) -> bytes: ...
```

Кодирует пользовательское сообщение в байты для записи в stdin процесса. Для Codex CLI **всегда возвращает пустые байты `b""`** — промпт уже включён в команду через `compose_subprocess_command_args`, stdin не используется. Потребитель (`process_manager`), увидев пустые байты, не пишет в stdin и сразу закрывает его.

**Аргументы:**
- `prompt_text` (`str`) — текст сообщения. **Игнорируется** методом
- `image_paths` (`list[str]`) — пути к изображениям. **Игнорируются** методом

**Возвращает:** `bytes`. Всегда `b""` (литерал).

**Исключения:** не выбрасывает.

#### `parse_stdout_line_into_event(raw_line) -> UnifiedEvent | None`

```python
def parse_stdout_line_into_event(self, raw_line: str) -> UnifiedEvent | None: ...
```

Парсит одну строку stdout (одна строка JSONL из режима `--json`) в унифицированное событие.

**Аргументы:**
- `raw_line` (`str`) — строка из stdout без завершающего `\n`, в UTF-8

**Возвращает:** `UnifiedEvent` (тип-алиас `dict[str, Any]`) — результат `json.loads(raw_line)`. Либо `None`, если строка пустая (после `strip()`).

**Исключения:**
- `BackendProtocolError` — `raw_line` не парсится как валидный JSON. Сообщение исключения содержит первые 200 символов строки. Это контрактное нарушение CLI — Codex обязан выдавать валидный JSON в `--json` режиме (`exec/src/event_processor_with_jsonl_output.rs:104-114`, сериализация через `serde_json::to_string`)

#### `is_turn_complete_event(event) -> bool`

```python
def is_turn_complete_event(self, event: UnifiedEvent) -> bool: ...
```

Возвращает `True`, если событие означает завершение текущего turn-а — после него `process_manager` должен прекратить чтение stdout.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `bool`. Возвращает `True` тогда и только тогда, когда `event.get("type")` входит в множество `STDOUT_TURN_TERMINAL_EVENT_TYPES = frozenset({"turn.completed", "turn.failed"})`. Оба события — финальные, после `turn.failed` процесс тоже завершается (`exec/src/lib.rs:521,543` — оба переходят в `InitiateShutdown`).

#### `read_session_id_from_event(event) -> str | None`

```python
def read_session_id_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает идентификатор сессии (`thread_id`) из события.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `str | None`.
- Если `event.get("type") == STDOUT_EVENT_TYPE_THREAD_STARTED` (строка `"thread.started"`) — возвращает `event.get("thread_id")` (UUID v7, `exec_events.rs:40-43`)
- В остальных событиях `thread_id` отсутствует — возвращает `None`

В отличие от Claude, у Codex `session_id`-эквивалент (`thread_id`) присутствует **только в первом событии** (`thread.started`). Все последующие события (`turn.started`, `item.*`, `turn.completed`, `turn.failed`) его не содержат. `process_manager` при первом получении непустого значения вызывает `session_id_callback(old, new)` и обновляет привязки (см. CLAUDE.md → «Единство идентификатора сессии (temp → real)»).

#### `read_assistant_text_from_event(event) -> str | None`

```python
def read_assistant_text_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает финальный текст ответа ассистента из события.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `str | None`.
- Если `event.get("type") != STDOUT_EVENT_TYPE_ITEM_COMPLETED` (строка `"item.completed"`) — возвращает `None` (не содержит финального текста)
- Если событие — `item.completed`: проверяет `event.get("item", {}).get("type") == ITEM_TYPE_AGENT_MESSAGE` (строка `"agent_message"`). Если да — возвращает `event["item"]["text"]` (поле `text` струтуры `AgentMessageItem`, `exec_events.rs:134-137`). Если нет — возвращает `None`

**Stateless семантика накопления.** Codex может выдать несколько `item.completed` с `agent_message` за один turn (модель отвечает несколькими сообщениями подряд). Метод `read_assistant_text_from_event` возвращает текст **каждого** такого события; накопление «последнего» — ответственность вызывающей стороны. `process_manager` запоминает последнее ненулевое возвращённое значение и использует его как окончательный ответ при `is_turn_complete_event == True`. Это поведение зафиксировано в родительской спеке (`coding_agent_backend_spec.md:218-222`).

`turn.completed` сам по себе текст не содержит (только `usage`-метаданные, `exec_events.rs:49-52`). Этот метод для `turn.completed` вернёт `None`, потому что `event["type"]` будет не `item.completed`.

#### `read_progress_text_from_event(event) -> str | None`

```python
def read_progress_text_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает текст промежуточного обновления (для отправки пользователю как progress-сообщение `#N ⏳ ...`).

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `str | None`.
- Если `event.get("type") != STDOUT_EVENT_TYPE_ITEM_COMPLETED` — возвращает `None`
- Если событие — `item.completed`: проверяет `event.get("item", {}).get("type") == ITEM_TYPE_REASONING` (строка `"reasoning"`). Если да — возвращает `event["item"]["text"]` (поле `text` структуры `ReasoningItem`, `exec_events.rs:140-143` — это reasoning summary, отрывок суммаризации мыслей модели). Если `item.type == "agent_message"` — возвращает `None` (это финальный ответ, а не прогресс; он попадёт в `read_assistant_text_from_event`)

**Поведение для `item.started` / `item.updated`.** Согласно `event_processor_with_jsonl_output.rs:333-341`, `agent_message` и `reasoning` НЕ эмитятся как `item.started` — только как `item.completed`. Это значит, что для прогресса достаточно проверять `item.completed`. События `item.started` для других типов (`command_execution`, `file_change`, `mcp_tool_call`) могут быть, но их progress-текст в текущей версии бэкенда не показывается пользователю (это отдельная фича). Метод возвращает `None` для всех `item.started` и `item.updated`.

**Отличие от Claude.** У Claude поток thinking-блоков может приходить вперемешку с text-блоками в одном `assistant`-событии, и код выбирает text над thinking. У Codex reasoning никогда не идёт одновременно с agent_message в одном `item.completed` (это разные структуры варианта `ThreadItemDetails`, `exec_events.rs:102-130`), поэтому приоритизация не нужна. Метод возвращает либо reasoning-текст, либо `None` — однозначно.

#### `locate_session_files_directory_for_project(project_dir) -> str`

```python
def locate_session_files_directory_for_project(self, project_dir: str) -> str: ...
```

Возвращает абсолютный путь к директории, где Codex CLI хранит JSONL-файлы сессий. **Игнорирует аргумент `project_dir`** — Codex хранит сессии глобально (`~/.codex/sessions/`), не по проектам (`rollout/src/lib.rs:21`, константа `SESSIONS_SUBDIR = "sessions"`). Фильтрация по проекту делается в методах перечисления сессий через чтение поля `payload.cwd` записи `session_meta`.

**Аргументы:**
- `project_dir` (`str`) — абсолютный путь к директории проекта. **Игнорируется** методом, сохраняется в сигнатуре для совместимости с родительским интерфейсом

**Возвращает:** `str`. Формат: `<HOME>/.codex/sessions`, где `HOME` — домашняя директория пользователя (`os.path.expanduser("~")`).

**Исключения:** не выбрасывает.

#### `list_session_files_for_project(project_dir) -> list[SessionFileInfo]`

```python
async def list_session_files_for_project(
    self, project_dir: str
) -> list[SessionFileInfo]: ...
```

Возвращает список метаданных JSONL-файлов сессий для данного проекта, отсортированный от свежих к старым, не более `MAX_RECENT_SESSIONS = 15` элементов. В отличие от Claude, **фильтрация по проекту делается через содержимое файла** (поле `payload.cwd` первой записи `session_meta`), а не через имя папки.

**Аргументы:**
- `project_dir` (`str`) — абсолютный путь к директории проекта (используется как фильтр при сравнении `payload.cwd == project_dir`)

**Возвращает:** `list[SessionFileInfo]`. Каждый элемент содержит `session_id` (UUID сессии — `payload.id` из `session_meta`, либо извлечённый из имени файла), `file_path` (абсолютный путь к JSONL), `last_modified_at` (значение `os.path.getmtime`), `preview` (первое настоящее сообщение пользователя, обрезанное до `PREVIEW_MAX_LENGTH = 120` символов).

Алгоритм:
1. Сформировать корень: `sessions_root = ~/.codex/sessions`
2. Обойти YYYY/MM/DD-папки за последние `LOOKBACK_DAYS_FOR_SESSION_LISTING = 30` дней (от текущего локального дня в обратную сторону), пропуская несуществующие
3. Собрать все `rollout-*.jsonl` файлы из этих папок
4. Для каждого файла прочитать первые `MAX_LINES_FOR_PREVIEW = 50` строк (асинхронно через `asyncio.to_thread`), распарсить, найти запись `type == "session_meta"`, проверить `payload.cwd == project_dir`. Если cwd не совпадает — пропустить файл
5. Для оставшихся: отсортировать по `os.path.getmtime` в убывающем порядке, взять первые `MAX_RECENT_SESSIONS = 15`
6. Для каждого выжившего файла: извлечь `session_id` (из `payload.id` записи `session_meta`), извлечь preview (первое user-сообщение из `response_item` с `role == "user"`)

**Исключения:** не выбрасывает. Все ошибки обрабатываются:
- Папка `~/.codex/sessions` не существует — возвращает `[]` и логирует `info` (норма для пользователя без активности в Codex)
- Папка `YYYY/MM/DD` не существует — пропускается без лога (норма для дней без сессий)
- Ошибка чтения отдельного файла (`OSError`, `PermissionError`, `EDEADLK` errno 11) — пропускает файл, логирует `warning`, продолжает
- Файл не содержит `session_meta` (например, Codex упал при создании) — пропускается, логирует `debug`

**Async** — потому что чтение десятков директорий и сотен файлов блокирует event loop на сотни миллисекунд. Все блокирующие I/O-операции выполняются через `asyncio.to_thread`.

#### `list_all_session_files_for_project(project_dir) -> list[SessionFileInfo]`

```python
async def list_all_session_files_for_project(
    self, project_dir: str
) -> list[SessionFileInfo]: ...
```

Возвращает все JSONL-файлы сессий Codex для данного проекта, отсортированные от свежих к старым, без ограничения `MAX_RECENT_SESSIONS = 15` и без ограничения `LOOKBACK_DAYS_FOR_SESSION_LISTING = 30`. Это operational API для watcher-а, pending delivery, reset state и ownership-проверок; UI-команда `/sessions` продолжает использовать `list_session_files_for_project`.

**Аргументы:**
- `project_dir` (`str`) — абсолютный путь к директории проекта, сравнивается с `payload.cwd` записи `session_meta`

**Возвращает:** `list[SessionFileInfo]` для всех подходящих rollout-файлов проекта. Каждый элемент содержит `session_id`, `file_path`, `last_modified_at`, `preview`.

**Исключения:** не выбрасывает. Ошибки чтения отдельных папок и файлов логируются и пропускаются, чтобы один повреждённый rollout не останавливал operational scan.

#### `read_messages_from_session_file(file_path) -> list[SessionMessage]`

```python
async def read_messages_from_session_file(
    self, file_path: str
) -> list[SessionMessage]: ...
```

Читает все сообщения (user + assistant) из JSONL-файла сессии и возвращает унифицированный список.

**Аргументы:**
- `file_path` (`str`) — абсолютный путь к JSONL-файлу сессии

**Возвращает:** `list[SessionMessage]`. Хронологический порядок (как в файле). Каноническим источником текста ассистента и пользователя в Codex являются записи `response_item` (а не `event_msg`-дубликаты — те служат для трансляции через wire-протокол, а файл хранит обе формы для разных целей). Алгоритм извлечения:

Для каждой записи `RolloutLine = {timestamp, type, payload}`:
- Если `type != ROLLOUT_TYPE_RESPONSE_ITEM` (строка `"response_item"`) — пропустить (не сообщение пользователя/ассистента)
- Из payload: если `payload.get("type") != "message"` — пропустить (это reasoning, function_call, function_call_output — служебные блоки)
- Извлечь `role = payload.get("role")`. Допустимые для нас значения: `"user"`, `"assistant"`. Остальные (`"developer"`, `"system"`) пропускаются — это автоматические системные сообщения (instructions из AGENTS.md, permissions instructions), которые пользователь не писал
- Извлечь текст: `text = _extract_text_from_content_blocks(payload.get("content", []))` — для `role: "user"` берётся `content[].type == "input_text"` поле `text`, для `role: "assistant"` берётся `content[].type == "output_text"` поле `text`. Если несколько блоков — конкатенируются через перенос строки
- Создать `SessionMessage(role=role, text=text, timestamp=parse_iso_to_unix(record["timestamp"]), is_empty_response=False)`. `is_empty_response` для Codex всегда `False` — у него нет синтетических маркеров пустого ответа (см. `text_markers_indicating_empty_response`)

**Исключения:** не выбрасывает. Файл не существует или нет прав на чтение — возвращает `[]`, логирует `warning`/`debug`. Невалидная JSON-строка внутри файла — пропускается, логируется `warning` (с номером строки), чтение продолжается.

**Async** — по тем же причинам, что и `list_session_files_for_project`. Все блокирующие операции через `asyncio.to_thread`.

#### `text_markers_indicating_empty_response() -> frozenset[str]`

```python
def text_markers_indicating_empty_response(self) -> frozenset[str]: ...
```

Возвращает множество строк, которые Codex CLI использует как синтетические маркеры пустого ответа. **Для Codex — пустое множество.** Эмпирически проверено в концепции 06-38 и в исходниках Rust: у Codex нет аналога Claude-маркера `"No response requested."`. Если Codex по какой-то причине не выдаёт текст в `agent_message` — поле `text` будет пустой строкой или `agent_message` событие отсутствует совсем.

**Возвращает:** `frozenset()` (пустой frozenset).

#### `event_types_meaning_cli_is_busy() -> frozenset[str]`

```python
def event_types_meaning_cli_is_busy(self) -> frozenset[str]: ...
```

Возвращает множество значений верхнего поля `type` записей JSONL в файле сессии, которые означают «CLI всё ещё работает над текущим turn-ом — не помечать сообщения как финальные». Используется `session_watcher` при чтении JSONL-файлов в реальном времени.

**Возвращает:** `frozenset({"event_msg", "response_item", "turn_context", "compacted"})`.

Состав множества — все возможные значения `RolloutItem.type` (`protocol/src/protocol.rs:2807-2815`) **кроме** `session_meta`. `session_meta` записывается один раз в начале сессии и не присутствует на «хвосте» файла во время активного turn-а — его наличие как последней записи означало бы, что сессия только что создана и пуста (что не «busy», но и не финальный ответ).

**Семантика для `session_watcher`.** В отличие от Claude, где конкретный `type` записи однозначно говорит о состоянии turn-а (`assistant` = busy, `result` = финал), в Codex решение «busy/завершён» делается **по подтипу**, а не по верхнему `type`. Watcher обязан после получения списка `SessionMessage` дополнительно проверить: если последняя запись в файле — `event_msg.payload.type == EVENT_MSG_SUBTYPE_TASK_COMPLETE` (строка `"task_complete"`), turn завершён. Иначе — turn ещё идёт. Эта дополнительная проверка делается через метод `is_turn_terminal_session_record(record: dict) -> bool` (см. ниже).

#### `is_turn_terminal_session_record(record: dict) -> bool`

```python
def is_turn_terminal_session_record(self, record: dict) -> bool: ...
```

Метод родительского интерфейса `CodingAgentBackend` (см. `coding_agent_backend_spec.md`). Используется `session_watcher` для определения, что turn в файле сессии завершён штатно. Watcher вызывает метод единообразно для всех бэкендов — никаких `isinstance` или проверок `backend.name` не требуется.

**Аргументы:**
- `record` (`dict`) — одна распарсенная запись JSONL из файла сессии Codex (формат `RolloutLine`)

**Возвращает:** `bool`. `True` если запись — `event_msg` с подтипом `task_complete` (`protocol.rs:1351-1357`, serde rename `TurnComplete -> task_complete`). Иначе `False`. Подтипы `token_count` (служебная статистика, приходит и до, и после `task_complete`), `error`, `turn_aborted` (ошибочное завершение, отдельная семантика, обрабатывается через `is_error_event` / `read_terminal_status_from_event`) — возвращают `False`.

**Контекст решения.** Метод изначально планировался как Codex-only расширение: предполагалось, что `session_watcher` будет вызывать его через `isinstance` или `backend.name == BackendName.CODEX`, а Claude-watcher обойдётся проверкой `record.get("type") == "result"` напрямую. После уточнения ревью 06-05 это решение пересмотрено — метод поднят в родительский интерфейс, чтобы устранить ветвление в потребителе и не закладывать LSP-нарушение, которое масштабируется на каждый новый бэкенд. У Claude этот метод реализуется тривиально (`record.get("type") == "result"`, см. `claude_code_backend_spec.md`), но реализуется обязательно. Подробнее — раздел «Расхождение с концепцией от 06-05-2026» в `coding_agent_backend_spec.md` и одноимённый раздел в этой спеке выше.

### Расширения интерфейса для snapshot, terminal status и stop strategy

Эти методы соответствуют расширениям родительского интерфейса `CodingAgentBackend`, согласованным после ревью спек 06-05 (см. `dev/docs/session-reports/06-05/14-54_codex-specs-review-undone-items.md`, разделы 2, 3, 4). Расширения нужны, чтобы потребители (`session_watcher`, `process_manager`) могли работать с обоими бэкендами одинаково: получать snapshot файла сессии с raw-счётчиком и индикатором активности turn-а, отличать ошибочное завершение от штатного, единообразно останавливать процесс многошаговой эскалацией сигналов. Ниже описывается **только Codex-семантика** этих методов; типы DTO (`SessionFileSnapshot`, `TerminalStatus`, `StopStrategy`, `StopSignalStep`) объявляются в родительской спеке `coding_agent_backend_spec.md` и импортируются Codex-бэкендом.

#### `read_session_file_snapshot(file_path) -> SessionFileSnapshot`

```python
async def read_session_file_snapshot(
    self, file_path: str
) -> SessionFileSnapshot: ...
```

Возвращает snapshot файла сессии: список сообщений плюс служебные поля для `session_watcher`. Заменяет прямое использование `read_messages_from_session_file` в watcher-е (тот метод остаётся публичным API для `session_reader` и `/sessions`, где snapshot-поля не нужны).

**Аргументы:**
- `file_path` (`str`) — абсолютный путь к JSONL-файлу сессии Codex (формат `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`)

**Возвращает:** `SessionFileSnapshot` (DTO из родительской спеки) со следующими полями для Codex:
- `messages` (`list[SessionMessage]`) — то же, что возвращает `read_messages_from_session_file` для этого файла. Хронологический порядок. Источник — `response_item` записи (см. «Расхождения» → канонический источник)
- `raw_record_count` (`int`) — количество строк JSONL в файле, **включая невалидные и служебные** (`session_meta`, `event_msg` всех подтипов, `turn_context`, `compacted`, `response_item`). Watcher использует это число для дельта-чтения: если число выросло с прошлого опроса — появились новые записи, нужно пересчитать. Подсчёт делается по сырым строкам (после удаления только пустых), а не по распарсенным записям, чтобы счётчик был стабильным даже в момент партиальной записи последней строки CLI-процессом
- `last_record` (`UnifiedEvent | None`) — последняя валидная распарсенная запись JSONL (формат `RolloutLine = {timestamp, type, payload}`), либо `None` если файл пуст или ни одна строка не парсится. Watcher использует поле для определения `is_turn_active` без повторного чтения файла; `process_manager` может использовать его для аудит-логов
- `is_turn_active` (`bool`) — `True` если turn ещё идёт (CLI-процесс пишет в файл), `False` если turn завершён или файл новый/пустой. **Семантика для Codex:** `False` тогда и только тогда, когда последняя валидная запись файла — `event_msg` с подтипом `task_complete` (штатное завершение turn-а, проверяется через `is_turn_terminal_session_record(last_record) == True`) **либо** `event_msg` с подтипом из множества `EVENT_MSG_TERMINAL_FAILURE_SUBTYPES = frozenset({"error", "turn_aborted"})` (отказ или принудительное прерывание, реально записанные в файл). **`token_count` НЕ считается завершающим маркером** — это служебное обновление статистики, оно приходит и до, и после `task_complete`. **Если последняя запись — `response_item` с `phase == "final_answer"`** или любая другая запись из `BUSY_ROLLOUT_TYPES`, и при этом `task_complete` ещё не записан — `is_turn_active = True`. Если файл пустой или вся файл состоит из не-парсящихся строк — `is_turn_active = False` (нет активности, watcher не помечает ничего)

**Исключения:** не выбрасывает; ошибки чтения и парсинга обрабатываются так же, как в `read_messages_from_session_file` (warning-лог, пустой snapshot со счётчиком 0 и `is_turn_active = False`).

**Async** — по тем же причинам, что и `read_messages_from_session_file`. Все блокирующие I/O через `asyncio.to_thread`.

#### `is_error_event(event) -> bool`

```python
def is_error_event(self, event: UnifiedEvent) -> bool: ...
```

Возвращает `True`, если событие означает ошибочное завершение turn-а (Codex CLI отдал финальное событие, но оно несёт ошибку, а не успешный ответ). Используется `process_manager` для решения о retry: явный флаг `is_error == True` приводит к перезапросу или к доставке текста ошибки пользователю — без эвристики «пустой текст значит ошибка».

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `bool`. `True` тогда и только тогда, когда `event.get("type") == STDOUT_EVENT_TYPE_TURN_FAILED` (строка `"turn.failed"`). В остальных случаях (`turn.completed`, `item.completed`, `thread.started`, `error` верхнего уровня и любые нефинальные) — `False`.

**Семантика Codex-протокола:** в отличие от Claude (где финальное событие одно — `result` с булевым флагом `is_error`), у Codex финал бывает двух разных типов: `turn.completed` (успех) и `turn.failed` (ошибка). Метод `is_error_event` смотрит именно на `type == "turn.failed"`. Дополнительно у Codex существует событие верхнего уровня `error` (`{type: "error", message: ...}`) — оно НЕ считается терминальным финалом turn-а в этой спеке, потому что Codex продолжает после него работать (внутреннее уведомление); если оно действительно завершает turn — следом придёт `turn.failed`, который и обработает `is_error_event`.

#### `read_error_text_from_event(event) -> str | None`

```python
def read_error_text_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает текст ошибки из события `turn.failed`. Используется `process_manager` для логирования и для отправки внятного сообщения пользователю при невозможности retry.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `str | None`.
- Если `is_error_event(event) is False` — возвращает `None`
- Если событие — `turn.failed`: возвращает `event.get("error", {}).get("message")`. Структура `error: {message: String}` зафиксирована в `exec_events.rs:88-91`. Если поле отсутствует или пустое — `None`

В отличие от Claude (где успешный текст и текст ошибки лежат в одном поле `result`, а различает их флаг `is_error`), у Codex текст ошибки лежит в отдельном поле `event["error"]["message"]` события `turn.failed`. Backend-neutral интерфейс скрывает это различие за двумя методами (`read_assistant_text_from_event` для успеха, `read_error_text_from_event` для ошибки).

#### `read_terminal_status_from_event(event) -> TerminalStatus | None`

```python
def read_terminal_status_from_event(self, event: UnifiedEvent) -> TerminalStatus | None: ...
```

Возвращает обобщённый терминальный статус turn-а для финального события. `None` для нефинальных событий. Используется `process_manager` для записи итога turn-а в логи и для решения о retry, когда удобнее работать с enum-статусом, чем с парой булевых флагов (`is_turn_complete_event` + `is_error_event`).

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `TerminalStatus | None` (enum из родительской спеки со значениями как минимум `SUCCESS` и `FAILED`).
- Если `is_turn_complete_event(event) is False` — возвращает `None`
- Если `event.get("type") == STDOUT_EVENT_TYPE_TURN_FAILED` — возвращает `TerminalStatus.FAILED`
- Если `event.get("type") == STDOUT_EVENT_TYPE_TURN_COMPLETED` — возвращает `TerminalStatus.SUCCESS`

В отличие от Claude, у Codex статус определяется по `type` события, а не по флагу внутри одного финального типа. Этот метод инкапсулирует разницу.

#### `get_stop_strategy() -> StopStrategy`

```python
def get_stop_strategy(self) -> StopStrategy: ...
```

Возвращает стратегию остановки процесса Codex CLI. Используется `process_manager` при обработке `/stop` и при принудительном завершении по таймауту.

**Аргументы:** нет.

**Возвращает:** `StopStrategy` (DTO из родительской спеки). Для Codex стратегия — **многошаговая эскалация SIGINT → SIGTERM → SIGKILL** через `steps: tuple[StopSignalStep, ...]`:

- **Шаг 1** — `StopSignalStep(signal_to_send=signal.SIGINT, wait_seconds_before_next=STOP_SIGINT_TIMEOUT_SECONDS = 5)`. SIGINT — единственный сигнал, который Codex CLI обрабатывает специально (`exec/src/lib.rs:741-843`, `tokio::signal::ctrl_c()`). При получении SIGINT Codex отправляет на сервер `ClientRequest::TurnInterrupt`, дописывает в файл сессии запись штатного прерывания и инициирует shutdown. 5 секунд достаточно для типового шатдауна (обычно укладывается в 1-2 секунды), запас — на случай долгого сетевого вызова
- **Шаг 2** — `StopSignalStep(signal_to_send=signal.SIGTERM, wait_seconds_before_next=STOP_SIGTERM_TIMEOUT_SECONDS = 5)`. Fallback на случай, если SIGINT-handler Codex по какой-то причине завис (зависимая ошибка, deadlock в Tokio runtime). SIGTERM завершает Tokio runtime через стандартный shutdown, без `TurnInterrupt`-семантики, но процесс точно покинет память. 5 секунд согласованы с родительской стратегией остановки
- **Финальный шаг** — `StopSignalStep(signal_to_send=signal.SIGKILL, wait_seconds_before_next=0.0)`. SIGKILL гарантирует завершение процесса; `wait_seconds_before_next=0.0` означает «не ждать после этого сигнала» — после `process.kill()` процесс снимается ядром безусловно

**Семантика Codex-протокола:** SIGTERM **не эквивалентен** SIGINT для Codex — это эмпирически проверено (см. «Контракты с внешними системами → Codex CLI — обработка сигналов»). Tokio runtime обрабатывает SIGINT через `tokio::signal::ctrl_c()` (явный handler с `TurnInterrupt`), а SIGTERM — через стандартный shutdown без отправки `TurnInterrupt`. Разница важна для `session_watcher`: после SIGINT в файле сессии останется запись о штатном прерывании (turn помечен как завершённый), после SIGTERM такой записи может не быть, и watcher не увидит маркера завершения. Поэтому первый сигнал **обязан** быть SIGINT.

**Контрактный тест.** Эмпирическая проверка стратегии — в разделе «Тест-план → Контрактные тесты с реальным CLI» (`test_codex_sigint_records_turn_interrupt_in_session_file`): запускается долгий Codex turn (например, «прочитай большой файл и резюмируй»), через 2 секунды ему отправляется SIGINT, проверяется что (а) процесс завершается в течение `STOP_SIGINT_TIMEOUT_SECONDS`, (б) в файле сессии последняя запись — `event_msg` с подтипом `task_complete` или `turn_aborted` (не оборванный turn), (в) `is_turn_terminal_session_record` для этой записи возвращает `True` (либо subtype попадает в `EVENT_MSG_TERMINAL_FAILURE_SUBTYPES`).

### Локальная фабрика модуля

Конкретный класс `CodexBackend` создаётся через фабрику `get_backend(BackendName.CODEX)` из родительского модуля `coding_agent_backend`. Внутри родительской фабрики — lazy import: `from claude_manager.codex_backend import CodexBackend`. Сам этот модуль публичной фабрики не предоставляет — это прерогатива интерфейса.

Для удобства тестов и прямого использования (минуя фабрику) класс `CodexBackend` экспортируется из модуля и может быть инстанцирован напрямую — он stateless, повторное создание не вредит.

## Внутренние функции

### `_resolve_codex_binary_path() -> str`

Lazy-резолвер пути к бинарнику `codex`. Возвращает `shutil.which("codex") or CODEX_CLI_DEFAULT_PATH`. Если ни `which`, ни путь по умолчанию (`~/.npm-global/bin/codex` — фактическое место установки на dev-машине, см. концепцию 06-38) не находят бинарник — выбрасывает `BackendBinaryNotFoundError`. Вызывается из `compose_subprocess_command_args`, не из `__init__` класса.

### `_extract_text_from_content_blocks(content_blocks: list) -> str`

Извлекает текст из списка content-блоков `response_item`. Для каждого блока:
- Если `block.get("type") == "input_text"` — взять `block["text"]`
- Если `block.get("type") == "output_text"` — взять `block["text"]`
- `input_image` и другие типы — пропустить (бот не показывает превью изображений в `/sessions`)

Результат — строки, разделённые `"\n"` (если блоков несколько). Если блоков нет или все — не текстовые — возвращает пустую строку.

### `_clean_preview_text(raw_text: str) -> str`

Очищает превью: сжимает любые подряд идущие whitespace-символы в один пробел (паттерн `\s+`), обрезает до `PREVIEW_MAX_LENGTH = 120` символов с добавлением `...` при обрезке. В отличие от Claude-версии, **не удаляет XML-теги** — у Codex нет аналога `<command-name>`-тегов в response_item; в текстах могут быть пользовательские XML-конструкции, которые надо сохранить.

### `_iter_session_dirs_in_lookback_window(sessions_root: str, today: date, lookback_days: int) -> Iterator[str]`

Генератор путей к папкам `YYYY/MM/DD/` за последние `lookback_days` дней от `today` (включительно, в обратном порядке — самые свежие первые). Пропускает несуществующие папки. `today` принимается параметром (а не `date.today()` внутри функции) для тестируемости.

### `_read_session_meta_record_blocking(file_path: str) -> dict | None`

Блокирующая функция: открывает файл, читает первые `MAX_LINES_FOR_PREVIEW = 50` строк, парсит каждую через `json.loads`, возвращает первую запись с `type == "session_meta"`. Если такой нет — возвращает `None`. Невалидные строки пропускаются молча (партиальная запись в момент создания сессии — норма). Вызывается через `asyncio.to_thread`.

### `_read_first_user_response_item_blocking(file_path: str) -> dict | None`

Блокирующая функция: читает первые `MAX_LINES_FOR_PREVIEW = 50` строк, ищет первую запись с `type == "response_item"` и `payload.role == "user"` и `payload.type == "message"`. Возвращает `payload.content` (список content-блоков) или `None`. Вызывается через `asyncio.to_thread`.

### `_extract_uuid_from_rollout_filename(file_path: str) -> str | None`

Извлекает UUID из имени файла `rollout-YYYY-MM-DDTHH-MM-SS-<UUID>.jsonl`. Регулярка `re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")`. Используется как fallback, если первая запись `session_meta` не нашлась или повреждена.

### `_parse_iso_timestamp_to_unix(iso_string: str) -> float | None`

Парсит ISO-8601 timestamp (формат Codex: `"2026-05-06T01:35:14.505Z"`) в unix-timestamp `float`. При неудаче — возвращает `None`. Используется для поля `timestamp` в `SessionMessage`.

### `_parse_jsonl_string_lines(raw_lines: list[str], file_path: str) -> list[dict]`

Парсит список сырых строк JSONL в список словарей. Пустые строки пропускаются. Невалидные строки логируются (`warning` с номером строки и путём файла) и пропускаются. Идентична функции в `claude_code_backend.py` — переносится без изменений (или импортируется из общего helper-модуля при рефакторинге, что — задача отдельного решения, не этой спеки).

### `_read_file_lines_blocking(file_path: str, max_lines: int | None) -> list[str]`

Блокирующая функция чтения строк файла. Если `max_lines is None` — читает все строки, иначе — первые `max_lines`. Вызывается через `asyncio.to_thread`. Идентична функции в `claude_code_backend.py`.

### `_list_rollout_files_blocking(sessions_root: str, lookback_days: int, today: date) -> list[str]`

Блокирующая функция: возвращает список абсолютных путей всех файлов с шаблоном `rollout-*.jsonl` в папках `YYYY/MM/DD/` за окно lookback от `today`. Использует `_iter_session_dirs_in_lookback_window` для перебора папок, `os.listdir` для каждой папки, фильтрует по префиксу `rollout-` и расширению `.jsonl`. Вызывается через `asyncio.to_thread`.

### `_sort_paths_by_mtime_descending(file_paths: list[str]) -> list[str]`

Сортирует пути по `os.path.getmtime` в убывающем порядке (новые первые). Блокирующая, через `asyncio.to_thread`. Идентична функции в `claude_code_backend.py`.

### `_compute_is_turn_active_for_codex(last_record: dict | None) -> bool`

Определяет значение `is_turn_active` для snapshot Codex-файла. Логика — единственное место, где принимается это решение, чтобы не дублировать ветвления в `read_session_file_snapshot`. Возвращает `False` если:
1. `last_record is None` (файл пустой или не парсится)
2. `last_record.get("type") == ROLLOUT_TYPE_EVENT_MSG` и `last_record.get("payload", {}).get("type") == EVENT_MSG_SUBTYPE_TASK_COMPLETE` (штатное завершение)
3. `last_record.get("type") == ROLLOUT_TYPE_EVENT_MSG` и `last_record.get("payload", {}).get("type") in EVENT_MSG_TERMINAL_FAILURE_SUBTYPES` (отказ или принудительное прерывание реально записаны в файле)

В остальных случаях — `True`. Для записей `event_msg` с подтипом `token_count` функция явно возвращает `True` (turn ещё активен — `token_count` не маркер завершения, см. «Расхождения с концепцией»).

### `_count_raw_jsonl_lines_blocking(file_path: str) -> int`

Блокирующая функция: открывает файл, считает количество **непустых** строк (после `rstrip("\n")` и проверки на не-пустоту). Не парсит содержимое — это нужно для стабильного `raw_record_count` в snapshot, который не должен меняться от того, валидна ли последняя строка JSON или нет (Codex может писать строки атомарно по `\n`, но между записями файл может содержать частично записанную последнюю строку). Вызывается через `asyncio.to_thread`. Используется в `read_session_file_snapshot`.

### `_find_last_valid_record_blocking(file_path: str) -> dict | None`

Блокирующая функция: читает все строки файла, парсит каждую через `json.loads` с обработкой исключений, возвращает **последнюю успешно распарсенную** запись. Если ни одна не парсится — `None`. Используется в `read_session_file_snapshot` для заполнения поля `last_record`. Невалидные строки пропускаются молча (это норма — последняя строка может быть в момент записи Codex'ом). Вызывается через `asyncio.to_thread`.

## Алгоритм работы

### compose_subprocess_command_args

1. Резолвить путь к бинарнику через `_resolve_codex_binary_path()`. Если бинарник не найден — выбросить `BackendBinaryNotFoundError`
2. Собрать общие флаги headless-режима: `["--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"]`
3. Если `session_id is None`:
   - Сформировать аргументы новой сессии: `[binary_path, "exec"] + common_flags + ["-C", cwd, prompt_text]`
4. Если `session_id is not None`:
   - Сформировать аргументы resume: `[binary_path, "exec", "resume", session_id] + common_flags + [prompt_text]`
   - Аргумент `cwd` игнорировать (resume не имеет `-C`; cwd задаётся через subprocess `cwd=`)
5. `image_paths` игнорировать на этом этапе (контракт текущей версии бэкенда — путь к фото идёт текстом в `prompt_text`)
6. Вернуть итоговый список

### encode_user_message_for_cli_stdin

1. Игнорировать оба аргумента
2. Вернуть `b""`

### parse_stdout_line_into_event

1. Если `raw_line.strip()` пустая — вернуть `None`
2. Попытаться распарсить через `json.loads(raw_line)`. При успехе — вернуть результат как `UnifiedEvent`
3. При `json.JSONDecodeError` — выбросить `BackendProtocolError(f"Невалидный JSON от Codex: '{raw_line[:200]}'")`

### is_turn_complete_event

1. Вернуть `event.get("type") in STDOUT_TURN_TERMINAL_EVENT_TYPES` (множество `{"turn.completed", "turn.failed"}`)

### read_session_id_from_event

1. Если `event.get("type") != STDOUT_EVENT_TYPE_THREAD_STARTED` — вернуть `None`
2. Иначе вернуть `event.get("thread_id")` (строка-UUID или `None`, если поле отсутствует)

### read_assistant_text_from_event

1. Если `event.get("type") != STDOUT_EVENT_TYPE_ITEM_COMPLETED` — вернуть `None`
2. `item = event.get("item")`. Если не словарь или None — вернуть `None`
3. Если `item.get("type") != ITEM_TYPE_AGENT_MESSAGE` — вернуть `None`
4. Вернуть `item.get("text")` (строка или `None`, если поле отсутствует — что не должно происходить по контракту Codex, но защита от регрессии)

### read_progress_text_from_event

1. Если `event.get("type") != STDOUT_EVENT_TYPE_ITEM_COMPLETED` — вернуть `None`
2. `item = event.get("item")`. Если не словарь — вернуть `None`
3. Если `item.get("type") != ITEM_TYPE_REASONING` — вернуть `None`
4. Вернуть `item.get("text")` (reasoning summary)

### locate_session_files_directory_for_project

1. Получить домашнюю директорию: `home_dir = os.path.expanduser("~")`
2. Собрать абсолютный путь: `os.path.join(home_dir, ".codex", "sessions")`
3. Аргумент `project_dir` игнорировать
4. Вернуть строку

### list_session_files_for_project

1. Вычислить `sessions_root = locate_session_files_directory_for_project(project_dir)` — игнорирует `project_dir`, возвращает `~/.codex/sessions`
2. Через `asyncio.to_thread(os.path.exists, sessions_root)` проверить существование. Если не существует — залогировать `info`, вернуть `[]`
3. Получить сегодняшнюю дату: `today = date.today()`. Передать в helper для тестируемости
4. Через `await asyncio.to_thread(_list_rollout_files_blocking, sessions_root, LOOKBACK_DAYS_FOR_SESSION_LISTING, today)` получить список всех `rollout-*.jsonl` за окно
5. Если список пуст — вернуть `[]`
6. Для каждого файла **параллельно** через `asyncio.gather` (с ограничением concurrency через `asyncio.Semaphore(MAX_CONCURRENT_FILE_READS = 8)` при реализации, чтобы не открывать сотни файловых дескрипторов одновременно):
   - `meta_record = await asyncio.to_thread(_read_session_meta_record_blocking, file_path)`. При `OSError` (включая `EDEADLK`) — залогировать `warning`, вернуть `None`
   - Если `meta_record is None` — пропустить файл
   - Если `meta_record["payload"].get("cwd") != project_dir` — пропустить файл
   - Иначе — собрать предварительный `(file_path, meta_record)` для последующей обработки
7. Из выживших — отсортировать по mtime: `sorted_pairs = await asyncio.to_thread(_sort_pairs_by_mtime_descending, pairs)`
8. Взять первые `MAX_RECENT_SESSIONS = 15`
9. Для каждого выжившего файла **параллельно** (с тем же semaphore):
   - `session_id = meta_record["payload"].get("id")` или fallback на `_extract_uuid_from_rollout_filename(file_path)`. Если оба `None` — пропустить
   - `last_modified_at = await asyncio.to_thread(os.path.getmtime, file_path)`. При `OSError` — пропустить
   - `content_blocks = await asyncio.to_thread(_read_first_user_response_item_blocking, file_path)`. Если `None` — `preview = ""`
   - `text = _extract_text_from_content_blocks(content_blocks or [])`
   - `preview = _clean_preview_text(text)`
   - Создать `SessionFileInfo(session_id=session_id, file_path=file_path, last_modified_at=last_modified_at, preview=preview)`
10. Вернуть список `SessionFileInfo`

### list_all_session_files_for_project

1. Вычислить `sessions_root = locate_session_files_directory_for_project(project_dir)`.
2. Проверить существование `sessions_root`. Если не существует — залогировать `info`, вернуть `[]`.
3. Через `_list_all_rollout_files_blocking(sessions_root)` получить все `rollout-*.jsonl` из дерева `~/.codex/sessions/YYYY/MM/DD/` без lookback-окна.
4. Для каждого файла выполнить ту же фильтрацию по `session_meta.payload.cwd == project_dir`, что в `list_session_files_for_project`.
5. Из выживших — отсортировать по `mtime DESC`.
6. Не применять `MAX_RECENT_SESSIONS`.
7. Собрать `SessionFileInfo` для всех подходящих файлов и вернуть список.

### read_messages_from_session_file

1. Через `asyncio.to_thread(os.path.exists, file_path)` проверить существование. Если нет — залогировать `debug`, вернуть `[]`
2. Прочитать все строки: `raw_lines = await asyncio.to_thread(_read_file_lines_blocking, file_path, None)`. Поймать `PermissionError` и `OSError` — залогировать `error`, вернуть `[]`
3. Парсить: `parsed = _parse_jsonl_string_lines(raw_lines, file_path)`
4. Преобразовать в список `SessionMessage`:
   - Для записи `RolloutLine = {timestamp, type, payload}`:
     - Если `type != "response_item"` — пропустить (включая `event_msg`-дубликаты, `session_meta`, `turn_context`, `compacted`)
     - Если `payload.get("type") != "message"` — пропустить (служебные блоки reasoning/function_call/function_call_output)
     - `role = payload.get("role")`. Если `role not in {"user", "assistant"}` — пропустить (system-/developer-сообщения не показываются)
     - `text = _extract_text_from_content_blocks(payload.get("content", []))`
     - `ts = _parse_iso_timestamp_to_unix(record.get("timestamp", ""))`
     - Создать `SessionMessage(role=role, text=text, timestamp=ts, is_empty_response=False)`
5. Вернуть список `SessionMessage` в исходном порядке (Codex пишет записи строго хронологически)

### text_markers_indicating_empty_response

1. Вернуть статическое `frozenset()` (пустое множество)

### event_types_meaning_cli_is_busy

1. Вернуть статическое `frozenset({"event_msg", "response_item", "turn_context", "compacted"})`

### is_turn_terminal_session_record

1. Если `record.get("type") != "event_msg"` — вернуть `False`
2. Если `record.get("payload", {}).get("type") != EVENT_MSG_SUBTYPE_TASK_COMPLETE` (строка `"task_complete"`) — вернуть `False`
3. Иначе — вернуть `True`

### read_session_file_snapshot

1. Через `asyncio.to_thread(os.path.exists, file_path)` проверить существование. Если нет — залогировать `debug`, вернуть `SessionFileSnapshot(messages=[], raw_record_count=0, last_record=None, is_turn_active=False)`
2. Параллельно через `asyncio.gather` (на одном файле — три независимых задачи):
   - `messages = await self.read_messages_from_session_file(file_path)` — переиспользует существующий метод (он уже обрабатывает все ошибки и возвращает `[]` при сбое)
   - `raw_record_count = await asyncio.to_thread(_count_raw_jsonl_lines_blocking, file_path)` — стабильный счётчик строк
   - `last_record = await asyncio.to_thread(_find_last_valid_record_blocking, file_path)` — последняя валидная запись или `None`
3. Вычислить `is_turn_active = _compute_is_turn_active_for_codex(last_record)`
4. Вернуть `SessionFileSnapshot(messages=messages, raw_record_count=raw_record_count, last_record=last_record, is_turn_active=is_turn_active)`

При параллельном чтении файла тремя задачами есть риск получить частично-несинхронизированный snapshot (например, строка появилась после `read_messages_from_session_file`, но до `_count_raw_jsonl_lines_blocking`). Это допустимо: `raw_record_count` использует `session_watcher` только для определения «появилось ли что-то новое», и легкий рассинхрон ≤1 строки не ломает дельта-чтение (на следующем опросе watcher всё доберёт).

### is_error_event

1. Вернуть `event.get("type") == STDOUT_EVENT_TYPE_TURN_FAILED`

### read_error_text_from_event

1. Если `is_error_event(event) is False` — вернуть `None`
2. `error_obj = event.get("error")`. Если не словарь или `None` — вернуть `None`
3. Вернуть `error_obj.get("message")` (строка или `None`, если поле отсутствует)

### read_terminal_status_from_event

1. Если `is_turn_complete_event(event) is False` — вернуть `None`
2. Если `event.get("type") == STDOUT_EVENT_TYPE_TURN_FAILED` — вернуть `TerminalStatus.FAILED`
3. Иначе (это будет `STDOUT_EVENT_TYPE_TURN_COMPLETED`, потому что `is_turn_complete_event` пропустил) — вернуть `TerminalStatus.SUCCESS`

### get_stop_strategy

1. Сформировать список шагов:
   - `StopSignalStep(signal_to_send=signal.SIGINT, wait_seconds_before_next=STOP_SIGINT_TIMEOUT_SECONDS)`
   - `StopSignalStep(signal_to_send=signal.SIGTERM, wait_seconds_before_next=STOP_SIGTERM_TIMEOUT_SECONDS)`
   - `StopSignalStep(signal_to_send=signal.SIGKILL, wait_seconds_before_next=0.0)`
2. Вернуть `StopStrategy(steps=tuple(steps))`

Метод stateless и идемпотентен — может вызываться `process_manager` многократно, всегда возвращает идентичную стратегию (значения констант фиксированы).

## Зависимости

**От модулей проекта:**
- `coding_agent_backend` — импортирует абстрактный класс `CodingAgentBackend`, enum `BackendName`, тип `UnifiedEvent`, dataclass-ы `SessionFileInfo`, `SessionMessage`, `SessionFileSnapshot`, `StopStrategy`, `StopSignalStep`, enum `TerminalStatus`, исключения `BackendBinaryNotFoundError`, `BackendProtocolError`. Потребляет: `class CodexBackend(CodingAgentBackend)` — наследование, типы из DTO — параметры/возвраты методов. **Замечание:** `SessionFileSnapshot`, `StopStrategy`, `StopSignalStep`, `TerminalStatus` объявлены в родительской спеке `coding_agent_backend_spec.md` как часть расширений интерфейса, описанных в разделе «Расхождения с концепцией от 06-05-2026» этого файла

**От стандартной библиотеки:**
- `asyncio` — `asyncio.to_thread`, `asyncio.gather` для параллельного чтения файлов
- `datetime` — `date`, `timedelta` для окна lookback по дням
- `json` — `json.loads` (парсинг событий из stdout, парсинг JSONL)
- `logging` — `logging.getLogger(__name__)` для логирования
- `os` — `os.path.expanduser`, `os.path.join`, `os.path.exists`, `os.path.getmtime`, `os.listdir`, `os.path.basename`
- `re` — `re.compile` для паттерна имени файла rollout
- `shutil` — `shutil.which` для резолва пути к бинарнику `codex`
- `signal` — константы `signal.SIGINT`, `signal.SIGTERM`, `signal.SIGKILL` для построения `StopSignalStep`-ов в `get_stop_strategy()`

**Не зависит:**
- `claude_runner.py`, `session_reader.py`, `process_manager.py` — НЕ импортируются. Codex CLI работает по принципиально другому протоколу, чем Claude CLI; общей логики для импорта нет
- `claude_code_backend.py` — НЕ импортируется. Каждый бэкенд — изолированная реализация. Если в будущем выделится общий helper (например, `_parse_jsonl_string_lines`), он переедет либо в `coding_agent_backend.py`, либо в отдельный модуль `_jsonl_helpers.py` — но это задача рефакторинга, не этой спеки

## Обработка ошибок

- **Бинарник `codex` не найден.** При первом вызове `compose_subprocess_command_args` — `_resolve_codex_binary_path` пытается `shutil.which("codex")`, при неудаче — проверить существование `~/.npm-global/bin/codex`. Если оба не сработали — выбросить `BackendBinaryNotFoundError` с сообщением: «Codex CLI не найден. Убедитесь, что 'codex' доступен в PATH или установлен через npm install -g @openai/codex». **Lazy-проверка обязательна:** проверка выполняется при первом вызове метода, а не при импорте модуля. Это требование родительской спеки: импорт `codex_backend.py` не должен падать у пользователя, у которого установлен только Claude CLI

- **Невалидный JSON в stdout (`parse_stdout_line_into_event`).** Выбросить `BackendProtocolError` с сообщением, содержащим первые 200 символов строки. Это контрактное нарушение — Codex CLI обязан выдавать валидный JSON в `--json` режиме (`event_processor_with_jsonl_output.rs:104-114`)

- **Файл сессии не существует или нет прав (`read_messages_from_session_file`).** НЕ выбрасывать. Вернуть `[]`. Залогировать `warning` (при `OSError`/`PermissionError`) или `debug` (если файл просто не существует — это норма, файл может быть удалён между листингом и чтением, гонка с Codex CLI)

- **Невалидная JSON-строка внутри файла сессии.** НЕ прерывать чтение. Пропустить строку. Залогировать `warning` с номером строки и путём файла. Codex пишет в файл, не закрывая его — чтение в момент записи может вернуть неполный JSON последней строки. `_parse_jsonl_string_lines` логирует и пропускает; следующее чтение прочтёт уже целую строку

- **Транзиентная `OSError` (включая `EDEADLK`, errno 11) при чтении папки сессий или файлов.** macOS может вернуть `EDEADLK` на обычный `read()` при высокой конкуренции процессов Codex за файлы (см. CLAUDE.md → «Транзиентная ошибка EDEADLK»). Метод `list_session_files_for_project` должен ловить `OSError`, логировать `warning`/`error` и пропускать конкретный файл — никогда не падать на весь список

- **Папка сессий не существует.** При первом запуске Codex папка `~/.codex/sessions/` создаётся самим Codex CLI. До первой сессии её может не быть. `list_session_files_for_project` возвращает `[]`, лог `info`

- **`session_meta` не найден в первых 50 строках файла.** Аномалия: Codex всегда пишет `session_meta` первой записью (`recorder.rs:1363-1393`). Если её нет в первых 50 — файл повреждён или это не rollout-файл. Пропустить файл, залогировать `debug`

- **Пользовательское сообщение для preview не найдено в первых 50 строках.** Норма: первое user-сообщение может быть сразу после `session_meta` + `turn_context` + системного `developer`-сообщения, но иногда оно отстаёт (Codex может прислать developer-инструкции в начале, до user-промпта). 50 строк достаточно практически всегда, но если нет — `preview` остаётся пустой строкой, сессия в `/sessions` показывается с номером и пустым текстом

- **`turn.failed` от Codex.** В отличие от Claude (где turn-ошибки возвращаются как `result` с булевым флагом `is_error: true`), у Codex финал ошибки приходит как **отдельный тип события** — `turn.failed` с полем `error.message`. Бэкенд явно поддерживает три метода-сигнализатора (см. «Публичный API → Расширения интерфейса»):
  - `is_turn_complete_event(event)` — `True` для `turn.completed` И `turn.failed` (оба завершают цикл чтения stdout в `process_manager`)
  - `is_error_event(event)` — `True` **только** для `turn.failed`. `process_manager` использует это как **явный флаг `is_error=True`** при формировании `SendResult`, не полагаясь на эвристику «пустой текст значит ошибка» (пустой текст бывает и при штатном завершении, если модель ничего не сказала)
  - `read_error_text_from_event(event)` — для `turn.failed` возвращает строку из `event["error"]["message"]`. Для остальных событий — `None`
  - `read_terminal_status_from_event(event)` — для `turn.failed` возвращает `TerminalStatus.FAILED`, для `turn.completed` — `TerminalStatus.SUCCESS`, для нефинальных — `None`
  
  Контракт с `process_manager`: на финальном событии `turn.failed` `process_manager` видит `is_error=True` через `is_error_event`, читает текст ошибки через `read_error_text_from_event`, и **запускает универсальный механизм retry** (тот же, что для Claude `result` с `is_error: true`); при исчерпании ретраев — отдаёт пользователю текст ошибки из `read_error_text_from_event` с префиксом `#N Ошибка ⚡ Codex` (или с дополнительной информацией из `display_name`)

## Контракты с внешними системами

### Codex CLI — формат подкоманд и флагов

**Источник правды:**
- Subcommands верхнего уровня: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/cli/src/main.rs:102-176` (enum `Subcommand`)
- Флаги `codex exec`: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/cli.rs:1-307` (`struct Cli`)
- Глобальные shared-флаги: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/utils/cli/src/shared_options.rs:1-163`
- Resume-аргументы: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/cli.rs:160-258` (`struct ResumeArgs`)

**Команда новой сессии:**
```
codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C <cwd> "<prompt>"
```

**Команда resume:**
```
codex exec resume <session_id> --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check "<prompt>"
```

**Особенности:**
- `--json` обязателен для машинно-читаемого вывода
- `--dangerously-bypass-approvals-and-sandbox` (alias `--yolo`) — без него Codex запросит интерактивное подтверждение перед каждым изменением
- `--skip-git-repo-check` — без него Codex отказывается работать в не-git директориях
- `-C <cwd>` принимается **только** на верхнем уровне `codex exec`, в `resume` его НЕТ (resume использует cwd процесса)
- Глобальные флаги `resume` (через `global = true` в Rust): `--json`, `--model`, `--dangerously-bypass-approvals-and-sandbox`, `--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `-o`. Остальные (`-s`, `-p`, `--add-dir`, `--output-schema`, `--color`) — НЕ глобальны и в resume недоступны
- Версия Codex CLI на dev-машине: 0.128.0 (`codex --version`)

### Codex CLI — формат событий stdout (--json)

**Источник правды:**
- `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/exec_events.rs:1-315` (enum `ThreadEvent`, struct `ThreadItem`, enum `ThreadItemDetails`)
- Сериализация: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs:104-114` (`println!("{}", serde_json::to_string(&event)?)`)

**Структура enum `ThreadEvent`** — `#[serde(tag = "type")]` (internally tagged, поле `type` на верхнем уровне).

**Точные JSON-формы:**

- `thread.started`:
  ```json
  {"type": "thread.started", "thread_id": "<uuid-v7>"}
  ```
  Источник: `exec_events.rs:13-14,40-43`

- `turn.started`:
  ```json
  {"type": "turn.started"}
  ```
  Источник: `exec_events.rs:16-17,46-47`

- `turn.completed`:
  ```json
  {"type": "turn.completed", "usage": {"input_tokens": N, "cached_input_tokens": N, "output_tokens": N, "reasoning_output_tokens": N}}
  ```
  Источник: `exec_events.rs:19-20,49-52,60-70`

- `turn.failed`:
  ```json
  {"type": "turn.failed", "error": {"message": "<error text>"}}
  ```
  Источник: `exec_events.rs:23-24,54-57,88-91`

- `item.started` / `item.updated` / `item.completed`:
  ```json
  {"type": "item.<state>", "item": {"id": "item_N", "type": "<kind>", ...details}}
  ```
  Источник: `exec_events.rs:26-33,72-85`

- `error`:
  ```json
  {"type": "error", "message": "<error text>"}
  ```

**Структура `ThreadItem`** — `#[serde(tag = "type", rename_all = "snake_case")]` через `#[serde(flatten)]` поле `details`. Поле `id` всегда генерируется как `format!("item_{}", N)` (`event_processor_with_jsonl_output.rs:99-101`).

**Варианты `ThreadItemDetails` (snake_case значения поля `type`):**
- `agent_message` — `{text: String}` (`exec_events.rs:134-137`). **Поле `text`**, не `message`
- `reasoning` — `{text: String}` (`exec_events.rs:140-143`)
- `command_execution` — `{command, aggregated_output, exit_code, status}` (`exec_events.rs:157-163`)
- `file_change` — `{changes, status}` (`exec_events.rs:183-186`)
- `mcp_tool_call` — `{server, tool, arguments, result, error, status}` (`exec_events.rs:279-288`)
- `collab_tool_call` — `{tool, sender_thread_id, receiver_thread_ids, prompt, agents_states, status}` (`exec_events.rs:248-256`)
- `web_search` — `{query, action}` (`exec_events.rs:291-296`)
- `todo_list` — `{items: [{text, completed}]}` (`exec_events.rs:312-314`)
- `error` — `{message: String}` (`exec_events.rs:299-301`)

**Семантика для бэкенда:**
- `is_turn_complete_event = (type in {"turn.completed", "turn.failed"})`
- `read_session_id_from_event` — только из `thread.started`, поле `thread_id`
- `read_assistant_text_from_event` — `item.completed` с `item.type == "agent_message"`, поле `item.text`
- `read_progress_text_from_event` — `item.completed` с `item.type == "reasoning"`, поле `item.text`. `agent_message` и `reasoning` НЕ эмитятся как `item.started` (`event_processor_with_jsonl_output.rs:333-341`)

### Codex CLI — формат файла сессии на диске

**Источник правды:**
- Путь: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/rollout/src/recorder.rs:1363-1393` (функция `precompute_log_file_info`)
- Корень: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/rollout/src/lib.rs:21` (константа `SESSIONS_SUBDIR = "sessions"`)
- Структура строки: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/protocol/src/protocol.rs:2957-2962` (`struct RolloutLine`)
- Варианты type: `protocol.rs:2807-2815` (enum `RolloutItem`)

**Расположение файла:** `~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDTHH-MM-SS-<UUID>.jsonl`. Дефис вместо двоеточия в имени — для совместимости с FS Windows/macOS. UUID — `ThreadId` сессии (UUID v7, не v4).

**Структура одной строки JSONL:** `RolloutLine = {timestamp, ...flatten(RolloutItem)}`. Внутренний `RolloutItem` сериализуется через `#[serde(tag = "type", content = "payload", rename_all = "snake_case")]` — adjacently tagged. Итоговый JSON:

```json
{"timestamp": "<iso>", "type": "<variant>", "payload": {...}}
```

**Варианты `type`:**
- `session_meta` — метаданные сессии. `payload`: `SessionMetaLine = SessionMeta + git`, где `SessionMeta` (`protocol.rs:2748-2776`) содержит `id` (UUID), `cwd` (путь), `timestamp`, `originator`, `cli_version`, `source`, `model_provider` и др.
- `response_item` — элемент истории (сообщения user/assistant/function_call). `payload`: `ResponseItem` (`protocol/src/models.rs:741-789`)
- `compacted` — сжатый контекст (после context_compacted)
- `turn_context` — контекст turn-а (`turn_id`, `cwd`, `model`, `sandbox_policy` и др.)
- `event_msg` — событие (все подтипы `EventMsg`)

**ResponseItem с `role: "user"`** — `payload.type == "message"`, `payload.role == "user"`, `payload.content == [{type: "input_text", text: "<пользовательский текст>"}]`. Источник: `models.rs:698-712` (`ContentItem::InputText`)

**ResponseItem с `role: "assistant"`** — `payload.type == "message"`, `payload.role == "assistant"`, `payload.content == [{type: "output_text", text: "<ответ модели>"}]`, `payload.phase == "final_answer"`. Источник: `models.rs:698-712` (`ContentItem::OutputText`), `models.rs:726-739` (`MessagePhase`)

**EventMsg-подтипы (`payload.type`)** — `enum EventMsg` использует `#[serde(tag = "type", rename_all = "snake_case")]`. Ключевые подтипы (`protocol.rs:1310-1530,1351-1357,2283-2289,2026-2041,2137-2141`):

- `task_started` — Rust `TurnStarted`, **сериализуется как `task_started`** (serde rename, `protocol.rs:1351`). Поля: `turn_id`, `started_at?`, `model_context_window?`, `collaboration_mode_kind`. **Это маркер начала turn-а в файле сессии.**
- `task_complete` — Rust `TurnComplete`, **сериализуется как `task_complete`** (serde rename, `protocol.rs:1355`). Поля: `turn_id`, `last_agent_message?`, `completed_at?`, `duration_ms?`, `time_to_first_token_ms?`. **Это маркер завершения turn-а в файле сессии — единственный надёжный способ для watcher определить, что turn закончен.**
- `agent_message` — `{message: String, phase, memory_citation}` (`protocol.rs:2283-2289`). **Поле `message`** (не `text`)! Это event-обёртка финального ответа; параллельно тот же текст пишется и в `response_item` с `role: "assistant"`. Бэкенд читает из `response_item` (см. `read_messages_from_session_file`)
- `user_message` — `{message}`. Дубликат пользовательского сообщения; бэкенд читает из `response_item`
- `agent_reasoning` — reasoning ассистента
- `agent_message_delta` — потоковые куски агентского сообщения
- `exec_command_begin` / `exec_command_output_delta` / `exec_command_end` — выполнение shell-команд
- `patch_apply_begin` / `patch_apply_end` — применение патчей
- `mcp_tool_call_begin` / `mcp_tool_call_end` — MCP-вызовы
- `view_image_tool_call` — `{call_id, path}` — вызов встроенного `view_image` инструмента
- `token_count` — обновление статистики токенов (`protocol.rs:2137-2141`). **НЕ означает завершения turn-а** — это служебное событие, может приходить и во время, и после
- `error` / `warning` — ошибки и предупреждения
- `session_configured` — однократное в начале при подключении модели
- `context_compacted` / `shutdown_complete` — управление сессией
- `plan_update` — обновление to-do списка
- `turn_aborted` — прерывание turn-а

**КРИТИЧНО:** в файле сессии для `event_msg.payload.type == "agent_message"` поле текста — `message`, а в stdout `--json` для `item.completed.item.type == "agent_message"` поле текста — `text`. Это **разные struct-ы** (`AgentMessageEvent` vs `AgentMessageItem`); десериализаторы alias'ят их через `serde(alias = ...)`, но сериализация — каноническая.

### Codex CLI — встроенный инструмент view_image

**Источник правды:**
- `~/.codex/custom-codex-rust-v0.128.0/codex-rs/core/src/tools/handlers/view_image.rs:47-187`
- `~/.codex/custom-codex-rust-v0.128.0/codex-rs/utils/image/src/lib.rs:19,68-122`

**Поведение:** Codex имеет встроенный инструмент `view_image(path, detail?)` — модель сама вызывает его, когда видит путь к изображению в тексте промпта (как Claude вызывает Read). Поддерживаемые форматы: PNG, JPEG, GIF, WebP. Изображения с шириной/высотой > 2048 px ресайзятся до 2048 (константа `MAX_DIMENSION = 2048`, `lib.rs:19`). Возвращает data URL `data:<mime>;base64,<encoded>`.

**Capability check:** если модель не поддерживает `InputModality::Image` (`view_image.rs:47-56`) — отказ с сообщением «view_image is not allowed because you do not support image inputs». Бэкенд **не делает** предварительной проверки модели — пользователь увидит ошибку в `turn.failed` или в финальном сообщении.

**Применимость к спеке:** `CodexBackend.compose_subprocess_command_args` НЕ использует флаг `-i <path>` в текущей версии (хотя он поддерживается Codex CLI через `shared_options.rs:9-17`). Изображение упоминается в `prompt_text` модулем `claude_interaction`, и Codex сам вызывает `view_image`. Поведение симметрично Claude (где путь упоминается в тексте, и Claude вызывает Read).

### Codex CLI — обработка сигналов остановки (SIGINT, SIGTERM, SIGKILL)

**Источник правды:**
- SIGINT-обработчик: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/lib.rs:741-843` (`tokio::signal::ctrl_c()`, отправка `ClientRequest::TurnInterrupt` и запись маркера прерывания)
- Tokio runtime по умолчанию: `tokio::main` устанавливает handler только на SIGINT через `ctrl_c()`. SIGTERM **не имеет** специального handler-а — Tokio останавливает runtime «обычным» способом, без вызова `TurnInterrupt`

**Точное поведение по сигналам:**

- **SIGINT (Ctrl+C, числовое значение 2)** — Codex CLI слушает через `tokio::signal::ctrl_c()`, при получении: (1) формирует `ClientRequest::TurnInterrupt` с `thread_id` и `turn_id`, (2) отправляет его на сервер модели, (3) сервер дописывает в файл сессии запись `event_msg.payload.type == "task_complete"` или `event_msg.payload.type == "turn_aborted"` (зависит от стадии turn-а), (4) Codex штатно завершает Tokio runtime и процесс. **Время полного шатдауна:** обычно 1-2 секунды, в худшем случае до 5 секунд (если был активный сетевой запрос). **Это единственный сигнал, при котором в файле сессии остаётся надёжная маркер-запись завершения turn-а**

- **SIGTERM (числовое значение 15)** — Tokio runtime получает сигнал, начинает «обычное» завершение всех async-задач. **`TurnInterrupt` НЕ отправляется**, потому что Tokio нет соответствующего handler-а. Текущий active turn в файле сессии может остаться **без записи завершения** (последняя строка — это `response_item` или `event_msg` с подтипом `agent_message_delta`, без `task_complete`). `session_watcher` не увидит маркера и оставит сообщения в «промежуточном» состоянии — пользователю не доставляется ничего, пока следующий запуск Codex не перезапишет файл

- **SIGKILL (числовое значение 9)** — ядро снимает процесс безусловно, без участия процесса. В файле сессии последняя запись остаётся как есть (что Codex успел записать до момента kill-а). `session_watcher` обращается с такой сессией как с zombie — она требует ручного `/stop`-следствия или перезапуска

**Применимость к спеке:** `get_stop_strategy()` возвращает многошаговую эскалацию `SIGINT → SIGTERM → SIGKILL` (см. «Публичный API → get_stop_strategy»). Первый шаг — SIGINT — даёт Codex время на штатный `TurnInterrupt` и запись маркера в файл сессии. Второй и третий — fallback. **`process.terminate()` (одиночный SIGTERM) — НЕ эквивалент SIGINT** для Codex и не должен использоваться как основной способ остановки. У Codex также нет CLI-флага, subcommand-а или API-эндпоинта для прерывания turn-а извне без сигналов.

### Эмпирические эксперименты, требуемые для подтверждения контрактов

Контракты с Codex CLI должны быть проверены реальными вызовами, а не догадками. Соответствующие тесты — в разделе «Тест-план → Контрактные тесты с реальным CLI». Каждый тест соответствует одному контракту:

- Команда новой сессии запускается без ошибок и возвращает `thread.started` → `test_codex_compose_args_for_new_session_runs_real_cli`
- Команда resume принимает session_id → `test_codex_resume_command_args_accept_session_id`
- `thread.started` — первое событие stdout → `test_codex_thread_started_is_first_stdout_event`
- `turn.completed` или `turn.failed` — финальное событие → `test_codex_turn_completed_is_terminal_stdout_event`
- В файле сессии `payload.cwd` соответствует `-C <cwd>` команды → `test_codex_session_meta_cwd_matches_command_cwd`
- В файле сессии есть `event_msg` с `payload.type == "task_complete"` после успешного turn-а → `test_codex_session_file_contains_task_complete`
- SIGINT — единственный сигнал, после которого в файле сессии остаётся маркер штатного прерывания (`task_complete` или `turn_aborted`) → `test_codex_sigint_records_turn_interrupt_in_session_file`
- Изображение упоминается путём в `prompt_text`, и Codex вызывает встроенный `view_image` без флага `-i` → `test_codex_view_image_path_in_prompt_text`

## Константы

Все константы определяются на уровне модуля `codex_backend.py`. Их значения зафиксированы исходниками Codex и эмпирическими наблюдениями — менять без эмпирической проверки запрещено.

- `BACKEND_DISPLAY_NAME_CODEX = "⚡ Codex"` — UI-метка бэкенда. Эмодзи ⚡ (молния) — намёк на скорость. Возвращается из свойства `display_name`. Согласовано с родительской спекой `coding_agent_backend_spec.md:533`
- `CODEX_CLI_DEFAULT_PATH` — fallback-путь к бинарнику, если `shutil.which("codex")` не нашёл в `PATH`. Значение: `os.path.expanduser("~/.npm-global/bin/codex")` — фактическое место установки на dev-машине (концепция 06-38, эмпирическая находка)
- `CODEX_SESSIONS_RELATIVE_DIR = ".codex/sessions"` — относительный путь от домашней директории к корню папки сессий. Источник: `rollout/src/lib.rs:21` (константа `SESSIONS_SUBDIR = "sessions"`) + `~/.codex` как домашний корень Codex
- `CODEX_BINARY_NAME = "codex"` — имя бинарника для `shutil.which`
- `STDOUT_EVENT_TYPE_THREAD_STARTED = "thread.started"` — тип события начала thread-а в stdout. Источник: `exec_events.rs:13`
- `STDOUT_EVENT_TYPE_TURN_STARTED = "turn.started"` — тип события начала turn-а в stdout. Источник: `exec_events.rs:16`
- `STDOUT_EVENT_TYPE_TURN_COMPLETED = "turn.completed"` — тип финального события успешного turn-а в stdout. Источник: `exec_events.rs:19`
- `STDOUT_EVENT_TYPE_TURN_FAILED = "turn.failed"` — тип финального события упавшего turn-а в stdout. Источник: `exec_events.rs:23`
- `STDOUT_EVENT_TYPE_ITEM_COMPLETED = "item.completed"` — тип события завершения item в stdout. Источник: `exec_events.rs:32`
- `STDOUT_TURN_TERMINAL_EVENT_TYPES = frozenset({STDOUT_EVENT_TYPE_TURN_COMPLETED, STDOUT_EVENT_TYPE_TURN_FAILED})` — множество финальных типов событий stdout. Возвращается из `is_turn_complete_event`
- `ITEM_TYPE_AGENT_MESSAGE = "agent_message"` — тип ThreadItem с финальным ответом ассистента. Источник: `exec_events.rs:134-137`
- `ITEM_TYPE_REASONING = "reasoning"` — тип ThreadItem с reasoning summary. Источник: `exec_events.rs:140-143`
- `ROLLOUT_TYPE_SESSION_META = "session_meta"` — тип записи RolloutLine с метаданными сессии. Источник: `protocol.rs:2807-2815`
- `ROLLOUT_TYPE_RESPONSE_ITEM = "response_item"` — тип записи RolloutLine с сообщением (user/assistant/system/developer). Источник: тот же
- `ROLLOUT_TYPE_TURN_CONTEXT = "turn_context"` — тип записи RolloutLine с контекстом turn-а
- `ROLLOUT_TYPE_COMPACTED = "compacted"` — тип записи RolloutLine со сжатым контекстом
- `ROLLOUT_TYPE_EVENT_MSG = "event_msg"` — тип записи RolloutLine с событием
- `BUSY_ROLLOUT_TYPES = frozenset({ROLLOUT_TYPE_EVENT_MSG, ROLLOUT_TYPE_RESPONSE_ITEM, ROLLOUT_TYPE_TURN_CONTEXT, ROLLOUT_TYPE_COMPACTED})` — типы записей, означающие «turn ещё идёт». Возвращается из `event_types_meaning_cli_is_busy`. Семантически: всё кроме `session_meta`
- `EVENT_MSG_SUBTYPE_TASK_COMPLETE = "task_complete"` — подтип `event_msg` для финала turn-а в файле сессии. Источник: `protocol.rs:1351-1357` (serde rename `TurnComplete -> task_complete`). Используется в `is_turn_terminal_session_record`
- `EVENT_MSG_SUBTYPE_TASK_STARTED = "task_started"` — подтип `event_msg` для начала turn-а в файле сессии. Источник: тот же
- `RESPONSE_ITEM_TYPE_MESSAGE = "message"` — подтип `response_item.payload.type` для сообщения. Источник: `models.rs:741-789`
- `RESPONSE_ITEM_ROLE_USER = "user"` — роль пользовательского сообщения
- `RESPONSE_ITEM_ROLE_ASSISTANT = "assistant"` — роль ассистентского сообщения
- `CONTENT_BLOCK_TYPE_INPUT_TEXT = "input_text"` — тип content-блока с пользовательским текстом. Источник: `models.rs:698-712`
- `CONTENT_BLOCK_TYPE_OUTPUT_TEXT = "output_text"` — тип content-блока с ассистентским текстом
- `CLI_FLAG_JSON = "--json"` — флаг включения JSONL-формата stdout
- `CLI_FLAG_BYPASS_APPROVALS = "--dangerously-bypass-approvals-and-sandbox"` — флаг отключения подтверждений
- `CLI_FLAG_SKIP_GIT_CHECK = "--skip-git-repo-check"` — флаг разрешения работы вне git-репозитория
- `CLI_FLAG_CWD = "-C"` — флаг рабочей директории (только для новой сессии)
- `CLI_SUBCOMMAND_EXEC = "exec"` — подкоманда headless-запуска
- `CLI_SUBCOMMAND_RESUME = "resume"` — sub-подкоманда возобновления (`codex exec resume`)
- `MAX_RECENT_SESSIONS = 15` — максимум сессий из UI-метода `list_session_files_for_project`. Источник: BRD CJM-05 («15 самых свежих сессий»). Согласовано с Claude (`session_reader.py:22`). Operational-метод `list_all_session_files_for_project` этот лимит не применяет.
- `PREVIEW_MAX_LENGTH = 120` — максимум символов в `preview` поле `SessionFileInfo`. Источник: BRD CJM-05 («первое сообщение пользователя, до 120 символов»)
- `MAX_LINES_FOR_PREVIEW = 50` — сколько строк JSONL читать из начала файла для извлечения `session_meta` и первого user-сообщения. Согласовано с Claude (`session_reader.py:28`)
- `LOOKBACK_DAYS_FOR_SESSION_LISTING = 30` — окно в днях для UI-метода `list_session_files_for_project` при поиске сессий проекта. Объяснение и причина значения 30 — в разделе «Расхождения с концепцией». Operational-метод `list_all_session_files_for_project` обходит всю доступную историю.
- `MAX_CONCURRENT_FILE_READS = 8` — ограничение concurrent чтения файлов в методах перечисления через `asyncio.Semaphore`. Защита от исчерпания файловых дескрипторов при сотнях rollout-файлов. Значение 8 — баланс между параллелизмом и предсказуемостью на macOS (дефолтный `ulimit -n` обычно 256, оставляем запас под другие операции бота)
- `WHITESPACE_PATTERN = re.compile(r"\s+")` — регулярка для сжатия whitespace в превью
- `ROLLOUT_FILENAME_PATTERN = re.compile(r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")` — регулярка для извлечения UUID из имени файла rollout. Источник: формат пути из `recorder.rs:1363-1393`
- `STREAM_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024` — лимит буфера StreamReader для stdout/stderr. Экспортируется для потребителя `process_manager` (использует общий лимит для обоих бэкендов; согласован с `claude_code_backend.py`). Дефолт 64 KB слишком мал для длинных reasoning-блоков и больших command output
- `READ_LINE_TIMEOUT_SECONDS = 1800` — таймаут одного `readline` на stdout. Codex может молчать до 30 минут на длинных операциях. Согласовано с Claude
- `TERMINATE_TIMEOUT_SECONDS = 5` — общий fallback-таймаут для случаев, не связанных с `/stop`-эскалацией. Используется родителем `process_manager` как глобальный watchdog, не для штатной остановки. Согласовано с Claude
- `STOP_SIGINT_TIMEOUT_SECONDS = 5` — таймаут ожидания после SIGINT перед эскалацией на SIGTERM в `get_stop_strategy()`. Значение покрывает типовой Codex-shutdown (1-2 секунды) и оставляет запас на долгий сетевой вызов. Источник: эмпирические наблюдения (`exec/src/lib.rs:741-843`, обработчик ctrl_c с отправкой `TurnInterrupt` и записью маркера в файл сессии)
- `STOP_SIGTERM_TIMEOUT_SECONDS = 5` — таймаут ожидания после SIGTERM перед эскалацией на SIGKILL. Значение согласовано с родительской стратегией остановки и даёт процессу тот же запас, что и Claude SIGTERM-shutdown
- `EVENT_MSG_SUBTYPE_TURN_ABORTED = "turn_aborted"` — подтип `event_msg` для прерванного turn-а в файле сессии. Источник: `protocol.rs` enum `EventMsg` вариант `TurnAborted`. Используется в `EVENT_MSG_TERMINAL_FAILURE_SUBTYPES` и в `_compute_is_turn_active_for_codex`
- `EVENT_MSG_SUBTYPE_ERROR = "error"` — подтип `event_msg` для записи ошибки внутри turn-а в файле сессии. Используется в `EVENT_MSG_TERMINAL_FAILURE_SUBTYPES`
- `EVENT_MSG_TERMINAL_FAILURE_SUBTYPES = frozenset({EVENT_MSG_SUBTYPE_ERROR, EVENT_MSG_SUBTYPE_TURN_ABORTED})` — множество подтипов `event_msg`, которые означают «turn завершился неудачей или прерван» **только если они реально записаны на конце файла**. Используется в `_compute_is_turn_active_for_codex` для определения `is_turn_active = False`. Не путать с `event_msg.payload.type == "task_complete"` (штатное завершение) — оно считается отдельно через `is_turn_terminal_session_record`

## Тест-план

Тесты живут в `tests/test_codex_backend.py` (юнит/edge/error) и `tests/integration/test_codex_backend_contracts.py` (контрактные интеграционные с реальным CLI).

Все тесты, помеченные «async», требуют `pytest-asyncio` (в проекте включён `asyncio_mode = "auto"`, отдельных декораторов не нужно).

### Юнит-тесты

- **test_name_returns_codex_enum** — `CodexBackend().name == BackendName.CODEX`. Тип: unit
- **test_display_name_is_lightning_emoji_codex** — `CodexBackend().display_name == "⚡ Codex"`. Тип: unit

- **test_compose_args_for_new_session_uses_exec_subcommand_and_no_resume** — `CodexBackend().compose_subprocess_command_args(None, "/tmp/proj", "hello", [])`:
  - Содержит подряд `"exec"`
  - НЕ содержит `"resume"`
  - Содержит `"--json"`, `"--dangerously-bypass-approvals-and-sandbox"`, `"--skip-git-repo-check"`
  - Содержит подряд `"-C", "/tmp/proj"`
  - Заканчивается на `"hello"` (prompt последним позиционным)
  - Тип: unit

- **test_compose_args_for_resume_session_uses_resume_subcommand_with_session_id** — `CodexBackend().compose_subprocess_command_args("uuid-123", "/tmp/proj", "hi", [])`:
  - Содержит подряд `"exec", "resume", "uuid-123"`
  - Содержит `"--json"`, `"--dangerously-bypass-approvals-and-sandbox"`, `"--skip-git-repo-check"`
  - НЕ содержит `"-C"` (для resume cwd не передаётся флагом)
  - Заканчивается на `"hi"`
  - Тип: unit

- **test_compose_args_includes_prompt_as_last_positional** — для двух разных промптов (`"banana"` и `"привет 🚀"`) — оба идут как последний элемент массива; никаких изменений в окружающих флагах. Тип: unit

- **test_compose_args_ignores_image_paths_in_current_version** — два вызова с разными `image_paths`, но одинаковыми остальными аргументами — результат идентичен (флаг `-i` не добавляется). Тип: unit

- **test_encode_user_message_returns_empty_bytes** — `CodexBackend().encode_user_message_for_cli_stdin("anything", [])` → `b""`. Тип: unit

- **test_encode_user_message_returns_empty_bytes_for_unicode_and_emojis** — `encode_user_message_for_cli_stdin("Привет 🚀", ["/tmp/x.png"])` → `b""` (всегда). Тип: unit

- **test_parse_stdout_line_returns_dict_for_valid_json** — `CodexBackend().parse_stdout_line_into_event('{"type":"thread.started","thread_id":"abc"}')` → `{"type":"thread.started","thread_id":"abc"}`. Тип: unit

- **test_parse_stdout_line_returns_none_for_empty_line** — для `""`, `"   "`, `"\t\n"` → `None`. Тип: unit

- **test_is_turn_complete_event_true_for_turn_completed** — `is_turn_complete_event({"type": "turn.completed"})` → `True`. Тип: unit

- **test_is_turn_complete_event_true_for_turn_failed** — `is_turn_complete_event({"type": "turn.failed", "error": {"message": "x"}})` → `True`. Тип: unit

- **test_is_turn_complete_event_false_for_thread_started** — `is_turn_complete_event({"type": "thread.started"})` → `False`. Тип: unit

- **test_is_turn_complete_event_false_for_item_completed** — `is_turn_complete_event({"type": "item.completed", "item": {...}})` → `False`. Тип: unit

- **test_is_turn_complete_event_false_for_empty_dict** — `is_turn_complete_event({})` → `False`. Тип: unit

- **test_read_session_id_from_event_returns_thread_id_for_thread_started** — `read_session_id_from_event({"type": "thread.started", "thread_id": "uuid-1"})` → `"uuid-1"`. Тип: unit

- **test_read_session_id_from_event_returns_none_for_other_events** — для `{"type": "turn.started"}`, `{"type": "item.completed", "item": {...}}`, `{"type": "turn.completed"}` — все возвращают `None`. Тип: unit

- **test_read_session_id_from_event_returns_none_when_thread_id_missing** — `read_session_id_from_event({"type": "thread.started"})` (без поля `thread_id`) → `None`. Тип: unit

- **test_read_assistant_text_from_item_completed_with_agent_message** — `read_assistant_text_from_event({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Готово"}})` → `"Готово"`. Тип: unit

- **test_read_assistant_text_returns_none_for_item_completed_with_reasoning** — `read_assistant_text_from_event({"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "глубже"}})` → `None`. Тип: unit

- **test_read_assistant_text_returns_none_for_turn_completed** — `read_assistant_text_from_event({"type": "turn.completed", "usage": {...}})` → `None`. Тип: unit

- **test_read_assistant_text_returns_none_for_thread_started** — `read_assistant_text_from_event({"type": "thread.started", "thread_id": "x"})` → `None`. Тип: unit

- **test_read_progress_text_from_item_completed_with_reasoning** — `read_progress_text_from_event({"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "Размышляю"}})` → `"Размышляю"`. Тип: unit

- **test_read_progress_text_returns_none_for_item_completed_with_agent_message** — `read_progress_text_from_event({"type": "item.completed", "item": {"type": "agent_message", "text": "Финал"}})` → `None`. Тип: unit

- **test_read_progress_text_returns_none_for_other_event_types** — для `{"type": "turn.started"}`, `{"type": "thread.started"}`, `{"type": "turn.completed"}` → все `None`. Тип: unit

- **test_read_progress_text_returns_none_for_item_started_and_item_updated** — для `{"type": "item.started", "item": {"type": "command_execution", ...}}` и `{"type": "item.updated", ...}` → все `None` (мы не показываем прогресс этих типов в текущей версии). Тип: unit

- **test_locate_session_files_directory_returns_codex_sessions_root** — `CodexBackend().locate_session_files_directory_for_project("/any/project")` → `os.path.expanduser("~/.codex/sessions")` (project_dir игнорируется). Тип: unit

- **test_locate_session_files_directory_ignores_project_dir** — два разных значения `project_dir` — результат идентичен. Тип: unit

- **test_text_markers_indicating_empty_response_is_empty_frozenset** — `CodexBackend().text_markers_indicating_empty_response() == frozenset()`. Тип: unit

- **test_text_markers_indicating_empty_response_returns_frozenset_type** — результат — экземпляр `frozenset` (попытка `result.add("x")` падает с `AttributeError`). Тип: unit

- **test_event_types_meaning_cli_is_busy_contains_event_msg** — `"event_msg" in CodexBackend().event_types_meaning_cli_is_busy()` → `True`. Тип: unit

- **test_event_types_meaning_cli_is_busy_does_not_contain_session_meta** — `"session_meta" not in busy_types` → `True`. Тип: unit

- **test_event_types_meaning_cli_is_busy_returns_frozenset** — результат — экземпляр `frozenset`. Тип: unit

- **test_is_turn_terminal_session_record_true_for_event_msg_task_complete** — `CodexBackend().is_turn_terminal_session_record({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "x"}})` → `True`. Тип: unit

- **test_is_turn_terminal_session_record_false_for_event_msg_task_started** — для `{"type": "event_msg", "payload": {"type": "task_started"}}` → `False`. Тип: unit

- **test_is_turn_terminal_session_record_false_for_event_msg_token_count** — для `{"type": "event_msg", "payload": {"type": "token_count", "info": {...}}}` → `False`. Тип: unit

- **test_is_turn_terminal_session_record_false_for_response_item** — для `{"type": "response_item", "payload": {...}}` → `False`. Тип: unit

- **test_is_turn_terminal_session_record_false_for_session_meta** — для `{"type": "session_meta", "payload": {...}}` → `False`. Тип: unit

- **test_is_error_event_true_for_turn_failed** — `is_error_event({"type": "turn.failed", "error": {"message": "rate limit"}})` → `True`. Тип: unit

- **test_is_error_event_false_for_turn_completed** — `is_error_event({"type": "turn.completed", "usage": {...}})` → `False`. Тип: unit

- **test_is_error_event_false_for_item_completed_and_thread_started** — для `{"type": "item.completed", "item": {...}}` и `{"type": "thread.started", "thread_id": "x"}` оба возвращают `False`. Тип: unit

- **test_is_error_event_false_for_top_level_error_event** — `is_error_event({"type": "error", "message": "x"})` → `False` (это не финал turn-а; финал — `turn.failed`). Тип: unit

- **test_read_error_text_returns_message_for_turn_failed** — `read_error_text_from_event({"type": "turn.failed", "error": {"message": "превышен лимит токенов"}})` → `"превышен лимит токенов"`. Тип: unit

- **test_read_error_text_returns_none_for_turn_completed** — `read_error_text_from_event({"type": "turn.completed", "usage": {...}})` → `None`. Тип: unit

- **test_read_error_text_returns_none_for_turn_failed_without_error_field** — `read_error_text_from_event({"type": "turn.failed"})` → `None` (поле `error` отсутствует — защита от регрессии формата). Тип: unit

- **test_read_error_text_returns_none_when_error_message_missing** — `read_error_text_from_event({"type": "turn.failed", "error": {}})` → `None`. Тип: unit

- **test_read_terminal_status_returns_failed_for_turn_failed** — `read_terminal_status_from_event({"type": "turn.failed", "error": {"message": "x"}})` → `TerminalStatus.FAILED`. Тип: unit

- **test_read_terminal_status_returns_success_for_turn_completed** — `read_terminal_status_from_event({"type": "turn.completed", "usage": {...}})` → `TerminalStatus.SUCCESS`. Тип: unit

- **test_read_terminal_status_returns_none_for_nonterminal_events** — для `{"type": "thread.started"}`, `{"type": "item.completed", "item": {...}}` → `None`. Тип: unit

- **test_get_stop_strategy_first_step_is_sigint** — `CodexBackend().get_stop_strategy().steps[0].signal_to_send == signal.SIGINT`. Тип: unit

- **test_get_stop_strategy_second_step_is_sigterm** — `steps[1].signal_to_send == signal.SIGTERM`. Тип: unit

- **test_get_stop_strategy_third_step_is_sigkill** — `steps[2].signal_to_send == signal.SIGKILL` и `steps[2].wait_seconds_before_next == 0.0` (после kill ждать нечего). Тип: unit

- **test_get_stop_strategy_sigint_timeout_matches_constant** — `steps[0].wait_seconds_before_next == STOP_SIGINT_TIMEOUT_SECONDS`. Тип: unit

- **test_get_stop_strategy_sigterm_timeout_matches_constant** — `steps[1].wait_seconds_before_next == STOP_SIGTERM_TIMEOUT_SECONDS`. Тип: unit

- **test_get_stop_strategy_returns_three_steps** — `len(steps) == 3` (SIGINT → SIGTERM → SIGKILL). Тип: unit

- **test_get_stop_strategy_is_idempotent** — два вызова `get_stop_strategy()` возвращают эквивалентные структуры (по `signal_to_send` и `wait_seconds_before_next` каждого шага). Тип: unit

### Граничные случаи

- **test_compose_args_with_empty_string_session_id** — `compose_subprocess_command_args("", "/tmp", "x", [])` — пустая строка считается ненулевым session_id, попадает в команду как `"exec", "resume", ""`. Это легитимное поведение интерфейса (валидация не входит в задачу метода). Тип: edge case

- **test_compose_args_with_cyrillic_prompt** — `compose_subprocess_command_args(None, "/tmp", "Привет 🚀", [])` — кириллица и эмодзи попадают как один аргумент списка без эскейпов. Тип: edge case

- **test_compose_args_with_path_containing_spaces** — `cwd="/tmp/my project"` — пробел сохраняется внутри одного элемента списка после `-C`, без shell-quotation. Тип: edge case

- **test_parse_stdout_line_handles_nested_json** — `parse_stdout_line_into_event('{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"x"}}')` возвращает корректную вложенную структуру. Тип: edge case

- **test_read_assistant_text_handles_missing_item_key** — `read_assistant_text_from_event({"type": "item.completed"})` (без поля `item`) → `None` (не падает с `KeyError`). Тип: edge case

- **test_read_assistant_text_handles_item_not_dict** — `read_assistant_text_from_event({"type": "item.completed", "item": "string-not-dict"})` → `None` (защита от регрессии формата). Тип: edge case

- **test_read_assistant_text_handles_missing_text_field** — `read_assistant_text_from_event({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message"}})` (без `text`) → `None`. Тип: edge case

- **test_read_progress_text_handles_missing_item_key** — `read_progress_text_from_event({"type": "item.completed"})` → `None`. Тип: edge case

- **test_read_messages_from_empty_file_returns_empty_list** — async-тест: создать пустой файл, `read_messages_from_session_file(path)` → `[]`. Тип: edge case

- **test_read_messages_extracts_user_text_from_response_item** — async-тест: JSONL с записью `{"timestamp":"2026-05-06T01:35:14.505Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Привет"}]}}` — извлечённое сообщение `SessionMessage(role="user", text="Привет", timestamp=..., is_empty_response=False)`. Тип: edge case

- **test_read_messages_extracts_assistant_text_from_response_item** — async-тест: JSONL с `{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Готово"}],"phase":"final_answer"}}` — `SessionMessage(role="assistant", text="Готово", ...)`. Тип: edge case

- **test_read_messages_skips_developer_role** — async-тест: JSONL с `{"type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"system instructions"}]}}` — пропускается, не попадает в результат. Тип: edge case

- **test_read_messages_skips_system_role** — аналогично для `role: "system"`. Тип: edge case

- **test_read_messages_skips_response_item_with_reasoning_type** — `{"type":"response_item","payload":{"type":"reasoning","summary":[],"encrypted_content":"..."}}` — пропускается (это служебный блок без role). Тип: edge case

- **test_read_messages_skips_response_item_with_function_call_type** — `{"type":"response_item","payload":{"type":"function_call","name":"view_image","arguments":"{\"path\":\"/tmp/x.png\"}","call_id":"call_abc"}}` — пропускается. Тип: edge case

- **test_read_messages_skips_event_msg_records** — `{"type":"event_msg","payload":{"type":"agent_message","message":"дубликат"}}` — пропускается, потому что бэкенд читает только `response_item` (см. алгоритм `read_messages_from_session_file`). Канонический источник текста — `response_item`. Тип: edge case

- **test_read_messages_skips_session_meta** — `{"type":"session_meta","payload":{"id":"...","cwd":"..."}}` — пропускается. Тип: edge case

- **test_read_messages_extracts_text_from_multiple_content_blocks** — async-тест: запись с `content: [{"type":"input_text","text":"первая часть"},{"type":"input_text","text":"вторая часть"}]` — текст конкатенируется через `\n`: `"первая часть\nвторая часть"`. Тип: edge case

- **test_read_messages_handles_image_content_block_gracefully** — запись с `content: [{"type":"input_text","text":"подпись"},{"type":"input_image","image_url":"data:image/png;base64,..."}]` — текст `"подпись"` (image-блок пропущен, не падает). Тип: edge case

- **test_read_messages_marks_is_empty_response_false_always** — для любой ассистентской записи `is_empty_response` всегда `False` (у Codex нет синтетических маркеров). Тип: edge case

- **test_read_messages_preserves_chronological_order** — JSONL с тремя `response_item` в порядке user → assistant → user — результат имеет ту же последовательность ролей. Тип: edge case

- **test_list_session_files_returns_empty_when_codex_sessions_dir_missing** — async-тест: `~/.codex/sessions/` не существует (через мок `os.path.expanduser` или мок `os.path.exists`) — возвращает `[]`, лог `info`. Тип: edge case

- **test_list_session_files_filters_by_payload_cwd** — async-тест: создать в tmp-структуре `tmp_root/2026/05/06/rollout-A.jsonl` с `session_meta.payload.cwd = "/proj/A"` и `tmp_root/2026/05/06/rollout-B.jsonl` с `cwd = "/proj/B"`, вызвать с `project_dir = "/proj/A"` — в результате только сессия A. Тип: edge case

- **test_list_session_files_limits_to_max_recent_sessions** — async-тест: создать 20 rollout-файлов с одинаковым `cwd` за разные дни — результат содержит ровно 15 элементов, отсортированных по mtime DESC. Тип: edge case

- **test_list_all_session_files_ignores_recent_and_lookback_limits** — async-тест: создать 20 rollout-файлов с одинаковым `cwd` в recent-окне и один rollout за пределами `LOOKBACK_DAYS_FOR_SESSION_LISTING`; `list_all_session_files_for_project` возвращает все 21, а `list_session_files_for_project` остаётся ограниченным 15. Тип: edge case

- **test_list_session_files_sorts_by_mtime_desc** — async-тест: два файла с разным mtime — порядок: новый первым. Тип: edge case

- **test_list_session_files_includes_only_files_within_lookback_window** — async-тест: создать файл за 31 день назад и за 5 дней назад (с подходящим `cwd`) — в результате только второй (первый — за пределами `LOOKBACK_DAYS_FOR_SESSION_LISTING = 30`). Тип: edge case

- **test_list_session_files_extracts_session_id_from_session_meta** — async-тест: rollout с `session_meta.payload.id = "abc-uuid"` — `SessionFileInfo.session_id == "abc-uuid"`. Тип: edge case

- **test_list_session_files_skips_files_without_parsable_session_meta** — async-тест: rollout без записи `session_meta` (повреждённый файл, или файл создан, но Codex не успел дописать первые 50 строк, или валидный `session_meta` есть, но за пределами окна `MAX_LINES_FOR_PREVIEW = 50`). Имя файла соответствует паттерну `rollout-2026-05-06T06-33-50-019dfaeb-7c5b-7ba1-9e56-a33b5e0b512a.jsonl`. **Ожидание:** файл **полностью пропускается** в `list_session_files_for_project` — нельзя проверить `payload.cwd`, значит нельзя гарантировать, что сессия принадлежит этому проекту. Имя файла используется для `session_id` **только** в случаях, когда `session_meta` уже найден и cwd-проверка пройдена, но `payload.id` отсутствует или не парсится (ситуация маловероятная, но допустимая по контракту). Тип: edge case (граница реализации: cwd-проверка обязательна, имя файла — fallback только для session_id, а не для самого факта включения файла в список)

- **test_list_session_files_uses_filename_uuid_when_session_meta_id_missing_but_cwd_matches** — async-тест: rollout с записью `session_meta` где `payload.cwd == project_dir` (cwd-проверка пройдена), но `payload.id` отсутствует (повреждённое поле). **Ожидание:** файл включён в результат, `session_id` извлечён через `_extract_uuid_from_rollout_filename` из имени файла. Это единственный легитимный сценарий fallback к UUID из имени. Тип: edge case

- **test_list_session_files_extracts_preview_from_first_user_response_item** — async-тест: rollout с `session_meta` + `response_item` (role=developer, system) + `response_item` (role=user, text="Реальный вопрос пользователя") — `preview == "Реальный вопрос пользователя"` (developer/system пропущены). Тип: edge case

- **test_read_session_file_snapshot_returns_messages_count_and_active_status_for_active_turn** — async-тест: создать tmp-файл со строками `session_meta`, `response_item` (user "вопрос"), `event_msg` (`task_started`), `response_item` (assistant "ответ"). **Последняя запись — `response_item` с assistant**, `task_complete` ещё не пришёл. **Ожидание:** `snapshot.messages` содержит 2 сообщения (user, assistant), `snapshot.raw_record_count == 4`, `snapshot.last_record["type"] == "response_item"`, `snapshot.is_turn_active == True`. Тип: edge case

- **test_read_session_file_snapshot_marks_turn_inactive_after_task_complete** — async-тест: tmp-файл из предыдущего теста + дополнительная строка `event_msg` (`task_complete`). **Ожидание:** `snapshot.is_turn_active == False`, `snapshot.last_record["payload"]["type"] == "task_complete"`, `snapshot.raw_record_count == 5`. Тип: edge case

- **test_read_session_file_snapshot_marks_turn_inactive_after_turn_aborted** — async-тест: tmp-файл с `session_meta`, `response_item` (user), `event_msg` (`task_started`), `event_msg` (`turn_aborted`). **Ожидание:** `is_turn_active == False`, `last_record.payload.type == "turn_aborted"`. Тип: edge case

- **test_read_session_file_snapshot_marks_turn_inactive_after_event_msg_error** — async-тест: tmp-файл с `event_msg` (`error`) последней записью. **Ожидание:** `is_turn_active == False`. Тип: edge case

- **test_read_session_file_snapshot_keeps_turn_active_on_token_count_after_response_item** — async-тест: tmp-файл с `session_meta`, `event_msg` (`task_started`), `response_item` (assistant), `event_msg` (`token_count`). **Ожидание:** `is_turn_active == True` — `token_count` не маркер завершения, после него `task_complete` ещё может прийти. Тип: edge case

- **test_read_session_file_snapshot_returns_inactive_for_empty_file** — async-тест: пустой файл (0 строк). **Ожидание:** `snapshot.messages == []`, `raw_record_count == 0`, `last_record is None`, `is_turn_active == False`. Тип: edge case

- **test_read_session_file_snapshot_returns_inactive_for_only_invalid_lines** — async-тест: tmp-файл из 3 строк, все — невалидный JSON. **Ожидание:** `messages == []`, `raw_record_count == 3` (счётчик считает сырые строки), `last_record is None` (ни одна не парсится), `is_turn_active == False`. Тип: edge case

- **test_read_session_file_snapshot_raw_record_count_includes_all_types** — async-тест: tmp-файл со смесью `session_meta`, `turn_context`, `event_msg`, `compacted`, `response_item`. **Ожидание:** `raw_record_count` равно общему числу непустых строк, независимо от их типа (счётчик — сырой). Тип: edge case

- **test_read_session_file_snapshot_messages_match_read_messages_method** — async-тест: вызвать `read_session_file_snapshot(path)` и `read_messages_from_session_file(path)` для одного файла, сравнить `snapshot.messages == messages`. Контракт: snapshot НЕ должен фильтровать иначе, чем штатный метод. Тип: edge case

### Тесты ошибок

- **test_compose_args_raises_when_binary_not_found** — мок `shutil.which` возвращает `None`, мок `os.path.exists` для дефолтного пути возвращает `False` — `compose_subprocess_command_args(None, "/tmp", "x", [])` выбрасывает `BackendBinaryNotFoundError` с сообщением, содержащим «Codex CLI не найден». Тип: error

- **test_module_import_does_not_check_binary** — простой `import claude_manager.codex_backend` не должен проверять наличие бинарника. Реализуется через мок `shutil.which`, который выбрасывает `RuntimeError` при вызове — импорт модуля и инстанцирование класса не должны его вызвать. Это страховка контракта родительской спеки. Тип: error

- **test_parse_stdout_line_raises_protocol_error_for_invalid_json** — `parse_stdout_line_into_event("это не json")` выбрасывает `BackendProtocolError`, сообщение содержит первые 200 символов строки и слово «Codex». Тип: error

- **test_parse_stdout_line_truncates_long_invalid_json_to_200_chars** — для строки длиной 500 символов невалидного JSON — сообщение `BackendProtocolError` содержит ровно первые 200 символов. Тип: error

- **test_read_messages_returns_empty_when_file_does_not_exist** — async-тест: `read_messages_from_session_file("/nonexistent/file.jsonl")` → `[]`, лог `debug`/`warning`, исключение НЕ выбрасывается. Тип: error

- **test_read_messages_skips_invalid_json_lines** — async-тест: JSONL-файл с тремя строками, средняя — невалидный JSON — результат содержит две корректные записи, средняя пропущена с `warning`. Тип: error

- **test_read_messages_handles_permission_error** — async-тест: мок `_read_file_lines_blocking` выбрасывает `PermissionError` — метод возвращает `[]`, лог `error`, исключение НЕ выбрасывается. Тип: error

- **test_list_session_files_handles_oserror_per_file** — async-тест: один из файлов выбрасывает `OSError(11, "Resource deadlock avoided")` (имитация EDEADLK) — этот файл пропускается, остальные обрабатываются нормально, в логе `warning`. Тип: error

- **test_list_session_files_handles_missing_year_directory** — async-тест: `~/.codex/sessions/` существует, но `2026/` нет — метод не падает, проходит вглубь без ошибки, возвращает `[]`. Тип: error

- **test_list_session_files_handles_corrupt_session_meta_record** — async-тест: rollout-файл с первой строкой невалидного JSON — `_read_session_meta_record_blocking` возвращает `None`, файл пропускается, остальные продолжают обрабатываться. Тип: error

- **test_read_session_file_snapshot_returns_empty_snapshot_when_file_missing** — async-тест: путь к несуществующему файлу. **Ожидание:** возвращается `SessionFileSnapshot(messages=[], raw_record_count=0, last_record=None, is_turn_active=False)` без выбрасывания исключения; в логе `debug` или `warning`. Тип: error

- **test_read_session_file_snapshot_handles_permission_error** — async-тест: мок `_read_file_lines_blocking` (или соответствующего helper-а) выбрасывает `PermissionError` — метод возвращает пустой snapshot, лог `error`, исключение НЕ прокидывается. Тип: error

- **test_read_session_file_snapshot_handles_oserror_edeadlk** — async-тест: имитация `OSError(11, "Resource deadlock avoided")` — пустой snapshot, лог `warning`. Тип: error

### Контрактные тесты с реальным CLI (опциональные интеграционные)

Все тесты этого раздела пропускаются через `pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI not installed")`. Тесты должны очищать за собой созданные сессии (тестовая директория `/tmp/test-codex-backend-{timestamp}`).

- **test_codex_compose_args_for_new_session_runs_real_cli** — через `asyncio.create_subprocess_exec` запустить команду от `CodexBackend().compose_subprocess_command_args(None, "/tmp/test-cwd", "say banana", [])`, дождаться завершения (`wait_for(timeout=120)`), убедиться что код возврата 0, прочитать stdout — должна быть хотя бы одна строка с `"type": "thread.started"`. Это контрактный тест: команда формируется правильно и Codex CLI её принимает. Тип: contract / integration

- **test_codex_resume_command_args_accept_session_id** — после предыдущего теста — взять `thread_id` из `thread.started`, запустить `compose_subprocess_command_args(thread_id, "/tmp/test-cwd", "still here?", [])` через subprocess, убедиться что Codex принимает команду без ошибки парсинга аргументов (код возврата != 2 — codeprase clap не упал). Тип: contract / integration

- **test_codex_thread_started_is_first_stdout_event** — запустить `codex exec --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -C /tmp "say hi"` через subprocess, прочитать первую непустую строку stdout, разобрать через `CodexBackend().parse_stdout_line_into_event`, проверить что `event.get("type") == "thread.started"` и `read_session_id_from_event(event)` возвращает строку UUID-формата. Тип: contract / integration

- **test_codex_turn_completed_is_terminal_stdout_event** — продолжая предыдущий тест, прочитать все строки stdout до конца, найти событие где `is_turn_complete_event(event) == True`, убедиться что его `type` ∈ `{"turn.completed", "turn.failed"}` и что после него `process.wait()` возвращает в течение 5 секунд. Тип: contract / integration

- **test_codex_session_meta_cwd_matches_command_cwd** — после успешного теста выше найти файл `~/.codex/sessions/YYYY/MM/DD/rollout-*-{thread_id}.jsonl` (по UUID из `thread.started`), прочитать первую строку через `json.loads`, убедиться что `record["type"] == "session_meta"` и `record["payload"]["cwd"] == "/tmp"`. Это контрактный тест фильтрации сессий по проекту через `payload.cwd`. Тип: contract / integration

- **test_codex_session_file_contains_task_complete_event_msg** — после успешного turn-а из предыдущего теста — прочитать весь файл сессии через `read_messages_from_session_file` И через сырой `json.loads` каждой строки. Найти запись с `type == "event_msg"` и `payload.type == "task_complete"`. Убедиться что она присутствует и что `is_turn_terminal_session_record(record) == True`. Тип: contract / integration

- **test_codex_response_item_assistant_text_extracted** — после успешного turn-а — `read_messages_from_session_file(path)` возвращает список с минимум одним `SessionMessage(role="assistant", text=<непустой>, ...)`. Тип: contract / integration

- **test_codex_view_image_path_in_prompt_text** — создать тестовое изображение `/tmp/test_red.png` (1x1 пиксель красный PNG через `PIL` или прямо bytes). Запустить `codex exec --json ... -C /tmp "Опиши /tmp/test_red.png"` — модель должна вызвать `view_image` (видно в stdout как `item.completed` с `item.type` ∈ `{"command_execution"}` или в файле сессии как `event_msg.payload.type == "view_image_tool_call"`). Тест опциональный (зависит от модели), но **подтверждает осознанное ограничение**: путь в `prompt_text` достаточен для срабатывания `view_image`, флаг `-i/--image` не нужен. Если тест начинает стабильно падать на актуальной версии Codex — это сигнал к переходу на `-i` (см. «Расхождения с концепцией → Изображения»). Тип: contract / integration

- **test_codex_turn_failed_carries_error_message** — спровоцировать ошибку Codex (например, передать намеренно невалидный путь рабочей директории `-C /this/path/does/not/exist` либо использовать промт, который точно превышает лимит модели). Запустить через subprocess, прочитать все строки stdout, найти событие с `type == "turn.failed"`, разобрать через `parse_stdout_line_into_event`. **Ожидание:** `is_error_event(event) == True`, `is_turn_complete_event(event) == True`, `read_error_text_from_event(event)` возвращает непустую строку, `read_terminal_status_from_event(event) == TerminalStatus.FAILED`. Контракт: бэкенд корректно отдаёт текст ошибки `process_manager`-у вместо «пустого успешного ответа». Тип: contract / integration

- **test_codex_session_file_snapshot_reflects_active_then_complete_turn** — после теста `test_codex_thread_started_is_first_stdout_event` (или независимый запуск): дождаться `turn.completed` в stdout, найти соответствующий файл `~/.codex/sessions/.../rollout-*-{thread_id}.jsonl`, вызвать `await CodexBackend().read_session_file_snapshot(path)`. **Ожидание:** `snapshot.messages` содержит хотя бы одно user- и одно assistant-сообщение, `snapshot.raw_record_count >= 5` (session_meta + turn_context + event_msg task_started + response_item user + response_item assistant + event_msg task_complete минимум), `snapshot.last_record["type"] == "event_msg"` и `snapshot.last_record["payload"]["type"] == "task_complete"`, `snapshot.is_turn_active == False`. Тип: contract / integration

- **test_codex_sigint_records_turn_interrupt_in_session_file** — запустить долгий Codex turn (промт «прочитай большой файл и резюмируй» с реальным файлом ≥1 МБ в cwd). Спустя 2 секунды после старта turn-а отправить процессу `signal.SIGINT` через `process.send_signal(signal.SIGINT)`. **Ожидания:**
  - Процесс завершается в течение `STOP_SIGINT_TIMEOUT_SECONDS = 5` секунд (`process.wait()` с таймаутом)
  - Код возврата процесса не равен 137 (что было бы SIGKILL fallback) и не равен -9 (POSIX код принудительной остановки)
  - В файле сессии `~/.codex/sessions/.../rollout-*.jsonl` для нашего `thread_id` последняя валидная запись имеет `type == "event_msg"`, и `payload.type` ∈ `{"task_complete", "turn_aborted"}` (Codex штатно записал маркер прерывания)
  - `CodexBackend().is_turn_terminal_session_record(last_record) == True` либо `last_record["payload"]["type"] in EVENT_MSG_TERMINAL_FAILURE_SUBTYPES`
  - Snapshot файла после ожидания: `is_turn_active == False`
  
  Это **главный контрактный тест** стратегии остановки: подтверждает, что SIGINT (а не SIGTERM) — корректный первый сигнал для Codex. Если тест начинает падать на новой версии Codex CLI (например, если в Codex добавят specific SIGTERM-handler с эквивалентной семантикой) — это повод обновить `get_stop_strategy()` и сделать поведение однозначным. Тип: contract / integration

### Связанность тестов с разделами спеки

- 12 абстрактных методов родительской спеки + 2 свойства + 6 расширений (`read_session_file_snapshot`, `is_error_event`, `read_error_text_from_event`, `read_terminal_status_from_event`, `get_stop_strategy`, `is_turn_terminal_session_record`) — каждый покрыт хотя бы одним юнит-тестом
- Каждое значение константы (типы событий stdout, типы записей RolloutLine, подтипы event_msg, флаги CLI, таймауты SIGINT/SIGTERM) проверяется напрямую через тесты `test_compose_args_*`, `test_is_turn_complete_event_*`, `test_event_types_meaning_*`, `test_is_turn_terminal_session_record_*`, `test_get_stop_strategy_*`, `test_read_session_file_snapshot_*`
- Каждый контракт с Codex CLI (команда, событие stdout, формат файла сессии, `view_image` без `-i`, `turn.failed` с `error.message`, snapshot после `task_complete`, штатная запись прерывания после SIGINT) — покрыт интеграционным тестом
- Все тест-кейсы готовы к реализации без дополнительных уточнений — даны конкретные входы (с реальными UUID-формат-строками и текстами) и ожидаемые результаты

### Резюме тест-плана

- Юнит-тесты: 56
- Граничные случаи: 39
- Тесты ошибок: 13
- Контрактные интеграционные: 11
- **Итого: 119 тест-кейсов**

Высокое количество edge cases отражает сложность Codex-протокола: пять форматов записей в файле сессии (`session_meta`, `response_item`, `event_msg`, `turn_context`, `compacted`), три роли в `response_item` (`user`, `assistant`, `developer`/`system`/`null`), пять типов content-блоков (`input_text`, `output_text`, `input_image`, ...), две формы текста ассистента (stdout `text` vs файл `message`), три события финала turn-а (`turn.completed`, `turn.failed`, и **отдельно** запись штатного прерывания после SIGINT в файле сессии). Расширения для snapshot, terminal status и stop strategy дают дополнительные ~30 тестов поверх базового интерфейса.
