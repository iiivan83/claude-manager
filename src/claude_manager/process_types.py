"""Shared types and constants for CLI process management."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from claude_manager.coding_agent_backend import (
    BackendName,
    PermanentErrorKind,
)

type ProgressCallback = Callable[[str, str], Awaitable[None]]
type RetryCallback = Callable[[str, int, int, str], Awaitable[None]]
type SessionIdCallback = Callable[..., Awaitable[None]]

# Максимальное количество повторных попыток при ошибке от Claude
MAX_RETRIES = 10

# Интервал между повторными попытками (секунды)
RETRY_INTERVAL_SECONDS = 60

# Минимальный интервал между промежуточными обновлениями (секунды)
PROGRESS_THROTTLE_SECONDS = 30

# Тип финального события от Claude
EVENT_TYPE_RESULT = "result"

# Тип события с ответом или рассуждением Claude
EVENT_TYPE_ASSISTANT = "assistant"

# Типы контент-блоков внутри assistant-события
CONTENT_BLOCK_TEXT = "text"
CONTENT_BLOCK_THINKING = "thinking"

# Префикс временных идентификаторов сессий
TEMP_SESSION_PREFIX = "_new_"

# Служебный ответ Claude, который не нужно пересылать пользователю
EMPTY_RESPONSE_MARKER = "No response requested."

# Интервал проверки флага отмены внутри ожидания ретрая (секунды)
STOP_CHECK_INTERVAL_SECONDS = 1


class ProcessManagerError(Exception):
    """Общая ошибка process_manager (не удалось запустить процесс)."""


class CodingAgentStartError(ProcessManagerError):
    """Не удалось ЗАПУСТИТЬ CLI-процесс агента (Claude/Codex) — в отличие от «занят».

    Подкласс ProcessManagerError: существующие широкие `except ProcessManagerError`
    продолжают ловить сбой старта (обратная совместимость), но обработчик выше по
    стеку может отличить «CLI не стартовал» от «процесс уже обрабатывает прошлый
    запрос» и показать пользователю честное сообщение вместо ложного «занят».
    """


class ProcessNotFoundError(Exception):
    """Для указанного session_id нет запущенного процесса."""


class ProcessStoppedError(Exception):
    """Запрос прерван командой /stop."""


@dataclass(frozen=True)
class SendResult:
    """Результат отправки сообщения в Claude."""

    text: str
    session_id: str
    is_error: bool
    retries_used: int
    backend: BackendName = BackendName.CLAUDE
    error_text: str | None = None
    # Заполняется, если ошибка постоянная (повтор бессмыслен): транспортный
    # слой по этому полю показывает понятное сообщение вместо «повтор N/10».
    permanent_error_kind: PermanentErrorKind | None = None


@dataclass(frozen=True)
class StopResult:
    """Результат остановки процесса."""

    was_running: bool
    was_retrying: bool
    backend: BackendName = BackendName.CLAUDE
