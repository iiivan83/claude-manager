"""Обёртка для запуска Claude Code CLI через subprocess.

Создаёт процесс Claude с протоколом stream-json, отправляет сообщения
через stdin и читает потоковые JSON-события из stdout.
"""

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncGenerator

from claude_manager import config
from claude_manager.claude_code_backend import CLAUDE_OPUS_MODEL_ID
from claude_manager.coding_agent_backend import CodingAgentBackend

logger = logging.getLogger(__name__)

# Полный путь к Claude Code CLI (shutil.which ищет в PATH при импорте модуля)
# Если не найден — пробуем стандартное расположение /usr/local/bin/claude
CLAUDE_CLI_COMMAND = shutil.which("claude") or "/usr/local/bin/claude"

# Время ожидания завершения процесса после SIGTERM (секунды)
TERMINATE_TIMEOUT_SECONDS = 5

# Максимальное время ожидания одной строки из stdout Claude CLI (секунды).
# Если CLI не выдаёт ни одной строки за это время — считаем, что он завис.
# 30 минут — покрывает тяжёлые задачи (рефакторинг крупных файлов, длинные
# Bash-команды, extended thinking с --effort max). Во время работы инструментов
# и фазы thinking stdout молчит — это штатное поведение, а не зависание.
READ_LINE_TIMEOUT_SECONDS = 1800

# Размер буфера StreamReader для stdout/stderr процесса Claude CLI (байты).
# Дефолт asyncio — 64 KB на одну строку, но события stream-json могут
# быть значительно больше: длинные ответы Claude с markdown, результаты
# инструментов Read/Bash для больших файлов. Превышение дефолта приводит
# к asyncio.LimitOverrunError при чтении через readline(), что выглядит
# как обрыв процесса и запускает ретрай. 16 MB покрывает реалистичные
# edge cases — буфер растёт по мере необходимости, не аллоцируется заранее.
STREAM_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024

# Формат обмена данными с Claude CLI через stdin/stdout
STREAM_JSON_INPUT_FORMAT = "stream-json"
STREAM_JSON_OUTPUT_FORMAT = "stream-json"

# Типы событий stream-json, на которые реагирует модуль
EVENT_TYPE_SYSTEM = "system"
EVENT_TYPE_RESULT = "result"


class ClaudeStartError(Exception):
    """Ошибка запуска процесса Claude (CLI не найден, ошибка ОС)."""


class ClaudeProcessError(Exception):
    """Ошибка взаимодействия с запущенным процессом Claude."""


class BackendSubprocessStartError(Exception):
    """Ошибка запуска subprocess для выбранного backend-а."""


class BackendSubprocessError(Exception):
    """Ошибка взаимодействия с backend subprocess."""


def _build_command_args(session_id: str | None) -> list[str]:
    """Собирает аргументы командной строки для запуска Claude Code CLI."""
    args = [
        CLAUDE_CLI_COMMAND,
        "-p",
        "--output-format", STREAM_JSON_OUTPUT_FORMAT,
        "--verbose",
        "--input-format", STREAM_JSON_INPUT_FORMAT,
        "--dangerously-skip-permissions",
        "--model", CLAUDE_OPUS_MODEL_ID,
        "--effort", "max",
    ]

    if session_id is not None:
        args.extend(["--resume", session_id])

    return args


def _parse_event(raw_line: str) -> dict | None:
    """Разбирает одну строку stdout как JSON-событие."""
    if not raw_line.strip():
        return None

    try:
        return json.loads(raw_line)
    except json.JSONDecodeError:
        # Обрезаем строку до 200 символов, чтобы не засорять лог
        truncated_line = raw_line[:200]
        raise ClaudeProcessError(
            f"Невалидный JSON от Claude: '{truncated_line}'"
        )


def _extract_session_id_from_event(event: dict) -> str | None:
    """Извлекает session_id из события stream-json."""
    return event.get("session_id")


