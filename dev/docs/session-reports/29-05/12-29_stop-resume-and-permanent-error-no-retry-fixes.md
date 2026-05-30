# Сессия 29-05: фикс «стоп → сессия не оживает» и пропуск повторов при постоянных ошибках

## Резюме

Закрыли два дефекта. Дефект №1 (со скриншота пользователя): после `/stop` сессия не реагировала на новые сообщения — фикс в `process_manager.update_session_id` сохраняет форму ключа состояния, из-за чего раньше утекал `stop_event` и убивал следующий turn. Дефект №2 (переполненная сессия слала ~11 минут спама из 10 повторов с `Prompt is too long`): добавлена классификация ошибок backend по повторяемости — постоянные ошибки (переполнение контекста, исчерпанный лимит) больше не уходят в retry-цикл, пользователю сразу приходит понятное сообщение. Оба фикса под TDD, полный прогон — 1028 passed, 4 skipped.

## Изменённые файлы

### Дефект №1 — стоп → resume

- **src/claude_manager/process_manager.py** — изменён — в `update_session_id` при переносе состояния под новый `session_id` сохраняется форма ключа: если состояние лежало под tuple-ключом (backend-aware путь), новый ключ тоже tuple через `_make_backend_process_key`. Раньше `_make_process_key` схлопывал ключ в голую строку для CLAUDE, состояние переезжало под строковый ключ, а finally backend-aware пути чистил только tuple-ключи — выставленный `stop_event` оставался висеть и убивал следующий turn.
- **tests/test_process_manager.py** — изменён — добавлен воспроизводящий тест `test_claude_session_resumes_after_stop_during_retry`: после `/stop` во время обработки следующее сообщение в ту же сессию должно успешно запустить новый turn.

### Дефект №2 — классификация ошибок по повторяемости

- **src/claude_manager/coding_agent_backend.py** — изменён — добавлен enum `PermanentErrorKind` (`CONTEXT_OVERFLOW`, `USAGE_LIMIT`) и неабстрактный метод-дефолт `CodingAgentBackend.classify_permanent_error(error_text) -> PermanentErrorKind | None` (по умолчанию `None` — все ошибки временные; Codex наследует безопасный дефолт).
- **src/claude_manager/claude_code_backend.py** — изменён — `ClaudeCodeBackend` переопределяет `classify_permanent_error`: `prompt is too long` → `CONTEXT_OVERFLOW`, `hit your limit` → `USAGE_LIMIT`. Подстроки вынесены в константы `CLAUDE_CONTEXT_OVERFLOW_ERROR_MARKERS`, `CLAUDE_USAGE_LIMIT_ERROR_MARKERS`, сравнение регистронезависимое.
- **src/claude_manager/process_manager.py** — изменён — поле `permanent_error_kind` в `SendResult`; функция `_classify_permanent_error_result` (спрашивает backend, при постоянной ошибке возвращает SendResult с `retries_used=0` и заполненным kind, логирует warning); гарды во всех трёх точках запуска повтора: `_retry_loop`, `_send_message_backend_aware`, `_execute_send`.
- **src/claude_manager/claude_interaction.py** — изменён — словарь `PERMANENT_ERROR_MESSAGES` и функция `_build_permanent_error_message` (транспортный слой владеет текстом для пользователя); ветка в `handle_claude_result`: при `permanent_error_kind` шлёт «Сессия переполнилась… /new» или «Исчерпан лимит… дождись обновления».
- **tests/test_claude_code_backend.py** — изменён — тест распознавания постоянных ошибок (overflow/limit, регистронезависимость, временная ошибка → None).
- **tests/test_process_manager.py** — изменён — `test_permanent_overflow_error_skips_retries` (постоянная ошибка → `retries_used=0`, retry_callback не вызван) и `test_transient_error_still_retries_backend_aware` (временная ошибка по-прежнему повторяется).
- **tests/test_claude_interaction.py** — изменён — `test_permanent_overflow_result_sends_new_session_hint` (в сообщении есть `/new`, нет сырого `Prompt is too long`).

### Документы (этот запуск документалиста)

- **dev/docs/adr/29.05_12.29-session-change-documenter-backend-error-retryability-classification.md** — создан — ADR об архитектурном решении: классификация ошибок в контракте backend, enum `PermanentErrorKind`, гард перед повтором, человекочитаемое сообщение в транспортном слое.
- **dev/docs/brd/brd-user-journeys.md** — изменён — CJM-02 «Что может пойти не так»: разделение на временную и постоянную ошибку Claude; CJM-08 (/stop): добавлено, что после остановки сессию можно продолжить следующим сообщением.
- **dev/docs/specs/realised/process_manager_spec.md** — изменён — поле `permanent_error_kind` в описании `SendResult`, уточнение `is_error`/`retries_used`, разделение временной/постоянной ошибки в «Обработка ошибок», уточнение константы `MAX_RETRIES`.

