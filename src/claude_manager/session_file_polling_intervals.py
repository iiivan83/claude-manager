"""Numeric tunables that control how often the session file watcher polls and retries."""

from __future__ import annotations

# Из BRD CJM-07: watcher проверяет файлы сессий каждые 2 секунды.
POLL_INTERVAL_SECONDS = 2

# Задержка после непредвиденной ошибки в бесконечном цикле start().
ERROR_RETRY_DELAY_SECONDS = 10

# Защита от утечки pause_session(), если handler упал до resume_session().
PAUSE_LEAK_SAFETY_TIMEOUT_SECONDS = 120

# Backoff для временно отсутствующих или пустых файлов сессий.
MISSING_FILE_RETRY_BASE_SECONDS = 5
MISSING_FILE_RETRY_MAX_SECONDS = 60
MISSING_FILE_RETRY_STATE_TTL_SECONDS = 300

# Максимум одновременно читаемых файлов при reset_state.
# Без этого ограничения переключение проектов в больших проектах
# создаёт сотни одновременных I/O операций и упирается в файловые дескрипторы.
MAX_CONCURRENT_RESET_READS = 16