class ClaudeProcess:
    """Обёртка над запущенным процессом Claude Code CLI."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.session_id: str | None = None

    async def send_message(self, text: str) -> None:
        """Отправляет текстовое сообщение в процесс Claude через stdin."""
        self._check_process_alive()
        self._check_stdin_available()

        message = {
            "type": "user",
            "message": {"role": "user", "content": text},
        }
        # ensure_ascii=False сохраняет кириллицу как есть
        json_line = json.dumps(message, ensure_ascii=False) + "\n"

        await self._write_to_stdin(json_line)
        # Закрываем stdin — Claude CLI получает EOF и начинает обработку.
        # Без этого CLI буферизует stdout и не отдаёт ответ при запуске через pipe.
        self.process.stdin.close()
        logger.debug("Отправлено сообщение в Claude: %d символов", len(text))

    async def read_events(self) -> AsyncGenerator[dict, None]:
        """Читает JSON-события из stdout процесса Claude."""
        while True:
            try:
                raw_bytes = await asyncio.wait_for(
                    self.process.stdout.readline(),
                    timeout=READ_LINE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Claude CLI (PID %d) не отвечает %d секунд — завис",
                    self.process.pid, READ_LINE_TIMEOUT_SECONDS,
                )
                raise ClaudeProcessError(
                    f"Claude CLI не отвечает {READ_LINE_TIMEOUT_SECONDS} секунд"
                )

            # Пустые байты — процесс завершился
            if not raw_bytes:
                return

            line = raw_bytes.decode("utf-8").rstrip("\n")
            event = _parse_event(line)

            # Пустая строка — пропускаем
            if event is None:
                continue

            self._update_session_id(event)
            yield event

            # Событие result — последнее для текущего запроса
            if event.get("type") == EVENT_TYPE_RESULT:
                return

    async def terminate(self) -> None:
        """Принудительно завершает процесс Claude."""
        if self.process.returncode is not None:
            return

        self.process.terminate()

        try:
            await asyncio.wait_for(
                self.process.wait(),
                timeout=TERMINATE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Процесс Claude (PID %d) не завершился по SIGTERM, "
                "отправлен SIGKILL",
                self.process.pid,
            )
            self.process.kill()
            await self.process.wait()

        logger.info(
            "Процесс Claude завершён: PID=%d, код=%s",
            self.process.pid,
            self.process.returncode,
        )

    def is_running(self) -> bool:
        """Проверяет, работает ли процесс Claude."""
        return self.process.returncode is None

    def _check_process_alive(self) -> None:
        """Проверяет, что процесс ещё не завершился."""
        if self.process.returncode is not None:
            raise ClaudeProcessError("Процесс Claude уже завершился")

    def _check_stdin_available(self) -> None:
        """Проверяет, что stdin процесса доступен."""
        if self.process.stdin is None:
            raise ClaudeProcessError("stdin процесса Claude недоступен")
        if self.process.stdin.is_closing():
            raise ClaudeProcessError("stdin процесса Claude закрывается")

    async def _write_to_stdin(self, data: str) -> None:
        """Записывает данные в stdin процесса."""
        try:
            self.process.stdin.write(data.encode("utf-8"))
            await self.process.stdin.drain()
        except ConnectionError as pipe_error:
            raise ClaudeProcessError(
                f"Не удалось записать в stdin: {type(pipe_error).__name__} — "
                f"процесс Claude закрыл pipe"
            ) from pipe_error

    def _update_session_id(self, event: dict) -> None:
        """Обновляет session_id из события, если ещё не установлен."""
        if self.session_id is not None:
            return

        extracted_id = _extract_session_id_from_event(event)
        if extracted_id is not None:
            self.session_id = extracted_id


class BackendSubprocess:
    """Thin wrapper over a subprocess started for a coding-agent backend."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process

    async def write_stdin(self, payload: bytes) -> None:
        """Write optional stdin bytes and close stdin to deliver EOF."""
        if self.process.stdin is None:
            raise BackendSubprocessError("stdin backend subprocess недоступен")
        if payload:
            try:
                self.process.stdin.write(payload)
                await self.process.stdin.drain()
            except ConnectionError as pipe_error:
                raise BackendSubprocessError(
                    f"Не удалось записать в stdin backend subprocess: "
                    f"{type(pipe_error).__name__}"
                ) from pipe_error
        self.process.stdin.close()

    async def read_stdout_line(self) -> bytes:
        """Read one stdout line with the runner timeout."""
        if self.process.stdout is None:
            raise BackendSubprocessError("stdout backend subprocess недоступен")
        try:
            return await asyncio.wait_for(
                self.process.stdout.readline(),
                timeout=READ_LINE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as error:
            raise BackendSubprocessError(
                f"Backend subprocess не отвечает {READ_LINE_TIMEOUT_SECONDS} секунд"
            ) from error

    async def read_stderr_text(self) -> str:
        """Read stderr text for diagnostics."""
        if self.process.stderr is None:
            return ""
        raw_stderr = await self.process.stderr.read(500)
        return raw_stderr.decode("utf-8", errors="replace").strip()

    def is_running(self) -> bool:
        """Return whether the subprocess is still running."""
        return self.process.returncode is None

    async def wait(self) -> int:
        """Wait for subprocess completion and return its exit code."""
        return await self.process.wait()


async def start_subprocess_for_backend(
    backend: CodingAgentBackend,
    session_id: str,
    cwd: str,
    prompt_text: str,
    image_paths: list[str],
) -> BackendSubprocess:
    """Start one backend subprocess using only the backend adapter contract."""
    command_args = backend.compose_subprocess_command_args(
        session_id,
        cwd,
        prompt_text,
        image_paths,
    )
    stdin_payload = backend.encode_user_message_for_cli_stdin(prompt_text, image_paths)
    try:
        process = await asyncio.create_subprocess_exec(
            *command_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_BUFFER_LIMIT_BYTES,
            cwd=cwd,
        )
    except FileNotFoundError as error:
        raise BackendSubprocessStartError(
            f"Не удалось запустить {backend.display_name}: CLI не найден"
        ) from error
    except OSError as error:
        raise BackendSubprocessStartError(
            f"Не удалось запустить {backend.display_name}: {error}"
        ) from error

    backend_subprocess = BackendSubprocess(process)
    await backend_subprocess.write_stdin(stdin_payload)
    logger.info(
        "Backend subprocess запущен: backend=%s, PID=%d, cwd=%s",
        backend.name.value,
        process.pid,
        cwd,
    )
    return backend_subprocess


async def start_process(session_id: str | None = None, cwd: str | None = None) -> ClaudeProcess:
    """Запускает новый процесс Claude Code CLI."""
    command_args = _build_command_args(session_id)
    effective_cwd = cwd if cwd is not None else config.WORKING_DIR

    try:
        process = await asyncio.create_subprocess_exec(
            *command_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_BUFFER_LIMIT_BYTES,
            cwd=effective_cwd,
        )
    except FileNotFoundError:
        error_message = (
            "Claude Code CLI не найден. "
            "Убедитесь, что 'claude' доступен в PATH"
        )
        logger.error(error_message)
        raise ClaudeStartError(error_message)
    except OSError as os_error:
        error_message = f"Ошибка запуска Claude Code CLI: {os_error}"
        logger.error(error_message)
        raise ClaudeStartError(error_message)

    resume_info = f", resume={session_id}" if session_id else ""
    logger.info(
        "Процесс Claude запущен: PID=%d%s, cwd=%s",
        process.pid,
        resume_info,
        effective_cwd,
    )

    return ClaudeProcess(process)
