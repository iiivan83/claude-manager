"""Оркестрация взаимодействия с Claude CLI — отправка сообщений и обработка ответов.

Управляет жизненным циклом запроса к Claude: проверка занятости, создание процесса,
отправка сообщения, обработка промежуточных обновлений (progress, retry),
разблокировка watcher при тишине stdout (watchdog), обработка финального ответа.

Зависимости от Telegram-доставки разрешены через callback-функции:
bot.py регистрирует delivery-модуль через пары (модуль, имя_атрибута).
claude_interaction.py НЕ импортирует bot.py.
"""

import asyncio
import logging

from claude_manager import (
    all_projects_monitor,
    coding_agent_backend,
    config,
    daily_session_registry,
    process_manager,
    reply_anchor_registry,
    session_manager,
    session_reader,
    session_watcher,
    unread_buffer,
)
from claude_manager.coding_agent_backend import (
    BackendName,
    PermanentErrorKind,
    SessionUnreadState,
)
from claude_manager.session_manager import ActiveSession
from claude_manager.session_summary_generator import generate_session_summary

logger = logging.getLogger(__name__)


# --- Константы ---


# Если stdout Claude молчит дольше этого времени — разблокировать watcher,
# чтобы он показывал прогресс из JSONL. Типично для работы Agent tool.
AGENT_SILENCE_TIMEOUT_SECONDS = 60

# Сообщение для пустого ответа Claude
EMPTY_RESPONSE_TEXT = "Claude обработал запрос, но не дал текстовый ответ"

# Служебный ответ Claude, который не пересылается пользователю
NO_RESPONSE_MARKER = "No response requested."

# Сообщение при попытке написать в режиме /all
MONITORING_MODE_MESSAGE = (
    "Вы в режиме мониторинга. Для отправки сообщений "
    "подключитесь к сессии — нажмите на номер сессии или отправьте /new"
)

# Максимум ожидания отдельного LLM-вызова, который делает короткое название сессии.
SESSION_SUMMARY_TIMEOUT_SECONDS = 45

# Человекочитаемые сообщения о постоянных ошибках backend (повтор бессмыслен).
# Транспортный слой владеет текстом для пользователя — инфраструктура (process_manager)
# только помечает результат kind'ом, но не знает про Telegram и формулировки.
PERMANENT_ERROR_MESSAGES = {
    PermanentErrorKind.CONTEXT_OVERFLOW: (
        "Сессия переполнилась и больше не может принимать сообщения. "
        "Начни новую через /new"
    ),
    PermanentErrorKind.USAGE_LIMIT: (
        "Исчерпан лимит запросов к Claude — повтор не поможет. "
        "Дождись обновления лимита и попробуй снова"
    ),
}


def _build_permanent_error_message(
    kind: PermanentErrorKind, display_name: str,
) -> str:
    """Человекочитаемое сообщение о постоянной ошибке backend."""
    base_message = PERMANENT_ERROR_MESSAGES.get(kind)
    if base_message is None:
        return f"{display_name}: запрос нельзя повторить"
    return f"{display_name}: {base_message}"


# --- Callback-зависимости от транспортного слоя ---
# Инициализируются через init_callbacks() при старте бота.
# До вызова init_callbacks() функции, требующие отправки в Telegram, упадут с RuntimeError.
#
# Хранится пара (module, attr_name), а не ссылка на функцию — чтобы mock.patch
# на модуле-источнике доставки подхватывался при каждом вызове.

# (module, "send_response") — async (chat_id, text, session_number, is_final, ...) -> None
_send_response_ref: tuple | None = None

# (module, "_send_telegram_message") — async (chat_id, text, parse_mode=None, ...) -> None
_send_telegram_message_ref: tuple | None = None


def init_callbacks(
    *,
    send_response_module,
    send_response_attr: str,
    send_telegram_message_module,
    send_telegram_message_attr: str,
) -> None:
    """Инъекция callback-зависимостей от транспортного слоя.

    Принимает пары (модуль, имя_атрибута) вместо прямых ссылок на функции —
    чтобы unittest.mock.patch на модуле-источнике подхватывался автоматически.
    Вызывается один раз из bot.py при инициализации бота (setup_bot).
    Разрывает циклическую зависимость: claude_interaction НЕ импортирует bot.
    """
    global _send_response_ref, _send_telegram_message_ref
    _send_response_ref = (send_response_module, send_response_attr)
    _send_telegram_message_ref = (send_telegram_message_module, send_telegram_message_attr)


