"""Агрегация фотографий из медиа-группы Telegram в единый пакет.

Telegram доставляет альбом как N отдельных Update с общим media_group_id,
интервалы между Update -- 50-300 мс. Модуль накапливает Update, ждёт
debounce-паузу и формирует одно задание для Claude по всему альбому.

Зависимости от транспортного слоя (bot.py) разрешены через callback-функции:
bot.py вызывает init_callbacks() при старте, передавая замыкания на свои
внутренние функции. media_group_handler.py НЕ импортирует bot.py.
"""

import asyncio
import logging

from telegram import Update

from claude_manager import session_manager, telegram_file_downloader

logger = logging.getLogger(__name__)


# --- Константы ---


# Время ожидания после последнего пришедшего фото медиа-группы перед финализацией (секунды).
# Telegram доставляет Update альбома не атомарно: каждое фото приходит отдельным Update
# с интервалом 50-300 мс. Debounce сбрасывается на каждое новое фото -- финализируем
# только когда прошло MEDIA_GROUP_DEBOUNCE_SECONDS без новых фото.
# 0.8 с -- компромисс между дрожащей сетью и скоростью реакции.
MEDIA_GROUP_DEBOUNCE_SECONDS = 0.8

# Предельный размер одной медиа-группы в фото.
# Telegram сам не присылает больше 10 в одном альбоме, но защищаемся
# от аномалии (например, бага в клиенте или в чужом коде).
MEDIA_GROUP_MAX_SIZE = 10

# Максимальное число параллельных скачиваний файлов внутри одной медиа-группы.
# HTTP-пул HTTP_CONNECTION_POOL_SIZE = 32. 4 из 32 оставляет запас 28 соединений
# на send_message/send_chat_action и параллельные текстовые хендлеры.
MEDIA_GROUP_DOWNLOAD_CONCURRENCY = 4


# --- Callback-зависимости от транспортного слоя ---
# Инициализируются через init_callbacks() при старте бота.
# До вызова init_callbacks() функция finalize_photo_group упадёт с RuntimeError.

# async (chat_id: int, text: str) -> None
_send_to_claude_callback = None

# (chat_id: int) -> str | None
_build_busy_message_callback = None

# async (chat_id: int, text: str, parse_mode: str | None) -> None
_send_telegram_message_callback = None

# async (chat_id: int) -> None
_send_chat_action_callback = None


def init_callbacks(
    send_to_claude,
    build_busy_message,
    send_telegram_message,
    send_chat_action,
) -> None:
    """Инъекция callback-зависимостей от транспортного слоя.

    Вызывается один раз из bot.py при инициализации бота (setup_bot или post_init).
    Разрывает циклическую зависимость: media_group_handler не импортирует bot.
    """
    global _send_to_claude_callback, _build_busy_message_callback
    global _send_telegram_message_callback, _send_chat_action_callback
    _send_to_claude_callback = send_to_claude
    _build_busy_message_callback = build_busy_message
    _send_telegram_message_callback = send_telegram_message
    _send_chat_action_callback = send_chat_action


# --- Вспомогательные функции ---


def build_photo_group_task(
    file_paths: list[str], caption: str | None,
) -> str:
    """Формирует единое задание для Claude по альбому фото.

    Отличается от _build_file_task тем, что путь -- список, а не один файл,
    и в инструкции прямо просит Claude прочитать ВСЕ файлы с учётом общей подписи.
    """
    paths_joined = "\n".join(f"- {p}" for p in file_paths)
    photo_count = len(file_paths)
    if caption:
        return (
            f"Пользователь отправил альбом из {photo_count} фотографий "
            f"с подписью: {caption}.\n"
            f"Файлы:\n{paths_joined}\n"
            "Прочитай все файлы инструментом Read и выполни задачу из подписи "
            "с учётом всех фотографий."
        )
    return (
        f"Пользователь отправил альбом из {photo_count} фотографий без подписи.\n"
        f"Файлы:\n{paths_joined}\n"
        "Прочитай все файлы и опиши, что на фотографиях."
    )


def select_album_anchor_message_id(updates: list[Update]) -> int:
    """Choose the Telegram message_id used as the album reply anchor."""
    for update in updates:
        if update.message.caption:
            return update.message.message_id
    return updates[0].message.message_id


# --- Класс-агрегатор ---


