# Сессия 31-05: RCA медленного списка сессий

## Коротко

Проверена причина, почему команда `/sessions` стала долго отвечать. Корень оказался в Codex-части списка: она не использует вчерашний быстрый operational index и каждый раз перечитывает `session_meta` во всех Codex rollout-файлах за 30 дней.

## Рабочие файлы

- **`dev/docs/logs/root-cause-reports/31-05_02-47_slow-session-list.md`** — полный RCA-отчёт с цепочкой причин, замерами и чек-листом исправлений.
- **`dev/docs/logs/root-cause-analysis/31.05_02.42-root-cause-analysis-slow-session-list/orchestrator-log.json`** — лог RCA-пайплайна.
- **`src/claude_manager/bot.py`** — проверен обработчик `/sessions`, который вызывает listing всех backend-ов.
- **`src/claude_manager/codex_session_file_listing.py`** — проверен Codex listing, где происходит 30-дневный meta-scan.
- **`src/claude_manager/codex_session_index.py`** — проверен existing operational index; он работает, но не применяется к `/sessions`.

## Решения

- Основной фикс должен быть не уменьшением lookback с 30 до 4 дней, а user-facing индексом или cache для `/sessions`.
- Preview нужно строить только для 15 видимых сессий, а не привязывать весь поиск проекта к повторному чтению всех Codex-файлов.
- `codex_session_file_listing.py` уже 499 строк, поэтому серьёзный фикс лучше делать через отдельный модуль, а не дописывать логику в этот файл.

## Проверки

- Codex UI-list для `budgets`: 7.875 секунды, 15 сессий.
- Codex UI-list для `claude_manager`: 8.265-9.643 секунды, 15 сессий.
- Инструментированный вызов для `budgets`: 7.785 секунды, 14 106 чтений `session_meta`, 15 чтений preview.
- Claude UI-list для `claude_manager`: 0.140 секунды.
- Сбор путей rollout-файлов за 30 дней: 0.114 секунды; значит, тормозит не обход директорий, а чтение/парсинг JSONL.
- Operational index: первый вызов строит индекс за несколько секунд, повторный возвращается за миллисекунды, но `/sessions` его не использует.

## Риски и ограничения

- Код не менялся; это расследование и отчёт, не исправление.
- Живой Telegram-вызов не трогался, чтобы не вмешиваться в работающий бот.
- Абсолютные времена зависят от текущей нагрузки диска, но счётчик 14 106 meta-чтений подтверждает алгоритмическую причину.

## Продолжение

1. Добавить user-facing Codex session-list index/cache для `/sessions`.
2. Обновить `CLAUDE.md` и Codex backend specs, чтобы `/sessions` тоже считался горячим пользовательским путём.
3. Добавить regression test, который ловит повторный полный meta-scan по тысячам чужих rollout-файлов.