## Решения

- **Решение**: классификацию ошибок разместили в контракте backend (`coding_agent_backend.py`), а не в `process_manager`. **Причина**: формат ошибок специфичен для CLI-движка (Claude vs Codex), а `process_manager` backend-агностичен и не должен знать конкретные тексты ошибок.
- **Решение**: метод `classify_permanent_error` сделан неабстрактным с дефолтом «все ошибки временные». **Причина**: Codex наследует безопасное поведение без собственной реализации — не ломается, просто продолжает повторять, как раньше.
- **Решение**: классификацию для Codex намеренно НЕ реализовали. **Причина**: нет эмпирических образцов того, как Codex сообщает о переполнении/лимите, а проектное правило запрещает догадки о контрактах внешних систем. Открытая задача — добавить после появления реальных образцов ошибок Codex.
- **Решение**: разные сообщения для `CONTEXT_OVERFLOW` (совет `/new`) и `USAGE_LIMIT` (совет «дождись лимита»). **Причина**: `/new` бесполезен при исчерпанном лимите — новая сессия не обходит общий лимит запросов.
- **Решение**: тексты сообщений для пользователя живут в транспортном слое (`claude_interaction`), а инфраструктура (`process_manager`) только помечает результат kind'ом. **Причина**: соблюдение границ слоёв — инфраструктура не знает про Telegram и формулировки.
- **Решение**: фикс дефекта №1 — точечный, без нового ADR. **Причина**: это исправление бага в существующем инварианте формы ключа (backend-aware путь), а не новое архитектурное решение.

## Проблемы и решения

- **Проблема**: при переносе состояния под реальный UUID ключ схлопывался в голую строку для CLAUDE, и выставленный через `/stop` `stop_event` оставался висеть — следующий turn сразу падал на проверке отмены. **Решение**: сохранять форму ключа (tuple остаётся tuple) в `update_session_id`, чтобы finally backend-aware пути корректно вычистил `stop_event`.
- **Проблема**: переполненная сессия повторяла безнадёжный запрос 10 раз. **Решение**: backend классифицирует ошибку как постоянную, retry-цикл пропускается, пользователь получает понятное сообщение.

## Результаты тестирования

- 4 новых поведенческих теста дефекта №2: TDD-цикл RED → GREEN (до фикса 2 падали — overflow повторялся, сообщения про `/new` не было; после фикса все 4 зелёные).
- Воспроизводящий тест дефекта №1 — зелёный после фикса.
- Полный прогон `python -m pytest tests/ -q` — 1028 passed, 4 skipped, 0 failed.

## Контекст для следующей сессии

- **Незакоммичено**: на момент сессии в рабочем дереве, помимо двух фиксов, висит незавершённая работа прошлых сессий вокруг `session_watcher` (`coding_agent_session_file_poller.py`, `session_file_polling_cursors.py`, `session_watcher.py`, `tests/test_session_watcher.py` и часть `claude_interaction.py`). Это НЕ относится к фиксам дефектов №1/№2. Решение о коммите оставлено пользователю.
- **Открытая задача — место принципа**: принцип «классификация ошибок backend по повторяемости» рекомендован к фиксации на уровне проекта. В проекте нет корневого `architecture.md`; принципы живут в разделе «Архитектурные принципы» в `CLAUDE.md`. Выбор «новый architecture.md vs запись в CLAUDE.md» оставлен пользователю (правка `CLAUDE.md` чувствительна).
- **Открытая задача — Codex**: реализовать `classify_permanent_error` для Codex после получения реальных образцов его ошибок.
- **Вторичные рекомендации RCA** (к обсуждению отдельно): `/new` должен останавливать зависший запрос прошлой сессии; `/stop` должен бить не только по активной, но и по фоновым сессиям. Радиус поражения шире — отложено.
- **Связанные документы**: RCA-отчёт `dev/docs/logs/root-cause-reports/29-05_11-26_session-overflow-retry-spam.md`; ADR дедупликации того же дня; прошлый инцидент того же корня `13-04_17-45_retry-loop-session-multiplication.md`.

## Выполненные команды

- `python -m pytest tests/test_claude_code_backend.py tests/test_process_manager.py tests/test_claude_interaction.py -k "<4 теста>"` — подтверждение GREEN на новых тестах.
- `python -m pytest tests/ -q` — полный прогон без регрессий (1028 passed, 4 skipped).
- `git status` / `git diff HEAD` — отделение фиксов дефектов №1/№2 от незавершённой работы вокруг `session_watcher`.
