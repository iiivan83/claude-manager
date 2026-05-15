"""Чёрный ящик: проверка BOT_COMMANDS после фикса DEV-1/DEV-2.

Проверяет корректность описаний без знания о конкретных новых значениях —
только что старые некорректные тексты исчезли и структура сохранена.
"""

from claude_manager.bot import BOT_COMMANDS


# Все команды, которые должны быть в меню
EXPECTED_COMMAND_NAMES = {"new", "sessions", "all", "stop", "projects"}

# Старые некорректные описания, которых не должно быть после фикса
OLD_INCORRECT_DESCRIPTIONS = {
    "Мониторинг всех сессий",
    "Список проектов для переключения",
}


class TestBotCommandsBlackbox:
    """Чёрный ящик: BOT_COMMANDS содержит все команды с корректными описаниями."""

    def test_all_commands_present(self) -> None:
        """Все 5 команд присутствуют в BOT_COMMANDS."""
        actual_names = {name for name, _description in BOT_COMMANDS}
        assert actual_names == EXPECTED_COMMAND_NAMES, (
            f"Ожидались команды {EXPECTED_COMMAND_NAMES}, "
            f"получили {actual_names}"
        )

    def test_no_old_descriptions(self) -> None:
        """Ни одна команда не содержит старых некорректных описаний."""
        for name, description in BOT_COMMANDS:
            assert description not in OLD_INCORRECT_DESCRIPTIONS, (
                f"Команда /{name} всё ещё содержит старое описание: «{description}»"
            )

    def test_descriptions_not_empty(self) -> None:
        """Все описания непустые."""
        for name, description in BOT_COMMANDS:
            assert description.strip(), (
                f"Описание команды /{name} пустое"
            )
