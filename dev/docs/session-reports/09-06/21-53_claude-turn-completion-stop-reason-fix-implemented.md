# Сессия 09-06: фикс завершённости turn'а Claude по stop_reason (silence-mode reply bug)

## Коротко

Починили баг: в режиме тишины (бот доставляет только финальные ответы Claude с `is_final=True`) финальный ответ терялся, когда пользователь отвечал на сообщение бота Telegram-реплеем. Причина — читатель `.jsonl`-файлов сессий Claude считал уже завершённый ход (turn) незакрытым, потому что внешние сессии Claude Code не пишут запись `result`, а старый критерий ждал именно её. Фикс сделал критерий зависящим от `stop_reason` последней `assistant`-записи. Починены оба ридера, добавлено 9 регрессионных тестов, весь прогон зелёный (1196 passed). Решение — реализация диагностики из предыдущей сессии (handoff `21-14`).

## Рабочие файлы

- **`src/claude_manager/claude_code_session_file_reader.py`** — корень фикса. Добавлены константы `TURN_CONTINUING_STOP_REASONS = {"tool_use", "pause_turn"}` и `MID_TURN_STREAM_EVENT_TYPES = {"progress", "queue-operation"}`, добавлен предикат `_assistant_record_keeps_turn_active`, переписаны ветки решения в `_compute_is_turn_active_from_parsed_records` (snapshot reader, полный парсинг) и `_read_cursor_count_last_record_and_turn_active` (cursor reader, лёгкий проход для baseline в `/all`). `BUSY_EVENT_TYPES` оставлен как есть — это отдельный контракт бэкенда, не критерий turn'а. Файл вырос до **545 строк** — выше проектного порога 500, техдолг зафиксирован.
- **`tests/test_claude_code_backend.py`** — добавлено 9 тестов (snapshot и cursor reader): терминальные `stop_reason` (`end_turn`, `stop_sequence`, `max_tokens`) закрывают turn; служебные записи после `end_turn` (`last-prompt`, `ai-title`, `mode`, `file-history-snapshot`) не открывают turn заново; `tool_use` оставляет turn активным.
- **`dev/docs/adr/09.06_21.53-session-change-documenter-claude-turn-completion-stop-reason-detection.md`** — ADR этого решения, частично заменяет ADR от 29-05.
- **`dev/docs/session-reports/09-06/21-14_claude-finished-turn-misread-as-active-handoff.md`** — входной handoff: диагностика из предыдущей сессии (эта сессия — реализация фикса по её плану).
- Файлы, прочитанные для понимания цепочки (не менялись): `coding_agent_session_file_poller.py` (потребитель `is_turn_active`: `hold_final_message`, `is_final`), `reply_route_handler.py` (путь реплея, делегирует финал watcher'у), `telegram_response_delivery.py` (silence-фильтр), `codex_session_file_reader.py` (эталон правильного критерия — `_compute_is_turn_active_for_codex`).

## Решения

- **Решение:** чинить корень в ридере, а не симптом через `Silence off`. **Причина:** `Silence off` показал бы промежуточные, но одиночный финал через watcher всё равно придерживается из-за той же ошибки чтения.
- **Решение:** критерий зависит от `stop_reason` — `tool_use`/`pause_turn` и отсутствующий (`None`, ещё стримится) → turn активен; явный терминальный (`end_turn`/`stop_sequence`/`max_tokens`/`refusal`) → turn закрыт. **Причина:** внешние Claude Code-сессии завершаются `assistant end_turn` без `result`; старое правило «любой `assistant` → активен» держало их активными вечно.
- **Решение:** чинить **оба** ридера, общий предикат `_assistant_record_keeps_turn_active`. **Причина:** snapshot и cursor reader используют разный проход; правка одного оставила бы баг в половине сценариев (watcher poll и reset_state идут через cursor reader).
- **Решение:** `reply_route_handler` не трогать. **Причина:** после фикса ридера watcher сам корректно доставляет финал по пути реплея; прямая доставка `result.text` ввела бы риск двойной доставки.
- **Решение:** не добавлять отдельный сквозной тест. **Причина:** цепочка «ридер → poller → доставка» уже покрыта существующим `test_active_turn_holds_last_assistant_message_until_terminal_snapshot` (backend-agnostic: `is_turn_active=False` → финал с `is_final=True`); 9 новых тестов покрывают именно ридер, где жил баг.
- **Ограничение:** `BUSY_EVENT_TYPES` неизменна — это контракт `claude_code_backend.event_types_meaning_cli_is_busy`, не относится к завершённости turn'а.

## Проверки

- TDD RED → GREEN: 9 новых тестов до фикса падали с `assert True is False` (turn ошибочно активен), после фикса — зелёные.
- Полный прогон `python -m pytest tests/ -q` — **1196 passed, 4 skipped, 0 failed**.
- Прицельный прогон затронутых файлов `pytest tests/test_claude_code_backend.py tests/test_session_watcher.py` — 49 passed.
- Эмпирическая проверка контракта (требование CLAUDE.md по внешним системам): 6 последних `.jsonl` в проекте `bloger` — 0 записей `result`, есть финалы `end_turn`, самый свежий реально в середине turn'а со `stop_reason=tool_use`.
- Имя сущности ADR прогнано через name-reviewer (Explore) — вердикт ПОНЯТНО.

## Риски и ограничения

- **Не закоммичено.** На момент отчёта изменения не зафиксированы — ждём явного подтверждения пользователя на коммит (стандартное правило: не коммитить без явной просьбы). В рабочем дереве также лежат не относящиеся к этой сессии изменения: удаление скиллов в `.claude/skills/` и `.agents/skills/`, правка `docs-index.md`, untracked-папка `dev/docs/logs/root-cause-analysis/08.06_...` — это след прошлых сессий, не трогать при коммите этой сессии без отдельного решения.
- **Техдолг размера.** `claude_code_session_file_reader.py` — 545 строк (выше порога 500). Разбиение по ответственности отложено.
- **Текущий проект и `/all`.** Обычный watcher мониторит только текущий проект (`config.WORKING_DIR`). Для живых уведомлений из `bloger`, когда активен другой проект, нужен режим `/all`. Старые pending-сообщения живут 3 часа.
- **Диагностическое логирование не добавлено.** Опциональный пункт из вчерашнего RCA (логировать успешную и подавленную watcher-доставку) не реализован — без него Codex-кейсы из RCA нельзя доказуемо закрыть по логам.

## Продолжение

1. Решить по коммиту: скилл `session-change-documenter` предусматривает финальный коммит всех изменений сессии одним коммитом; стандартное правило требует явного подтверждения. Дождаться решения пользователя.
2. Опционально: добавить диагностическое логирование успешной/подавленной watcher-доставки (из вчерашнего RCA), чтобы доказуемо закрывать подобные кейсы.
3. Опционально: разбить `claude_code_session_file_reader.py`, чтобы увести файл под порог 500 строк.