def _get_send_response():
    """Разрешает callback send_response через getattr — совместимо с mock.patch."""
    if _send_response_ref is None:
        raise RuntimeError(
            "claude_interaction.init_callbacks() не вызван — "
            "callback-зависимости не инициализированы"
        )
    module, attr = _send_response_ref
    return getattr(module, attr)


def _get_send_telegram_message():
    """Разрешает callback send_telegram_message через getattr — совместимо с mock.patch."""
    if _send_telegram_message_ref is None:
        raise RuntimeError(
            "claude_interaction.init_callbacks() не вызван — "
            "callback-зависимости не инициализированы"
        )
    module, attr = _send_telegram_message_ref
    return getattr(module, attr)


# --- Внутреннее состояние ---


# Watchdog-таски разблокировки watcher при тишине stdout Claude.
# Ключ — session_id, значение — asyncio.Task с обратным отсчётом до resume_session.
# По одному таску на сессию — чтобы при нескольких параллельных сессиях watchdog
# каждой работал независимо.
WatchdogKey = tuple[str, BackendName]


class WatchdogTaskRegistry(dict[WatchdogKey, asyncio.Task]):
    """Registry that keeps backend-aware keys with Claude-string compatibility."""

    def _normalize_key(self, key: object) -> object:
        if isinstance(key, str):
            return (key, BackendName.CLAUDE)
        return key

    def __contains__(self, key: object) -> bool:
        return super().__contains__(self._normalize_key(key))

    def __getitem__(self, key: object) -> asyncio.Task:
        return super().__getitem__(self._normalize_key(key))

    def __setitem__(self, key: object, value: asyncio.Task) -> None:
        super().__setitem__(self._normalize_key(key), value)

    def get(self, key: object, default: object = None) -> object:
        return super().get(self._normalize_key(key), default)

    def pop(self, key: object, default: object = None) -> object:
        return super().pop(self._normalize_key(key), default)


watchdog_tasks: WatchdogTaskRegistry = WatchdogTaskRegistry()


# --- Вспомогательные функции ---


def _get_backend_display_name(backend: BackendName) -> str:
    """Возвращает человекочитаемое имя CLI-backend-а."""
    return coding_agent_backend.get_backend(backend).display_name


def _chat_is_not_viewing_all_projects(chat_id: int) -> bool:
    """Return whether the chat is outside global all-projects view."""
    return not all_projects_monitor.is_enabled_for_chat(chat_id)


def _save_last_delivered_message_index_for_later_project_view(
    session_id: str,
    backend: BackendName,
) -> None:
    """Save the session cursor so project view can deliver skipped messages."""
    if unread_buffer.restore_snapshot(session_id, backend) is not None:
        return

    last_seen_by_session = session_watcher.get_seen_counts_snapshot(backend)
    last_seen_position = last_seen_by_session.get(
        session_id,
        SessionUnreadState(raw_record_count=0, last_delivered_idx=-1),
    )
    unread_buffer.save_snapshot(
        session_id,
        backend,
        raw_record_count=last_seen_position.raw_record_count,
        last_delivered_idx=last_seen_position.last_delivered_idx,
    )


def build_file_task(file_path: str, caption: str | None, is_image: bool) -> str:
    """Формирует текстовое задание для Claude на основе скачанного файла."""
    if caption:
        return (
            f"Пользователь отправил файл с подписью: {caption}. "
            f"Файл: {file_path}. "
            "Прочитай файл инструментом Read и выполни задачу из подписи"
        )
    if is_image:
        return (
            "Пользователь отправил фотографию без подписи. "
            f"Файл: {file_path}. "
            "Прочитай файл и опиши, что на фотографии"
        )
    return (
        "Пользователь отправил файл без подписи. "
        f"Файл: {file_path}. "
        "Прочитай файл и опиши его содержимое"
    )


