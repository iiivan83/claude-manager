"""E2E тест: создание сессии и возврат в режим мониторинга."""

from tests.e2e.test_client import TelegramTestClient


async def test_new_session_and_back_to_all(
    telegram_client: TelegramTestClient,
) -> None:
    """Сценарий: /new создаёт сессию, /all возвращает в общий режим."""
    # Создаём новую сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_response()
    assert "Создана новая сессия" in response, (
        f"Ожидали 'Создана новая сессия', получили: {response}"
    )

    # Возвращаемся в режим мониторинга
    await telegram_client.send_command("/all")
    response = await telegram_client.wait_for_response()
    assert "Режим мониторинга" in response, (
        f"Ожидали 'Режим мониторинга', получили: {response}"
    )
