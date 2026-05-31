"""Tests for session-list request preview text."""

from claude_manager.session_request_preview import clean_session_request_preview


def test_file_caption_task_preview_uses_user_caption() -> None:
    """File task boilerplate is replaced with the original user caption."""
    raw_text = (
        "Пользователь отправил файл с подписью: Добавь в /sessions суть "
        "исходного запроса вместо служебного начала. "
        "Файл: /Users/ivan/Desktop/received/image.jpg. "
        "Прочитай файл инструментом Read и выполни задачу из подписи"
    )

    preview = clean_session_request_preview(raw_text)

    assert preview == (
        "Добавь в /sessions суть исходного запроса вместо служебного начала"
    )


def test_regular_prompt_preview_stays_unchanged_except_cleanup() -> None:
    """Normal text prompts are cleaned without changing user-authored content."""
    preview = clean_session_request_preview("Посмотри   файл\nmain.py")

    assert preview == "Посмотри файл main.py"


def test_long_prompt_preview_is_not_truncated() -> None:
    """Long session-list previews keep the full cleaned request text."""
    long_text = "А" * 200

    preview = clean_session_request_preview(long_text)

    assert preview == long_text


def test_without_caption_file_task_has_short_human_preview() -> None:
    """File uploads without captions get a compact fallback preview."""
    raw_text = (
        "Пользователь отправил файл без подписи. "
        "Файл: /tmp/report.pdf. Прочитай файл и опиши его содержимое"
    )

    preview = clean_session_request_preview(raw_text)

    assert preview == "Файл без подписи"
