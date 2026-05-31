"""Тесты lifecycle handlers Telegram-приложения."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import (
    current_backend_registry,
    daily_session_registry,
    reply_route_registry,
    telegram_lifecycle_handlers as lifecycle_handlers,
)
import claude_manager.bot as bot_module
import claude_manager.config as config_module


ALLOWED_USER_ID = 12345
E2E_TEST_USER_ID = 77777


def _discard_background_coro(coro):
    """Close a background coroutine that is intentionally not run in unit tests."""
    coro.close()
    return MagicMock()


@pytest.fixture(autouse=True)
def _setup_config():
    """Настраивает config для lifecycle-handler тестов."""
    original_allowed = config_module.ALLOWED_USER_IDS
    original_e2e = config_module.E2E_TEST_USER_ID
    config_module.ALLOWED_USER_IDS = {ALLOWED_USER_ID}
    config_module.E2E_TEST_USER_ID = None
    lifecycle_handlers.init_callbacks(
        bot_module._get_application_for_handlers,
        bot_module._has_access_for_handlers,
    )
    yield
    config_module.ALLOWED_USER_IDS = original_allowed
    config_module.E2E_TEST_USER_ID = original_e2e
    bot_module._init_handler_callbacks()


class TestE2ePostInit:
    """Тесты post_init для E2E пользователя."""

    @patch(
        "claude_manager.telegram_lifecycle_handlers.telegram_sender.send_telegram_message",
        new_callable=AsyncMock,
    )
    @patch(
        "claude_manager.telegram_lifecycle_handlers.telegram_file_downloader.clean_old_received_files",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_lifecycle_handlers.session_manager")
    async def test_post_init_skips_e2e_user(
        self,
        mock_session_mgr: MagicMock,
        mock_clean: AsyncMock,
        mock_send: AsyncMock,
    ) -> None:
        """post_init не шлёт уведомление E2E-пользователю."""
        config_module.ALLOWED_USER_IDS = {111, E2E_TEST_USER_ID}
        config_module.E2E_TEST_USER_ID = E2E_TEST_USER_ID

        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}

        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        with patch(
            "claude_manager.telegram_lifecycle_handlers.asyncio.create_task",
            side_effect=_discard_background_coro,
        ), patch.object(
            daily_session_registry, "is_registry_loaded", return_value=False,
        ):
            await lifecycle_handlers.post_init(mock_app)

        sent_chat_ids = [call.args[1] for call in mock_send.call_args_list]
        assert 111 in sent_chat_ids
        assert E2E_TEST_USER_ID not in sent_chat_ids

    @pytest.mark.asyncio()
    @patch.object(current_backend_registry, "load_state")
    @patch(
        "claude_manager.telegram_lifecycle_handlers.telegram_file_downloader.clean_old_received_files",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_lifecycle_handlers.session_manager")
    async def test_post_init_loads_current_backend_registry(
        self,
        mock_session_mgr: MagicMock,
        _mock_clean: AsyncMock,
        mock_load_backend_state: MagicMock,
    ) -> None:
        """post_init загружает текущий backend до старта watcher-а."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        with patch(
            "claude_manager.telegram_lifecycle_handlers.asyncio.create_task",
            side_effect=_discard_background_coro,
        ), patch.object(
            daily_session_registry,
            "is_registry_loaded",
            return_value=True,
        ):
            await lifecycle_handlers.post_init(mock_app)

        mock_load_backend_state.assert_called_once()