class MediaGroupAggregator:
    """Собирает фотографии из одной медиа-группы Telegram в единый пакет.

    Telegram доставляет альбом как N отдельных Update с общим media_group_id,
    интервалы между Update -- 50-300 мс. Агрегатор накапливает эти Update
    по ключу media_group_id и запускает финализатор через
    MEDIA_GROUP_DEBOUNCE_SECONDS после последнего пришедшего фото.
    Debounce-таймер сбрасывается на каждый новый add_update -- финализация
    срабатывает только когда пауза между фото превысила MEDIA_GROUP_DEBOUNCE_SECONDS.

    asyncio.Lock создаётся на import-time (модульный синглтон ниже
    инициализируется до запуска event loop). В CPython 3.10+ конструктор
    asyncio.Lock() не привязывается к конкретному loop -- привязка ленивая,
    при первом await self._lock.acquire() внутри уже запущенного loop.
    Проект требует Python 3.13, поэтому создание Lock на import-time безопасно.
    """

    def __init__(self) -> None:
        # Накопленные Update по ключу media_group_id
        self._groups: dict[str, list[Update]] = {}
        # Первое непустое caption из пришедших Update группы
        self._captions: dict[str, str | None] = {}
        # Запланированные задачи-финализаторы по ключу media_group_id
        self._pending_tasks: dict[str, asyncio.Task] = {}
        # Защита от гонки между add_update и финализатором
        self._lock: asyncio.Lock = asyncio.Lock()

    async def add_update(
        self,
        media_group_id: str,
        update: Update,
        finalize_callback,
    ) -> None:
        """Добавляет Update в буфер группы и перепланирует debounce-таймер.

        finalize_callback -- корутина вида async (media_group_id: str) -> None.
        Колбэк сам внутри себя вызывает pop_group -- это делает колбэк
        единственным владельцем вынутых данных.
        """
        async with self._lock:
            group_list = self._groups.setdefault(media_group_id, [])
            if len(group_list) >= MEDIA_GROUP_MAX_SIZE:
                # Защита от аномалии: Telegram не присылает больше 10,
                # но мы не доверяем чужому коду
                logger.warning(
                    "Медиа-группа %s превысила MEDIA_GROUP_MAX_SIZE=%d, "
                    "новый Update проигнорирован",
                    media_group_id, MEDIA_GROUP_MAX_SIZE,
                )
                return
            group_list.append(update)

            # Первое непустое caption из всех Update группы -- запоминаем навсегда.
            # Telegram обычно кладёт caption только в первое фото, но если вдруг
            # в нескольких -- берём ПЕРВОЕ непустое в порядке прихода
            caption = update.message.caption
            if caption and media_group_id not in self._captions:
                self._captions[media_group_id] = caption

            # Debounce: отменяем ранее запланированный финализатор,
            # планируем новый. Реально финализируем через N секунд
            # после ПОСЛЕДНЕГО фото группы
            previous_task = self._pending_tasks.get(media_group_id)
            if previous_task is not None and not previous_task.done():
                previous_task.cancel()
            self._pending_tasks[media_group_id] = asyncio.create_task(
                self._run_after_debounce(media_group_id, finalize_callback)
            )

    async def _run_after_debounce(
        self,
        media_group_id: str,
        finalize_callback,
    ) -> None:
        """Ждёт debounce-паузу и вызывает финализатор группы.

        CancelledError -- штатный сценарий debounce: пришло ещё одно фото
        и таймер перезапущен. Просто выходим, новую таску уже создал add_update.
        """
        try:
            await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        await finalize_callback(media_group_id)

    async def pop_group(
        self,
        media_group_id: str,
    ) -> tuple[list[Update], str | None]:
        """Атомарно извлекает всю группу и caption, очищая внутренние буферы.

        Возвращает пару (updates, caption). Если группа уже удалена
        или не существует -- возвращает ([], None).
        """
        async with self._lock:
            updates = self._groups.pop(media_group_id, [])
            self._pending_tasks.pop(media_group_id, None)
            caption = self._captions.pop(media_group_id, None)
            return updates, caption

    async def shutdown(self) -> None:
        """Отменяет все незавершённые таски-финализаторы и очищает буферы.

        Вызывается при graceful shutdown бота. Без этого задачи-финализаторы
        могут вызвать callback уже после остановки приложения и засорить лог
        warning'ами об отменённых корутинах.
        """
        async with self._lock:
            for task in self._pending_tasks.values():
                if not task.done():
                    task.cancel()
            self._pending_tasks.clear()
            self._groups.clear()
            self._captions.clear()


# Единый экземпляр агрегатора на весь бот: бот однопользовательский,
# шардить по чатам не нужно (media_group_id сам по себе уникален глобально)
media_group_aggregator: MediaGroupAggregator = MediaGroupAggregator()


# --- Публичные функции ---


