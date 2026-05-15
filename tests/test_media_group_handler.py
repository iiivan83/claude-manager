"""Тесты модуля media_group_handler — агрегация медиа-групп Telegram."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import session_manager
from claude_manager.media_group_handler import (
    MEDIA_GROUP_DEBOUNCE_SECONDS,
    MEDIA_GROUP_DOWNLOAD_CONCURRENCY,
    MEDIA_GROUP_MAX_SIZE,
    MediaGroupAggregator,
    build_photo_group_task,
    download_photo_group_in_parallel,
    finalize_photo_group,
    media_group_aggregator,
)
import claude_manager.media_group_handler as media_group_module


# --- Фикстуры ---


ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345


def _make_update(
    text: str = "test",
    chat_id: int = TEST_CHAT_ID,
    user_id: int = ALLOWED_USER_ID,
) -> MagicMock:
    """Создаёт фейковый Update для тестов."""
    update = MagicMock()
    update.message.text = text
    update.message.chat.id = chat_id
    update.message.chat_id = chat_id
    update.effective_chat.id = chat_id
    update.message.from_user.id = user_id
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    update.message.caption = None
    update.message.photo = None
    update.message.document = None
    update.message.media_group_id = None
    return update


@pytest.fixture(autouse=True)
def _setup_media_group_callbacks():
    """Инициализирует callback-зависимости и bot._application для тестов."""
    from claude_manager import media_group_handler
    import claude_manager.bot as bot_module

    mock_app = MagicMock()
    mock_app.bot = MagicMock()
    mock_app.bot.get_file = AsyncMock()
    original_app = bot_module._application
    bot_module._application = mock_app

    media_group_handler.init_callbacks(
        send_to_claude=AsyncMock(),
        build_busy_message=MagicMock(return_value=None),
        send_telegram_message=AsyncMock(),
        send_chat_action=AsyncMock(),
    )
    yield
    bot_module._application = original_app




# --- Тесты MediaGroupAggregator ---


class TestMediaGroupAggregator:
    """Тесты агрегатора медиа-групп Telegram."""

    @pytest.fixture()
    def aggregator(self) -> MediaGroupAggregator:
        """Создаёт чистый экземпляр агрегатора для каждого теста."""
        return MediaGroupAggregator()

    @pytest.mark.asyncio()
    async def test_add_update_accumulates_in_group(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """Несколько add_update с одним media_group_id накапливаются в одной группе."""
        callback = AsyncMock()
        update_first = _make_update()
        update_second = _make_update()

        await aggregator.add_update("group-1", update_first, callback)
        await aggregator.add_update("group-1", update_second, callback)

        updates, _ = await aggregator.pop_group("group-1")
        assert len(updates) == 2
        assert updates[0] is update_first
        assert updates[1] is update_second

    @pytest.mark.asyncio()
    async def test_add_update_different_groups_isolated(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """Update с разными media_group_id попадают в разные группы."""
        callback = AsyncMock()
        update_a = _make_update()
        update_b = _make_update()

        await aggregator.add_update("group-a", update_a, callback)
        await aggregator.add_update("group-b", update_b, callback)

        updates_a, _ = await aggregator.pop_group("group-a")
        updates_b, _ = await aggregator.pop_group("group-b")
        assert len(updates_a) == 1
        assert updates_a[0] is update_a
        assert len(updates_b) == 1
        assert updates_b[0] is update_b

    @pytest.mark.asyncio()
    async def test_add_update_captures_first_caption(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """Агрегатор запоминает первое непустое caption из группы."""
        callback = AsyncMock()

        update_with_caption = _make_update()
        update_with_caption.message.caption = "Подпись альбома"

        update_without_caption = _make_update()
        update_without_caption.message.caption = None

        await aggregator.add_update("group-1", update_with_caption, callback)
        await aggregator.add_update("group-1", update_without_caption, callback)

        _, caption = await aggregator.pop_group("group-1")
        assert caption == "Подпись альбома"

    @pytest.mark.asyncio()
    async def test_add_update_keeps_first_caption_ignores_later(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """Если несколько Update имеют caption — запоминается первое."""
        callback = AsyncMock()

        update_first = _make_update()
        update_first.message.caption = "Первая подпись"
        update_second = _make_update()
        update_second.message.caption = "Вторая подпись"

        await aggregator.add_update("group-1", update_first, callback)
        await aggregator.add_update("group-1", update_second, callback)

        _, caption = await aggregator.pop_group("group-1")
        assert caption == "Первая подпись"

    @pytest.mark.asyncio()
    async def test_add_update_no_caption_returns_none(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """Если ни у одного Update нет caption — pop_group возвращает None."""
        callback = AsyncMock()
        update = _make_update()
        update.message.caption = None

        await aggregator.add_update("group-1", update, callback)

        _, caption = await aggregator.pop_group("group-1")
        assert caption is None

    @pytest.mark.asyncio()
    async def test_add_update_max_size_protection(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """При достижении MEDIA_GROUP_MAX_SIZE новые Update игнорируются."""
        callback = AsyncMock()

        # Добавляем ровно MAX_SIZE обновлений
        for i in range(MEDIA_GROUP_MAX_SIZE):
            update = _make_update()
            await aggregator.add_update("group-full", update, callback)

        # Ещё одно — должно быть проигнорировано
        extra_update = _make_update()
        await aggregator.add_update("group-full", extra_update, callback)

        updates, _ = await aggregator.pop_group("group-full")
        assert len(updates) == MEDIA_GROUP_MAX_SIZE

    @pytest.mark.asyncio()
    async def test_add_update_debounce_restarts_timer(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """Каждый add_update отменяет предыдущий debounce-таск и создаёт новый."""
        callback = AsyncMock()

        update_first = _make_update()
        await aggregator.add_update("group-1", update_first, callback)

        # Внутренний _pending_tasks должен содержать таск для "group-1"
        first_task = aggregator._pending_tasks.get("group-1")
        assert first_task is not None
        assert not first_task.done()

        # Второй add_update должен отменить первый таск
        update_second = _make_update()
        await aggregator.add_update("group-1", update_second, callback)

        # Даём event loop обработать CancelledError внутри первого таска
        await asyncio.sleep(0)

        # Первый таск отменён (done=True после обработки CancelledError)
        assert first_task.done()
        # Новый таск создан
        second_task = aggregator._pending_tasks.get("group-1")
        assert second_task is not None
        assert second_task is not first_task

    @pytest.mark.asyncio()
    async def test_debounce_calls_finalize_callback(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """После debounce-паузы вызывается finalize_callback."""
        callback = AsyncMock()
        update = _make_update()

        await aggregator.add_update("group-1", update, callback)

        # Ждём, пока debounce истечёт (с запасом)
        await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SECONDS + 0.3)

        callback.assert_awaited_once_with("group-1")

    @pytest.mark.asyncio()
    async def test_pop_group_atomic_extraction(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """pop_group извлекает данные и очищает внутренние буферы."""
        callback = AsyncMock()
        update = _make_update()
        update.message.caption = "test caption"

        await aggregator.add_update("group-1", update, callback)

        updates, caption = await aggregator.pop_group("group-1")
        assert len(updates) == 1
        assert caption == "test caption"

        # Повторный вызов возвращает пустой результат
        updates_again, caption_again = await aggregator.pop_group("group-1")
        assert updates_again == []
        assert caption_again is None

    @pytest.mark.asyncio()
    async def test_pop_group_nonexistent_returns_empty(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """pop_group для несуществующей группы возвращает ([], None)."""
        updates, caption = await aggregator.pop_group("never-seen-id")
        assert updates == []
        assert caption is None

    @pytest.mark.asyncio()
    async def test_shutdown_cancels_pending_tasks(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """shutdown отменяет все незавершённые debounce-таски."""
        callback = AsyncMock()
        update = _make_update()

        await aggregator.add_update("group-1", update, callback)
        task = aggregator._pending_tasks.get("group-1")
        assert task is not None
        assert not task.done()

        await aggregator.shutdown()

        # Даём event loop обработать CancelledError внутри таска
        await asyncio.sleep(0)

        assert task.done()

    @pytest.mark.asyncio()
    async def test_shutdown_clears_all_buffers(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """shutdown очищает группы, caption и pending_tasks."""
        callback = AsyncMock()
        update = _make_update()
        update.message.caption = "caption"

        await aggregator.add_update("group-1", update, callback)

        await aggregator.shutdown()

        assert aggregator._groups == {}
        assert aggregator._captions == {}
        assert aggregator._pending_tasks == {}

    @pytest.mark.asyncio()
    async def test_shutdown_prevents_callback_after_shutdown(
        self, aggregator: MediaGroupAggregator,
    ) -> None:
        """После shutdown debounce-таймер не вызывает callback."""
        callback = AsyncMock()
        update = _make_update()

        await aggregator.add_update("group-1", update, callback)
        await aggregator.shutdown()

        # Ждём больше debounce — callback не должен сработать
        await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SECONDS + 0.3)

        callback.assert_not_awaited()


# --- Тесты _download_photo_group_in_parallel ---





# --- Тесты _build_photo_group_task ---


class TestBuildPhotoGroupTask:
    """Тесты формирования текстового задания для Claude по альбому фотографий."""

    def test_single_photo_with_caption(self) -> None:
        """Одно фото с подписью — задание включает подпись и путь."""
        result = build_photo_group_task(["/tmp/photo1.jpg"], "Анализируй")
        assert "Анализируй" in result
        assert "/tmp/photo1.jpg" in result
        assert "1 фотографи" in result
        assert "подписью" in result
        assert "Прочитай все файлы" in result

    def test_multiple_photos_with_caption(self) -> None:
        """Несколько фото с подписью — все пути перечислены, количество верное."""
        paths = ["/tmp/photo1.jpg", "/tmp/photo2.jpg", "/tmp/photo3.jpg"]
        result = build_photo_group_task(paths, "Сравни")
        assert "3 фотографи" in result
        assert "Сравни" in result
        for path in paths:
            assert path in result
        assert "подписью" in result

    def test_single_photo_without_caption(self) -> None:
        """Одно фото без подписи — просит описать содержимое."""
        result = build_photo_group_task(["/tmp/photo1.jpg"], None)
        assert "/tmp/photo1.jpg" in result
        assert "без подписи" in result
        assert "опиши" in result.lower()

    def test_multiple_photos_without_caption(self) -> None:
        """Несколько фото без подписи — количество и пути перечислены."""
        paths = ["/tmp/a.jpg", "/tmp/b.jpg"]
        result = build_photo_group_task(paths, None)
        assert "2 фотографи" in result
        assert "без подписи" in result
        for path in paths:
            assert path in result

    def test_paths_listed_as_bullets(self) -> None:
        """Пути к файлам форматируются как список с маркерами."""
        paths = ["/tmp/img1.png", "/tmp/img2.png"]
        result = build_photo_group_task(paths, None)
        assert "- /tmp/img1.png" in result
        assert "- /tmp/img2.png" in result


# --- Тесты MediaGroupAggregator ---





# --- Тесты _download_photo_group_in_parallel ---


class TestDownloadPhotoGroupInParallel:
    """Тесты параллельного скачивания фотографий из медиа-группы."""

    @pytest.mark.asyncio()
    @patch("claude_manager.telegram_file_downloader.download_and_save_file", new_callable=AsyncMock)
    async def test_downloads_all_photos(
        self, mock_download: AsyncMock,
    ) -> None:
        """Все фото из группы скачиваются, пути возвращаются."""
        mock_download.side_effect = ["/tmp/photo1.jpg", "/tmp/photo2.jpg"]

        updates = [_make_update(), _make_update()]
        result = await download_photo_group_in_parallel(updates, "test-group")

        assert result == ["/tmp/photo1.jpg", "/tmp/photo2.jpg"]
        assert mock_download.call_count == 2

    @pytest.mark.asyncio()
    @patch("claude_manager.telegram_file_downloader.download_and_save_file", new_callable=AsyncMock)
    async def test_failed_download_skipped_in_result(
        self, mock_download: AsyncMock,
    ) -> None:
        """Если одно скачивание упало — оно пропускается, остальные возвращаются."""
        mock_download.side_effect = [
            "/tmp/photo1.jpg",
            Exception("Network error"),
            "/tmp/photo3.jpg",
        ]

        updates = [_make_update(), _make_update(), _make_update()]
        result = await download_photo_group_in_parallel(updates, "test-group")

        assert result == ["/tmp/photo1.jpg", "/tmp/photo3.jpg"]

    @pytest.mark.asyncio()
    @patch("claude_manager.telegram_file_downloader.download_and_save_file", new_callable=AsyncMock)
    async def test_all_downloads_fail_returns_empty(
        self, mock_download: AsyncMock,
    ) -> None:
        """Если все скачивания упали — возвращается пустой список."""
        mock_download.side_effect = Exception("Network error")

        updates = [_make_update(), _make_update()]
        result = await download_photo_group_in_parallel(updates, "test-group")

        assert result == []

    @pytest.mark.asyncio()
    @patch("claude_manager.telegram_file_downloader.download_and_save_file", new_callable=AsyncMock)
    async def test_empty_updates_returns_empty(
        self, mock_download: AsyncMock,
    ) -> None:
        """Пустой список Update — пустой результат, скачивание не вызывается."""
        result = await download_photo_group_in_parallel([], "test-group")

        assert result == []
        mock_download.assert_not_called()

    @pytest.mark.asyncio()
    @patch("claude_manager.telegram_file_downloader.download_and_save_file", new_callable=AsyncMock)
    async def test_concurrency_limited_by_semaphore(
        self, mock_download: AsyncMock,
    ) -> None:
        """Количество одновременных скачиваний ограничено MEDIA_GROUP_DOWNLOAD_CONCURRENCY."""
        # Отслеживаем максимальное число одновременно работающих скачиваний
        concurrent_count = 0
        max_concurrent = 0

        original_return = "/tmp/photo.jpg"

        async def _track_concurrency(update):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.05)  # Имитация сетевой задержки
            concurrent_count -= 1
            return original_return

        mock_download.side_effect = _track_concurrency

        # Больше Update, чем лимит concurrency
        updates = [_make_update() for _ in range(MEDIA_GROUP_DOWNLOAD_CONCURRENCY + 3)]
        await download_photo_group_in_parallel(updates, "test-group")

        assert max_concurrent <= MEDIA_GROUP_DOWNLOAD_CONCURRENCY


# --- Тесты _finalize_photo_group ---





# --- Тесты _finalize_photo_group ---


class TestFinalizePhotoGroup:
    """Тесты финализации медиа-группы: скачивание и отправка в Claude."""

    @pytest.mark.asyncio()
    @patch("claude_manager.media_group_handler._send_to_claude_callback", new_callable=AsyncMock)
    @patch(
        "claude_manager.media_group_handler.download_photo_group_in_parallel",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.media_group_handler._build_busy_message_callback")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_happy_path_downloads_and_sends_to_claude(
        self,
        mock_monitoring: MagicMock,
        mock_busy: MagicMock,
        mock_download_group: AsyncMock,
        mock_send_claude: AsyncMock,
    ) -> None:
        """Группа скачивается и задание отправляется в Claude."""
        mock_monitoring.return_value = False
        mock_busy.return_value = None
        mock_download_group.return_value = ["/tmp/p1.jpg", "/tmp/p2.jpg"]

        # Подготовим агрегатор с данными
        update_a = _make_update()
        update_a.message.caption = "Что на фото?"
        update_b = _make_update()
        update_b.message.caption = None

        aggregator = media_group_aggregator
        await aggregator.add_update(
            "finalize-test", update_a, AsyncMock(),
        )
        await aggregator.add_update(
            "finalize-test", update_b, AsyncMock(),
        )

        await finalize_photo_group("finalize-test")

        mock_download_group.assert_awaited_once()
        mock_send_claude.assert_awaited_once()
        task_text = mock_send_claude.call_args[0][1]
        assert "/tmp/p1.jpg" in task_text
        assert "/tmp/p2.jpg" in task_text
        assert "Что на фото?" in task_text

    @pytest.mark.asyncio()
    async def test_empty_group_exits_silently(self) -> None:
        """Финализатор с пустой группой логирует warning и выходит."""
        # pop_group для несуществующего ID возвращает ([], None)
        # Финализатор должен просто выйти без ошибок
        await finalize_photo_group("non-existent-group-id")
        # Если дошли сюда — не упало, тест пройден

    @pytest.mark.asyncio()
    @patch("claude_manager.media_group_handler._send_telegram_message_callback", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_monitoring_mode_sends_warning(
        self,
        mock_monitoring: MagicMock,
        mock_send_msg: AsyncMock,
    ) -> None:
        """В режиме мониторинга — предупреждение вместо скачивания."""
        mock_monitoring.return_value = True

        update = _make_update()
        aggregator = media_group_aggregator
        await aggregator.add_update("monitor-test", update, AsyncMock())

        await finalize_photo_group("monitor-test")

        mock_send_msg.assert_awaited_once()
        sent_text = mock_send_msg.call_args[0][1]
        assert "мониторинг" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch("claude_manager.media_group_handler._send_telegram_message_callback", new_callable=AsyncMock)
    @patch("claude_manager.media_group_handler._build_busy_message_callback")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_busy_claude_sends_busy_message(
        self,
        mock_monitoring: MagicMock,
        mock_busy: MagicMock,
        mock_send_msg: AsyncMock,
    ) -> None:
        """Если Claude занят — отправляет сообщение о занятости."""
        mock_monitoring.return_value = False
        mock_busy.return_value = "Claude занят"

        update = _make_update()
        aggregator = media_group_aggregator
        await aggregator.add_update("busy-test", update, AsyncMock())

        await finalize_photo_group("busy-test")

        mock_send_msg.assert_awaited_once()
        sent_text = mock_send_msg.call_args[0][1]
        assert "занят" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch("claude_manager.media_group_handler._send_telegram_message_callback", new_callable=AsyncMock)
    @patch(
        "claude_manager.media_group_handler.download_photo_group_in_parallel",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.media_group_handler._build_busy_message_callback")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_all_downloads_failed_sends_error(
        self,
        mock_monitoring: MagicMock,
        mock_busy: MagicMock,
        mock_download_group: AsyncMock,
        mock_send_msg: AsyncMock,
    ) -> None:
        """Если все скачивания упали — сообщение об ошибке пользователю."""
        mock_monitoring.return_value = False
        mock_busy.return_value = None
        mock_download_group.return_value = []  # Все скачивания провалились

        update = _make_update()
        aggregator = media_group_aggregator
        await aggregator.add_update("fail-test", update, AsyncMock())

        await finalize_photo_group("fail-test")

        mock_send_msg.assert_awaited_once()
        sent_text = mock_send_msg.call_args[0][1]
        assert "не удалось" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch("claude_manager.media_group_handler._send_to_claude_callback", new_callable=AsyncMock)
    @patch("claude_manager.media_group_handler._send_telegram_message_callback", new_callable=AsyncMock)
    @patch(
        "claude_manager.media_group_handler.download_photo_group_in_parallel",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.media_group_handler._build_busy_message_callback")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_busy_after_download_sends_busy_message(
        self,
        mock_monitoring: MagicMock,
        mock_busy: MagicMock,
        mock_download_group: AsyncMock,
        mock_send_msg: AsyncMock,
        mock_send_claude: AsyncMock,
    ) -> None:
        """Если Claude стал занят ПОСЛЕ скачивания — не отправляем в Claude."""
        mock_monitoring.return_value = False
        # Первый вызов — свободен, второй (после скачивания) — занят
        mock_busy.side_effect = [None, "Claude занят после download"]
        mock_download_group.return_value = ["/tmp/photo.jpg"]

        update = _make_update()
        aggregator = media_group_aggregator
        await aggregator.add_update("busy-after-test", update, AsyncMock())

        await finalize_photo_group("busy-after-test")

        # send_to_claude_and_respond НЕ вызывается
        mock_send_claude.assert_not_awaited()
        # Пользователю отправляется сообщение о занятости
        mock_send_msg.assert_awaited_once()


# --- Тесты констант медиа-группы ---





# --- Тесты констант медиа-группы ---


class TestMediaGroupConstants:
    """Тесты значений констант медиа-группы."""

    def test_debounce_positive(self) -> None:
        """Debounce должен быть положительным числом."""
        assert MEDIA_GROUP_DEBOUNCE_SECONDS > 0

    def test_max_size_matches_telegram_limit(self) -> None:
        """Максимальный размер группы — 10 (лимит Telegram альбома)."""
        assert MEDIA_GROUP_MAX_SIZE == 10

    def test_concurrency_positive_and_reasonable(self) -> None:
        """Concurrency — положительное число, не больше пула соединений."""
        assert MEDIA_GROUP_DOWNLOAD_CONCURRENCY > 0
        assert MEDIA_GROUP_DOWNLOAD_CONCURRENCY <= 32  # HTTP_CONNECTION_POOL_SIZE


# --- Тесты _build_busy_message_if_busy ---
