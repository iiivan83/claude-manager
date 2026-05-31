"""Тесты Telegram handlers для пользовательского ввода."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.constants import ChatAction

from claude_manager import (
    all_projects_monitor,
    session_manager,
    telegram_input_handlers as input_handlers,
)
import claude_manager.bot as bot_module
import claude_manager.config as config_module


ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345


@pytest.fixture(autouse=True)
def _setup_config():
    """Настраивает config для input-handler тестов."""
    original_allowed = config_module.ALLOWED_USER_IDS
    original_e2e = config_module.E2E_TEST_USER_ID
    config_module.ALLOWED_USER_IDS = {ALLOWED_USER_ID}
    config_module.E2E_TEST_USER_ID = None
    yield
    config_module.ALLOWED_USER_IDS = original_allowed
    config_module.E2E_TEST_USER_ID = original_e2e


@pytest.fixture(autouse=True)
def _setup_application():
    """Устанавливает фейковый Application для input-handler модуля."""
    mock_app = MagicMock()
    mock_app.bot = MagicMock()
    mock_app.bot.send_message = AsyncMock()
    original = bot_module._application
    bot_module._application = mock_app
    input_handlers.init_callbacks(
        bot_module._get_application_for_handlers,
        bot_module._has_access_for_handlers,
    )
    yield mock_app
    bot_module._application = original
    bot_module._init_handler_callbacks()


def _make_update(
    text: str = "test",
    *,
    chat_id: int = TEST_CHAT_ID,
    user_id: int = ALLOWED_USER_ID,
    message_id: int | None = None,
) -> MagicMock:
    """Создаёт фейковый Update для input-handler тестов."""
    update = MagicMock()
    update.message.text = text
    if message_id is not None:
        update.message.message_id = message_id
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
    update.message.reply_to_message = None
    return update


def _make_reply_update(
    text: str = "reply text",
    *,
    bot_message_id: int = 8001,
    message_id: int = 9001,
) -> MagicMock:
    """Create an update that replies to a bot message."""
    update = _make_update(text=text, message_id=message_id)
    update.message.reply_to_message = MagicMock()
    update.message.reply_to_message.message_id = bot_message_id
    return update


def _make_context() -> MagicMock:
    """Создаёт фейковый context для handler тестов."""
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    return context


class TestHandleMessage:
    """Тесты обработки текстовых сообщений."""

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_sends_to_claude(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Текстовое сообщение отправляется в Claude."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Посмотри файл main.py")
        context = _make_context()
        await input_handlers.handle_message(update, context)

        mock_send_to_claude.assert_called_once_with(
            TEST_CHAT_ID, "Посмотри файл main.py"
        )

    @pytest.mark.asyncio()
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_in_all_mode(
        self,
        mock_is_monitoring: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Сообщение в режиме /all — предупреждение."""
        mock_is_monitoring.return_value = True

        update = _make_update(text="Привет")
        context = _make_context()
        await input_handlers.handle_message(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "мониторинг" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_in_all_projects_mode_mentions_project(
        self,
        mock_is_monitoring: MagicMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """All-project mode text is blocked with a project/session warning."""
        mock_is_monitoring.return_value = True
        mock_is_all_projects.return_value = True

        update = _make_update(text="запрос")
        context = _make_context()
        await input_handlers.handle_message(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_typing_indicator_shown(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Индикатор 'печатает...' включается перед отправкой в Claude."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Тест")
        context = _make_context()
        await input_handlers.handle_message(update, context)

        context.bot.send_chat_action.assert_called_once_with(
            TEST_CHAT_ID, ChatAction.TYPING
        )

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_text_commands_sent_to_claude(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Текстовые слова 'стоп' отправляются как обычные сообщения."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="стоп")
        context = _make_context()
        await input_handlers.handle_message(update, context)

        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "стоп")

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_input_handlers.silence_mode_registry")
    async def test_silence_on_command_not_sent_to_claude(
        self,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """'Silence on' перехватывается и не уходит в Claude."""
        update = _make_update(text="Silence on")
        context = _make_context()
        await input_handlers.handle_message(update, context)

        mock_silence.enable.assert_called_once()
        mock_send_to_claude.assert_not_called()
        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "включён" in sent_text

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_input_handlers.silence_mode_registry")
    async def test_silence_off_command_not_sent_to_claude(
        self,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """'Silence off' перехватывается — disable() вызван, в Claude не ушло."""
        update = _make_update(text="Silence off")
        context = _make_context()
        await input_handlers.handle_message(update, context)

        mock_silence.disable.assert_called_once()
        mock_send_to_claude.assert_not_called()

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_input_handlers.silence_mode_registry")
    async def test_silence_command_case_insensitive(
        self,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Команды silence нечувствительны к регистру и пробелам."""
        context = _make_context()
        for text in ["silence ON", "SILENCE on", " Silence On "]:
            mock_silence.reset_mock()
            mock_send_to_claude.reset_mock()
            update = _make_update(text=text)
            await input_handlers.handle_message(update, context)
            mock_silence.enable.assert_called_once()
            mock_send_to_claude.assert_not_called()

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_input_handlers.silence_mode_registry")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_silence_command_works_in_monitoring_mode(
        self,
        mock_is_monitoring: MagicMock,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Silence on перехватывается ДО проверки monitoring mode."""
        mock_is_monitoring.return_value = True
        update = _make_update(text="Silence on")
        context = _make_context()
        await input_handlers.handle_message(update, context)

        mock_silence.enable.assert_called_once()
        mock_send_to_claude.assert_not_called()

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_input_handlers.silence_mode_registry")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_regular_text_not_intercepted_as_silence(
        self,
        mock_is_monitoring: MagicMock,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Похожий текст не перехватывается как silence command."""
        mock_is_monitoring.return_value = False
        context = _make_context()
        for text in ["Silence", "silence on please", "Turn silence on"]:
            mock_silence.reset_mock()
            mock_send_to_claude.reset_mock()
            update = _make_update(text=text)
            await input_handlers.handle_message(update, context)
            mock_silence.enable.assert_not_called()
            mock_silence.disable.assert_not_called()

    @pytest.mark.asyncio()
    async def test_handle_message_passes_incoming_message_id_as_anchor(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Text messages pass their Telegram message_id as anchor candidate."""
        send_to_claude = AsyncMock()
        monkeypatch.setattr(
            input_handlers.claude_interaction,
            "send_to_claude_and_respond",
            send_to_claude,
        )
        monkeypatch.setattr(
            session_manager,
            "is_monitoring_mode",
            lambda _chat_id: False,
        )

        await input_handlers.handle_message(
            _make_update("hello", message_id=321),
            _make_context(),
        )

        send_to_claude.assert_awaited_once_with(
            TEST_CHAT_ID,
            "hello",
            reply_to_message_id=321,
        )

    @pytest.mark.asyncio()
    async def test_handle_message_routes_reply_before_all_mode_guard(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Routed text reply is handled before monitoring-mode warning."""
        route_handler = AsyncMock(return_value=True)
        send_to_claude = AsyncMock()
        monkeypatch.setattr(
            input_handlers.reply_route_handler,
            "try_handle_text_reply",
            route_handler,
        )
        monkeypatch.setattr(
            input_handlers.claude_interaction,
            "send_to_claude_and_respond",
            send_to_claude,
        )
        monkeypatch.setattr(session_manager, "is_monitoring_mode", lambda _chat_id: True)

        update = _make_reply_update("ответ")
        context = _make_context()
        await input_handlers.handle_message(update, context)

        route_handler.assert_awaited_once_with(update, context)
        send_to_claude.assert_not_awaited()


class TestHandlePhoto:
    """Тесты обработки фотографий."""

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch(
        "claude_manager.telegram_input_handlers.telegram_file_downloader.download_and_save_file",
        new_callable=AsyncMock,
    )
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_photo_sends_to_claude(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Фото скачивается и отправляется в Claude."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/photo.jpg"

        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context()
        await input_handlers.handle_photo(update, context)

        mock_download.assert_called_once()
        mock_send_to_claude.assert_called_once()
        task_text = mock_send_to_claude.call_args[0][1]
        assert "/tmp/received_files/photo.jpg" in task_text

    @pytest.mark.asyncio()
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_photo_in_all_mode(
        self,
        mock_is_monitoring: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Фото в режиме /all — предупреждение."""
        mock_is_monitoring.return_value = True

        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context()
        await input_handlers.handle_photo(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "мониторинг" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_photo_in_all_projects_mode_mentions_project(
        self,
        mock_is_monitoring: MagicMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Photo input is blocked in global all mode."""
        mock_is_monitoring.return_value = True
        mock_is_all_projects.return_value = True

        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context()
        await input_handlers.handle_photo(update, context)

        sent_text = _setup_application.bot.send_message.call_args.args[1]
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()

    @pytest.mark.asyncio()
    async def test_handle_single_photo_passes_photo_message_id_as_anchor(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Single photo input passes its Telegram message_id as anchor candidate."""
        send_to_claude = AsyncMock()
        monkeypatch.setattr(
            input_handlers.claude_interaction,
            "send_to_claude_and_respond",
            send_to_claude,
        )
        monkeypatch.setattr(
            input_handlers.telegram_file_downloader,
            "download_and_save_file",
            AsyncMock(return_value="/tmp/photo.jpg"),
        )
        monkeypatch.setattr(
            session_manager,
            "is_monitoring_mode",
            lambda _chat_id: False,
        )
        update = _make_update(message_id=456)
        update.message.photo = [MagicMock()]
        update.message.caption = "describe"

        await input_handlers.handle_photo(update, _make_context())

        assert send_to_claude.await_args.kwargs["reply_to_message_id"] == 456

    @pytest.mark.asyncio()
    async def test_handle_photo_rejects_routed_reply_before_download(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Photo reply with route is rejected before file download."""
        route_handler = AsyncMock(return_value=True)
        download = AsyncMock()
        add_update = AsyncMock()
        monkeypatch.setattr(
            input_handlers.reply_route_handler,
            "try_handle_unsupported_attachment_reply",
            route_handler,
        )
        monkeypatch.setattr(
            input_handlers.telegram_file_downloader,
            "download_and_save_file",
            download,
        )
        monkeypatch.setattr(
            input_handlers.media_group_handler.media_group_aggregator,
            "add_update",
            add_update,
        )
        monkeypatch.setattr(session_manager, "is_monitoring_mode", lambda _chat_id: True)

        update = _make_reply_update()
        update.message.photo = [MagicMock()]
        update.message.media_group_id = "album-1"
        context = _make_context()
        await input_handlers.handle_photo(update, context)

        route_handler.assert_awaited_once_with(update, context)
        download.assert_not_awaited()
        add_update.assert_not_awaited()


class TestHandleDocument:
    """Тесты обработки документов."""

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch(
        "claude_manager.telegram_input_handlers.telegram_file_downloader.download_and_save_file",
        new_callable=AsyncMock,
    )
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_document_sends_to_claude(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Документ скачивается и отправляется в Claude."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/report.pdf"

        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "report.pdf"
        context = _make_context()
        await input_handlers.handle_document(update, context)

        mock_download.assert_called_once()
        mock_send_to_claude.assert_called_once()
        task_text = mock_send_to_claude.call_args[0][1]
        assert "/tmp/received_files/report.pdf" in task_text

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond",
        new_callable=AsyncMock,
    )
    @patch(
        "claude_manager.telegram_input_handlers.telegram_file_downloader.download_and_save_file",
        new_callable=AsyncMock,
    )
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_document_image_by_extension(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Документ с расширением изображения определяется как изображение."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/screenshot.png"

        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "screenshot.png"
        context = _make_context()
        await input_handlers.handle_document(update, context)

        mock_send_to_claude.assert_called_once()
        task_text = mock_send_to_claude.call_args[0][1]
        assert (
            "фотографию" in task_text
            or "подписью" in task_text
            or "изображени" in task_text
        )

    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_document_in_all_projects_mode_mentions_project(
        self,
        mock_is_monitoring: MagicMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Document input is blocked in global all mode."""
        mock_is_monitoring.return_value = True
        mock_is_all_projects.return_value = True

        update = _make_update()
        update.message.document = MagicMock()
        context = _make_context()
        await input_handlers.handle_document(update, context)

        sent_text = _setup_application.bot.send_message.call_args.args[1]
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()

    @pytest.mark.asyncio()
    async def test_handle_document_passes_document_message_id_as_anchor(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Document input passes its Telegram message_id as anchor candidate."""
        send_to_claude = AsyncMock()
        monkeypatch.setattr(
            input_handlers.claude_interaction,
            "send_to_claude_and_respond",
            send_to_claude,
        )
        monkeypatch.setattr(
            input_handlers.telegram_file_downloader,
            "download_and_save_file",
            AsyncMock(return_value="/tmp/report.pdf"),
        )
        monkeypatch.setattr(
            session_manager,
            "is_monitoring_mode",
            lambda _chat_id: False,
        )
        update = _make_update(message_id=457)
        update.message.document = MagicMock()
        update.message.document.file_name = "report.pdf"

        await input_handlers.handle_document(update, _make_context())

        assert send_to_claude.await_args.kwargs["reply_to_message_id"] == 457

    @pytest.mark.asyncio()
    async def test_handle_document_rejects_routed_reply_before_download(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Document reply with route is rejected before file download."""
        route_handler = AsyncMock(return_value=True)
        download = AsyncMock()
        monkeypatch.setattr(
            input_handlers.reply_route_handler,
            "try_handle_unsupported_attachment_reply",
            route_handler,
        )
        monkeypatch.setattr(
            input_handlers.telegram_file_downloader,
            "download_and_save_file",
            download,
        )
        monkeypatch.setattr(session_manager, "is_monitoring_mode", lambda _chat_id: True)

        update = _make_reply_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "report.pdf"
        context = _make_context()
        await input_handlers.handle_document(update, context)

        route_handler.assert_awaited_once_with(update, context)
        download.assert_not_awaited()