async def download_photo_group_in_parallel(
    updates: list[Update], media_group_id: str,
) -> list[str]:
    """Скачивает все фото группы параллельно с ограничением concurrency.

    Возвращает список путей к успешно скачанным файлам. Падения
    отдельных скачиваний логируются и не прерывают остальные --
    лучше отправить Claude неполный альбом, чем ничего.
    """
    semaphore = asyncio.Semaphore(MEDIA_GROUP_DOWNLOAD_CONCURRENCY)

    async def _download_one(update: Update) -> str | None:
        async with semaphore:
            try:
                return await telegram_file_downloader.download_and_save_file(
                    update, _get_bot(),
                )
            except Exception:
                logger.error(
                    "Ошибка скачивания фото из группы %s",
                    media_group_id, exc_info=True,
                )
                return None

    download_results = await asyncio.gather(
        *[_download_one(u) for u in updates]
    )
    return [path for path in download_results if path is not None]


def _get_bot():
    """Возвращает объект bot из транспортного слоя для скачивания файлов.

    Используется download_photo_group_in_parallel для передачи
    в telegram_file_downloader.download_and_save_file.
    Lazy-import bot._application.bot -- разрывает циклическую зависимость
    на уровне import-time (import происходит при первом вызове функции,
    а не при загрузке модуля).
    """
    from claude_manager import bot as _bot_module
    return _bot_module._application.bot


async def finalize_photo_group(media_group_id: str) -> None:
    """Финализатор медиа-группы: скачивает все фото и формирует задание для Claude.

    Вызывается через MEDIA_GROUP_DEBOUNCE_SECONDS после последнего пришедшего
    фото группы. Извлекает накопленные Update из агрегатора, проверяет,
    что режим мониторинга не включился во время debounce и Claude не занят,
    параллельно скачивает файлы и отправляет единое задание в Claude.
    """
    if _send_to_claude_callback is None:
        raise RuntimeError(
            "media_group_handler.init_callbacks() не вызван -- "
            "callback-зависимости не инициализированы"
        )

    updates, common_caption = await media_group_aggregator.pop_group(
        media_group_id,
    )
    if not updates:
        # Группа уже извлечена кем-то ещё, либо теоретический баг --
        # логируем warning и тихо выходим
        logger.warning(
            "Финализатор медиа-группы %s вызван, но группа пуста",
            media_group_id,
        )
        return

    # Все Update из одной группы принадлежат одному чату
    chat_id = updates[0].effective_chat.id

    # Импортируем константу из claude_interaction (не из bot — bot уже не хранит реэкспорты).
    # Lazy-import внутри функции -- разрывает циклическую зависимость на import-time.
    from claude_manager.claude_interaction import MONITORING_MODE_MESSAGE

    try:
        # Ранняя проверка #1: режим /all мог включиться во время debounce.
        # Пользователь прислал альбом, потом /all до финализации --
        # файлы скачивать бессмысленно, в Claude писать нельзя
        if session_manager.is_monitoring_mode(chat_id):
            await _send_telegram_message_callback(
                chat_id, MONITORING_MODE_MESSAGE, None,
            )
            return

        # Ранняя проверка #2: Claude занят другим запросом.
        # Не тратим HTTP-пул на скачивание файлов, которые всё равно
        # будут отвергнуты process_manager.send_message
        busy_message = _build_busy_message_callback(chat_id)
        if busy_message is not None:
            await _send_telegram_message_callback(chat_id, busy_message, None)
            return

        saved_paths = await download_photo_group_in_parallel(
            updates, media_group_id,
        )

        if not saved_paths:
            await _send_telegram_message_callback(
                chat_id,
                "Не удалось скачать ни одно фото из альбома. "
                "Попробуйте отправить ещё раз",
                None,
            )
            return

        if len(saved_paths) < len(updates):
            logger.warning(
                "Медиа-группа %s: скачано %d из %d файлов",
                media_group_id, len(saved_paths), len(updates),
            )

        # Повторная проверка занятости после скачивания.
        # Между `await gather(...)` и send_message другой хендлер мог
        # захватить _busy_flags. Строгая атомарная защита живёт в
        # process_manager.send_message под _busy_lock -- эта проверка
        # просто избавляет от лишнего раунда запрос-ответ в большинстве случаев
        busy_message_after_download = _build_busy_message_callback(chat_id)
        if busy_message_after_download is not None:
            await _send_telegram_message_callback(
                chat_id, busy_message_after_download, None,
            )
            return

        task_text = build_photo_group_task(saved_paths, common_caption)
        reply_to_message_id = select_album_anchor_message_id(updates)

        try:
            await _send_chat_action_callback(chat_id)
        except Exception as exc:
            logger.warning(
                "send_chat_action не удался в finalize_photo_group: %s", exc,
            )

        await _send_to_claude_callback(
            chat_id,
            task_text,
            reply_to_message_id=reply_to_message_id,
        )
    finally:
        # pop_group уже очистил состояние агрегатора до try, finally здесь --
        # на будущее: если добавятся внутренние состояния-следы (ретраи,
        # частичные буферы), их очистка должна жить здесь. Явная точка расширения.
        logger.debug(
            "Финализация медиа-группы %s завершена (updates=%d)",
            media_group_id, len(updates),
        )