async def find_session_by_number(day_number: int) -> str | None:
    """Ищет сессию по дневному номеру в реестре и среди видимых сессий."""
    # Шаг 1: ищем в дневном реестре
    session_id = await daily_session_registry.get_session_id_by_number(day_number)
    if session_id is not None:
        return session_id

    # Шаг 2: регистрируем все видимые сессии и ищем повторно
    sessions = await session_reader.get_recent_sessions(config.WORKING_DIR)
    for session in sessions:
        await daily_session_registry.register_session(session.session_id)

    return await daily_session_registry.get_session_id_by_number(day_number)


# --- Watchdog-функции ---


async def agent_silence_watchdog(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> None:
    """Разблокирует watcher, если stdout Claude молчит дольше таймаута.

    Запускается рядом с pause_session в send_to_claude_and_respond.
    По таймауту AGENT_SILENCE_TIMEOUT_SECONDS вызывает resume_session —
    тогда watcher начнёт показывать пользователю прогресс из JSONL-файла.
    При отмене (новое progress-событие или завершение запроса) просто выходит.
    """
    try:
        await asyncio.sleep(AGENT_SILENCE_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return
    logger.info(
        "Agent silence watchdog: stdout молчит %d сек — разблокируем watcher для %s",
        AGENT_SILENCE_TIMEOUT_SECONDS, session_id,
    )
    await session_watcher.resume_session(session_id, backend)


def start_agent_silence_watchdog(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> None:
    """Запускает watchdog-таск для сессии, отменяя предыдущий если он был."""
    key = (session_id, backend)
    previous_task = watchdog_tasks.get(key)
    if previous_task is not None and not previous_task.done():
        previous_task.cancel()
    watchdog_tasks[key] = asyncio.create_task(
        agent_silence_watchdog(session_id, backend)
    )


def cancel_agent_silence_watchdog(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> None:
    """Отменяет watchdog-таск сессии и удаляет его из реестра."""
    task = watchdog_tasks.pop((session_id, backend), None)
    if task is not None and not task.done():
        task.cancel()


async def reset_watchdog_on_progress(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> None:
    """Обрабатывает новое progress-событие: возвращает watcher в паузу и перезапускает watchdog.

    Если watchdog ранее сработал по таймауту и успел снять паузу — ставим её обратно.
    Затем перезапускаем watchdog, чтобы следующие AGENT_SILENCE_TIMEOUT_SECONDS
    отсчитывались от момента этого нового события.
    """
    key = (session_id, backend)
    if key not in watchdog_tasks:
        # watchdog для этой сессии не запущен — значит мы не внутри
        # send_to_claude_and_respond (например, progress от другой сессии
        # при общем callback). Ничего не делаем.
        return
    session_watcher.pause_session(session_id, backend)
    start_agent_silence_watchdog(session_id, backend)


# --- Основные функции ---


def build_busy_message_if_busy(chat_id: int) -> str | None:
    """Возвращает вежливый текст, если Claude уже обрабатывает запрос.

    Если Claude свободен или нет привязанной сессии — возвращает None.
    Вызывается ДО затратных операций (скачивание файла с Telegram) —
    чтобы не тратить HTTP-пул на задание, которое всё равно будет отвергнуто.

    Проверка — fast-path оптимизация, атомарная защита в
    process_manager.send_message под _busy_lock: между этой проверкой
    и реальным захватом _busy_flags может вклиниться другой запрос.
    Это допустимо — цель не «гарантированно не ошибиться», а «в большинстве
    случаев не тратить HTTP-пул на заведомо отвергнутое задание».
    """
    active_session = session_manager.get_active_session(chat_id)
    if active_session is None:
        legacy_session_id = session_manager.get_bound_session(chat_id)
        if legacy_session_id is not None:
            active_session = ActiveSession(legacy_session_id, BackendName.CLAUDE)

    if active_session is None:
        # Нет привязанной сессии — дальше общий flow сам разберётся
        return None
    if process_manager.is_busy(active_session.session_id, active_session.backend):
        display_name = _get_backend_display_name(active_session.backend)
        return (
            f"{display_name} ещё обрабатывает предыдущее сообщение. "
            "Подождите или /stop"
        )
    return None


async def ensure_process_running(chat_id: int, session_id: str) -> bool:
    """Создаёт процесс Claude, если он не запущен. Возвращает True при успехе."""
    if process_manager.has_process(session_id):
        return True
    try:
        await process_manager.create_process(session_id)
        return True
    except process_manager.ProcessManagerError as error:
        logger.error("Не удалось создать процесс: %s", error)
        await _get_send_telegram_message()(
            chat_id, "Не удалось запустить Claude. Попробуйте ещё раз",
            parse_mode=None,
        )
        return False


async def handle_claude_result(
    chat_id: int,
    session_id: str,
    result: process_manager.SendResult,
    reply_to_message_id: int | None = None,
) -> str:
    """Обрабатывает результат от Claude: регистрирует сессию и отправляет ответ."""
    # Используем актуальный session_id из результата — callback уже обновил привязки
    actual_session_id = result.session_id
    backend = result.backend
    display_name = _get_backend_display_name(backend)

    day_number = await daily_session_registry.register_session(
        actual_session_id,
        backend,
    )

    if result.is_error:
        if result.permanent_error_kind is not None:
            message_text = _build_permanent_error_message(
                result.permanent_error_kind, display_name,
            )
        else:
            error_text = (
                result.error_text
                or result.text
                or f"Неизвестная ошибка {display_name}"
            )
            message_text = f"Ошибка {display_name}: {error_text}"
        await _get_send_telegram_message()(
            chat_id, message_text, parse_mode=None,
        )
    else:
        send_response_kwargs = {
            "is_final": True,
            "session_id": actual_session_id,
        }
        if reply_to_message_id is not None:
            send_response_kwargs["reply_to_message_id"] = reply_to_message_id
        await _get_send_response()(
            chat_id,
            result.text,
            day_number,
            backend,
            **send_response_kwargs,
        )

    return actual_session_id


async def _generate_and_store_session_summary(
    session_id: str,
    backend: BackendName,
    user_prompt: str,
) -> None:
    """Generates and stores a short summary for a newly-created session."""
    try:
        summary = await asyncio.wait_for(
            generate_session_summary(user_prompt, backend),
            timeout=SESSION_SUMMARY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Генерация summary сессии %s (%s) превысила %d секунд",
            session_id,
            backend.value,
            SESSION_SUMMARY_TIMEOUT_SECONDS,
        )
        return
    except Exception:
        logger.warning(
            "Генерация summary сессии %s (%s) упала",
            session_id,
            backend.value,
            exc_info=True,
        )
        return

    if summary:
        await daily_session_registry.update_session_summary(
            session_id,
            backend,
            summary,
        )


async def send_to_claude_and_respond(
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
) -> None:
    """Отправляет сообщение в Claude и обрабатывает ответ."""
    original_project_path = config.WORKING_DIR
    active_session = session_manager.get_active_session(chat_id)
    if active_session is None:
        legacy_session_id = session_manager.get_bound_session(chat_id)
        if legacy_session_id is not None:
            active_session = ActiveSession(legacy_session_id, BackendName.CLAUDE)
    if active_session is None:
        await _get_send_telegram_message()(
            chat_id, MONITORING_MODE_MESSAGE, parse_mode=None
        )
        return

    session_id = active_session.session_id
    backend = active_session.backend
    should_generate_session_summary = session_id.startswith("_new_")

    session_watcher.pause_session(session_id, backend)
    start_agent_silence_watchdog(session_id, backend)

    async def _on_progress(session_id: str, progress_text: str) -> None:
        """Промежуточное обновление — chat_id захвачен из замыкания."""
        if config.WORKING_DIR != original_project_path:
            return
        await reset_watchdog_on_progress(session_id, backend)
        day_number = await daily_session_registry.register_session(
            session_id,
            backend,
        )
        send_response_kwargs = {
            "is_final": False,
            "session_id": session_id,
        }
        anchor_message_id = reply_anchor_registry.get_anchor(
            original_project_path,
            backend,
            session_id,
        )
        if anchor_message_id is not None:
            send_response_kwargs["reply_to_message_id"] = anchor_message_id
        await _get_send_response()(
            chat_id,
            progress_text,
            day_number,
            backend,
            **send_response_kwargs,
        )

    async def _on_retry(session_id: str, attempt: int, max_attempts: int, error_reason: str) -> None:
        """Уведомление о ретрае — chat_id захвачен из замыкания."""
        if config.WORKING_DIR != original_project_path:
            return
        day_number = await daily_session_registry.register_session(
            session_id,
            backend,
        )
        display_name = _get_backend_display_name(backend)
        await _get_send_telegram_message()(
            chat_id,
            f"#{day_number} Ошибка {display_name}, повтор {attempt}/{max_attempts}: {error_reason}",
            parse_mode=None,
        )

    async def _on_session_id_changed(
        old_id: str,
        new_id: str,
        callback_backend: BackendName = backend,
    ) -> None:
        """Мгновенно обновляет привязки при смене session_id внутри потока событий."""
        nonlocal session_id
        if callback_backend != backend:
            logger.error(
                "Backend mismatch in session_id_callback: expected=%s got=%s",
                backend.value,
                callback_backend.value,
            )
            return
        reply_anchor_registry.move_anchor(
            original_project_path,
            backend,
            old_id,
            new_id,
        )
        if config.WORKING_DIR != original_project_path:
            # Проект сменился — не трогаем watcher и manager нового проекта,
            # но переносим watchdog и обновляем session_id для finally-cleanup
            cancel_agent_silence_watchdog(old_id, backend)
            start_agent_silence_watchdog(new_id, backend)
            session_id = new_id
            return
        session_watcher.update_session_id(old_id, new_id, backend)
        await session_manager.update_session_id(chat_id, old_id, new_id)
        # Переносим watchdog на новый session_id: старый таск отменяем,
        # новый запускаем — чтобы отсчёт тишины продолжался по актуальному id
        cancel_agent_silence_watchdog(old_id, backend)
        start_agent_silence_watchdog(new_id, backend)
        session_id = new_id

    try:
        if reply_to_message_id is not None:
            reply_anchor_registry.set_anchor(
                original_project_path,
                backend,
                session_id,
                reply_to_message_id,
            )
        result = await process_manager.send_message(
            session_id, text,
            progress_callback=_on_progress, retry_callback=_on_retry,
            session_id_callback=_on_session_id_changed,
            backend=backend,
            cwd=original_project_path,
        )
        if config.WORKING_DIR != original_project_path:
            logger.info(
                "Проект сменился во время обработки (was=%s, now=%s), "
                "подавляем доставку: session_id=%s",
                original_project_path, config.WORKING_DIR, session_id,
            )
        else:
            if result.is_error or _chat_is_not_viewing_all_projects(chat_id):
                session_id = await handle_claude_result(
                    chat_id,
                    session_id,
                    result,
                    reply_to_message_id=reply_anchor_registry.get_anchor(
                        original_project_path,
                        backend,
                        result.session_id,
                    ),
                )
            else:
                session_id = result.session_id
                _save_last_delivered_message_index_for_later_project_view(
                    session_id,
                    backend,
                )
                logger.info(
                    "Чат %d уже в режиме all, обычная доставка подавлена: "
                    "session_id=%s, backend=%s",
                    chat_id,
                    session_id,
                    backend.value,
                )
            if should_generate_session_summary and not result.is_error:
                await _generate_and_store_session_summary(
                    session_id,
                    backend,
                    text,
                )
    except process_manager.ProcessStoppedError:
        logger.info("Запрос прерван командой /stop: session_id=%s", session_id)
    except process_manager.ProcessNotFoundError:
        await _get_send_telegram_message()(
            chat_id, "Процесс агента не найден. Попробуйте /new",
            parse_mode=None,
        )
    except process_manager.ProcessManagerError as error:
        reply_anchor_registry.clear_anchor(original_project_path, backend, session_id)
        logger.warning(
            "Процесс занят (chat_id=%d): %s", chat_id, error,
        )
        await _get_send_telegram_message()(
            chat_id,
            f"{_get_backend_display_name(backend)} ещё обрабатывает предыдущее сообщение. Подождите или /stop",
            parse_mode=None,
        )
    except Exception:
        logger.error(
            "Ошибка при взаимодействии с Claude (chat_id=%d)", chat_id,
            exc_info=True,
        )
        await _get_send_telegram_message()(
            chat_id, "Произошла ошибка. Попробуйте ещё раз",
            parse_mode=None,
        )
    finally:
        cancel_agent_silence_watchdog(session_id, backend)
        if config.WORKING_DIR == original_project_path:
            await session_watcher.resume_session(session_id, backend)
            session_watcher.clear_handler_owns_final_delivery(session_id, backend)