class TestPostInit:
    """Тесты инициализации бота."""

    @pytest.fixture(autouse=True)
    def _disable_watcher_task(self):
        """post_init tests should not leave the infinite watcher task running."""
        with patch(
            "claude_manager.telegram_lifecycle_handlers.asyncio.create_task",
            side_effect=_discard_background_coro,
        ), patch.object(
            daily_session_registry,
            "is_registry_loaded",
            return_value=True,
        ):
            yield

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_lifecycle_handlers.telegram_file_downloader.clean_old_received_files",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_lifecycle_handlers.silence_mode_registry")
    @patch("claude_manager.telegram_lifecycle_handlers.session_manager")
    async def test_post_init_sets_commands(
        self,
        mock_session_mgr: MagicMock,
        mock_silence: MagicMock,
        mock_clean: AsyncMock,
    ) -> None:
        """post_init устанавливает меню команд."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await lifecycle_handlers.post_init(mock_app)

        mock_app.bot.set_my_commands.assert_called_once()
        commands = mock_app.bot.set_my_commands.call_args[0][0]
        assert len(commands) == len(lifecycle_handlers.BOT_COMMANDS)
        assert any(command.command == "all_projects" for command in commands)

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_lifecycle_handlers.telegram_file_downloader.clean_old_received_files",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_lifecycle_handlers.silence_mode_registry")
    @patch("claude_manager.telegram_lifecycle_handlers.session_manager")
    async def test_post_init_restores_bindings(
        self,
        mock_session_mgr: MagicMock,
        mock_silence: MagicMock,
        mock_clean: AsyncMock,
    ) -> None:
        """post_init восстанавливает привязки сессий."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {12345: "session-abc"}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await lifecycle_handlers.post_init(mock_app)

        mock_session_mgr.load_bindings.assert_called_once()

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_lifecycle_handlers.telegram_file_downloader.clean_old_received_files",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_lifecycle_handlers.silence_mode_registry")
    @patch("claude_manager.telegram_lifecycle_handlers.session_manager")
    async def test_post_init_continues_on_restore_error(
        self,
        mock_session_mgr: MagicMock,
        mock_silence: MagicMock,
        mock_clean: AsyncMock,
    ) -> None:
        """post_init не падает при ошибке восстановления состояния."""
        mock_session_mgr.load_bindings = AsyncMock(side_effect=OSError("disk error"))
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await lifecycle_handlers.post_init(mock_app)

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_lifecycle_handlers.telegram_file_downloader.clean_old_received_files",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_lifecycle_handlers.silence_mode_registry")
    @patch("claude_manager.telegram_lifecycle_handlers.session_manager")
    async def test_post_init_loads_silence_mode(
        self,
        mock_session_mgr: MagicMock,
        mock_silence: MagicMock,
        mock_clean: AsyncMock,
    ) -> None:
        """post_init вызывает silence_mode_registry.load_state()."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await lifecycle_handlers.post_init(mock_app)

        mock_silence.load_state.assert_called_once()

    @pytest.mark.asyncio()
    @patch.object(reply_route_registry, "load_routes")
    @patch(
        "claude_manager.telegram_lifecycle_handlers.telegram_file_downloader.clean_old_received_files",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_lifecycle_handlers.silence_mode_registry")
    @patch("claude_manager.telegram_lifecycle_handlers.session_manager")
    async def test_post_init_loads_reply_route_registry(
        self,
        mock_session_mgr: MagicMock,
        mock_silence: MagicMock,
        mock_clean: AsyncMock,
        mock_load_routes: MagicMock,
    ) -> None:
        """post_init loads reply routes saved before restart."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await lifecycle_handlers.post_init(mock_app)

        mock_load_routes.assert_called_once()

    @pytest.mark.asyncio()
    @patch(
        "claude_manager.telegram_lifecycle_handlers.telegram_file_downloader.clean_old_received_files",
        new_callable=AsyncMock,
    )
    @patch("claude_manager.telegram_lifecycle_handlers.silence_mode_registry")
    @patch("claude_manager.telegram_lifecycle_handlers.session_manager")
    async def test_post_init_handles_silence_mode_load_error(
        self,
        mock_session_mgr: MagicMock,
        mock_silence: MagicMock,
        mock_clean: AsyncMock,
    ) -> None:
        """post_init не падает при ошибке загрузки silence mode."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_silence.load_state.side_effect = Exception("test error")
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await lifecycle_handlers.post_init(mock_app)

        mock_app.bot.set_my_commands.assert_called_once()


class TestHandleRestart:
    """Тесты обработчика /restart."""

    async def test_sends_warning_message_and_writes_marker(
        self,
        tmp_path: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """handle_restart пишет маркер с chat_id и шлёт предупреждение."""
        marker_path = tmp_path / "restart-marker"
        monkeypatch.setattr(lifecycle_handlers, "RESTART_MARKER_PATH", marker_path)

        update = MagicMock()
        update.effective_chat.id = 12345
        update.effective_user.id = next(iter(config_module.ALLOWED_USER_IDS))
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        with patch(
            "claude_manager.telegram_lifecycle_handlers.asyncio.create_subprocess_exec",
            new=AsyncMock(),
        ):
            await lifecycle_handlers.handle_restart(update, context)

        assert marker_path.read_text() == "12345"
        context.bot.send_message.assert_awaited_once()
        sent_text = context.bot.send_message.call_args[0][1]
        assert "Перезапускаюсь" in sent_text
        assert "2" in sent_text

    async def test_launches_detached_systemctl_subprocess(
        self,
        tmp_path: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """handle_restart запускает detached systemctl restart."""
        monkeypatch.setattr(lifecycle_handlers, "RESTART_MARKER_PATH", tmp_path / "marker")

        update = MagicMock()
        update.effective_chat.id = 12345
        update.effective_user.id = next(iter(config_module.ALLOWED_USER_IDS))
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        with patch(
            "claude_manager.telegram_lifecycle_handlers.asyncio.create_subprocess_exec",
            new=AsyncMock(),
        ) as mock_exec:
            await lifecycle_handlers.handle_restart(update, context)

        mock_exec.assert_awaited_once()
        args, kwargs = mock_exec.call_args
        assert args[0] == "bash"
        assert args[1] == "-c"
        assert "systemctl --user restart claude-manager.service" in args[2]
        assert "sleep 2" in args[2]
        assert kwargs.get("start_new_session") is True
        assert kwargs.get("stdout") == asyncio.subprocess.DEVNULL
        assert kwargs.get("stderr") == asyncio.subprocess.DEVNULL
