"""Session-list preview cleanup for original user requests."""

import re

DEFAULT_PREVIEW_MAX_LENGTH: int | None = None
FILE_CAPTION_TASK_PREFIX = "Пользователь отправил файл с подписью: "
FILE_TASK_PATH_MARKER = ". Файл: "
FILE_WITHOUT_CAPTION_TASK_PREFIX = "Пользователь отправил файл без подписи."
PHOTO_WITHOUT_CAPTION_TASK_PREFIX = "Пользователь отправил фотографию без подписи."
FILE_WITHOUT_CAPTION_PREVIEW = "Файл без подписи"
PHOTO_WITHOUT_CAPTION_PREVIEW = "Фотография без подписи"
XML_TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


def _extract_user_caption_from_file_task(raw_text: str) -> str | None:
    """Return the user caption from the bot-composed file task text."""
    if not raw_text.startswith(FILE_CAPTION_TASK_PREFIX):
        return None

    task_without_prefix = raw_text.removeprefix(FILE_CAPTION_TASK_PREFIX)
    caption, separator, _tail = task_without_prefix.partition(FILE_TASK_PATH_MARKER)
    if not separator:
        return None

    stripped_caption = caption.strip()
    return stripped_caption or None


def _extract_original_request_text(raw_text: str) -> str:
    """Return user-authored request text without bot service boilerplate."""
    user_caption = _extract_user_caption_from_file_task(raw_text)
    if user_caption is not None:
        return user_caption
    if raw_text.startswith(FILE_WITHOUT_CAPTION_TASK_PREFIX):
        return FILE_WITHOUT_CAPTION_PREVIEW
    if raw_text.startswith(PHOTO_WITHOUT_CAPTION_TASK_PREFIX):
        return PHOTO_WITHOUT_CAPTION_PREVIEW
    return raw_text


def clean_session_request_preview(
    raw_text: str,
    max_length: int | None = DEFAULT_PREVIEW_MAX_LENGTH,
) -> str:
    """Return cleaned text for one session-list preview."""
    request_text = _extract_original_request_text(raw_text)
    text_without_xml_tags = XML_TAG_PATTERN.sub("", request_text)
    collapsed_text = WHITESPACE_PATTERN.sub(" ", text_without_xml_tags).strip()
    if max_length is not None and len(collapsed_text) > max_length:
        return collapsed_text[:max_length] + "..."
    return collapsed_text
