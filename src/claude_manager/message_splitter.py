"""Конвертация Markdown в HTML для Telegram и разбивка длинных сообщений.

Модуль без состояния — все функции чистые, без побочных эффектов.
Преобразует Markdown-ответы Claude в HTML-формат Telegram и разбивает
на части до 4096 символов с починкой HTML-тегов на границах.
"""

import html
import logging
import re

logger = logging.getLogger(__name__)

# Максимальная длина одного сообщения в Telegram (ограничение Telegram API)
TELEGRAM_MESSAGE_LIMIT = 4096

# Запас символов на закрывающие/открывающие теги при разбивке
# Реальные теги Telegram короткие (<b>, <pre>, <code>), 100 — с запасом
MAX_TAG_OVERHEAD_PER_SPLIT = 100

# Не искать точку разбивки раньше, чем на середине доступной длины
SPLIT_SEARCH_RATIO = 0.5

# Маркер начала/конца блока кода в Markdown (три обратных кавычки)
CODE_BLOCK_DELIMITER = "```"

# HTML-теги, которые поддерживает Telegram Bot API в режиме HTML
SUPPORTED_TAGS = ["b", "i", "code", "pre", "a"]


def markdown_to_html(markdown_text: str) -> str:
    """Конвертирует Markdown-текст в HTML-формат Telegram."""
    if not markdown_text:
        return ""

    blocks = _parse_markdown_blocks(markdown_text)
    html_parts = []

    for block in blocks:
        converted = _convert_block_to_html(block)
        html_parts.append(converted)

    return "\n\n".join(html_parts)


def split_html_message(html_text: str) -> list[str]:
    """Разбивает HTML-сообщение на части до 4096 символов с починкой тегов."""
    if len(html_text) <= TELEGRAM_MESSAGE_LIMIT:
        return [html_text]

    parts: list[str] = []
    remaining = html_text

    while len(remaining) > TELEGRAM_MESSAGE_LIMIT:
        part, remaining = _split_one_part(remaining)
        parts.append(part)

    if remaining:
        parts.append(remaining)

    return parts


def strip_html_tags(html_text: str) -> str:
    """Удаляет все HTML-теги и декодирует HTML-сущности."""
    # Убираем все HTML-теги
    text_without_tags = re.sub(r"<[^>]+>", "", html_text)
    # Декодируем HTML-сущности (&amp; -> &, &lt; -> <, и т.д.)
    return html.unescape(text_without_tags)


def prepare_message(markdown_text: str) -> list[str]:
    """Конвертирует Markdown в HTML и разбивает на части для Telegram."""
    html_result = markdown_to_html(markdown_text)
    return split_html_message(html_result)


# --- Внутренние функции ---


def _escape_html(text: str) -> str:
    """Экранирует спецсимволы HTML в обычном тексте."""
    return html.escape(text, quote=True)


def _convert_block_to_html(block: dict[str, str]) -> str:
    """Конвертирует один Markdown-блок в HTML."""
    block_type = block["type"]
    content = block["content"]

    if block_type == "code_block":
        return _convert_code_block(content, block.get("language", ""))
    if block_type == "heading":
        return f"<b>{_convert_inline_markdown(content)}</b>"
    if block_type == "list_item":
        return _convert_list_item(content)
    # paragraph — обычный текст
    return _convert_inline_markdown(content)


def _convert_code_block(content: str, language: str) -> str:
    """Оборачивает код в <pre><code> с экранированием содержимого."""
    escaped_content = _escape_html(content)
    if language:
        return (
            f'<pre><code class="language-{language}">'
            f"{escaped_content}"
            f"</code></pre>"
        )
    return f"<pre><code>{escaped_content}</code></pre>"


def _convert_list_item(content: str) -> str:
    """Конвертирует пункт списка в формат Telegram."""
    lines = content.split("\n")
    converted_lines = []

    for line in lines:
        converted_lines.append(_convert_single_list_line(line))

    return "\n".join(converted_lines)


def _convert_single_list_line(line: str) -> str:
    """Конвертирует одну строку списка."""
    # Маркированный список (- или *)
    unordered_match = re.match(r"^(\s*)[-*]\s+(.*)$", line)
    if unordered_match:
        indent = unordered_match.group(1)
        text = unordered_match.group(2)
        return f"{indent}  {_convert_inline_markdown(text)}"

    # Нумерованный список (1., 2., ...)
    ordered_match = re.match(r"^(\s*)(\d+\.\s+)(.*)$", line)
    if ordered_match:
        indent = ordered_match.group(1)
        number = ordered_match.group(2)
        text = ordered_match.group(3)
        return f"{indent}{number}{_convert_inline_markdown(text)}"

    return _convert_inline_markdown(line)


def _convert_inline_markdown(text: str) -> str:
    """Конвертирует inline-Markdown (жирный, курсив, код, ссылки) в HTML."""
    # Разбиваем текст на сегменты: inline-код обрабатывается отдельно,
    # чтобы не применять другие преобразования внутри кода
    segments = _split_by_inline_code(text)
    result_parts = []

    for segment_text, is_code in segments:
        if is_code:
            result_parts.append(
                f"<code>{_escape_html(segment_text)}</code>"
            )
        else:
            result_parts.append(_convert_non_code_inline(segment_text))

    return "".join(result_parts)


def _split_by_inline_code(text: str) -> list[tuple[str, bool]]:
    """Разбивает текст на сегменты: (содержимое, является_ли_кодом)."""
    segments: list[tuple[str, bool]] = []
    # Ищем `code` — inline-код в одинарных обратных кавычках
    pattern = re.compile(r"`([^`]+)`")
    last_end = 0

    for match in pattern.finditer(text):
        # Текст до кода
        if match.start() > last_end:
            segments.append((text[last_end:match.start()], False))
        # Сам код (без кавычек)
        segments.append((match.group(1), True))
        last_end = match.end()

    # Оставшийся текст после последнего кода
    if last_end < len(text):
        segments.append((text[last_end:], False))

    return segments


def _convert_non_code_inline(text: str) -> str:
    """Конвертирует жирный, курсив и ссылки (но не код) в HTML."""
    # Сначала экранируем спецсимволы HTML в исходном тексте,
    # чтобы < > & не путались с HTML-тегами, которые мы создадим дальше
    text = _escape_html(text)
    # Ссылки: [text](url) -> <a href="url">text</a>
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    # Жирный: **text** -> <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Курсив через *: *text* -> <i>text</i>
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    # Курсив через _: _text_ -> <i>text</i>
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    return text


def _detect_nested_code_blocks(
    text: str,
) -> list[tuple[int, int, bool]]:
    """Находит все блоки кода с учётом вложенности."""
    delimiter_len = len(CODE_BLOCK_DELIMITER)
    positions = _find_all_delimiter_positions(text, delimiter_len)

    if len(positions) < 2:
        return []

    return _build_code_block_ranges(positions, text, delimiter_len)


def _find_all_delimiter_positions(
    text: str, delimiter_len: int
) -> list[int]:
    """Находит позиции всех ``` в тексте."""
    positions: list[int] = []
    search_start = 0

    while True:
        found_pos = text.find(CODE_BLOCK_DELIMITER, search_start)
        if found_pos == -1:
            break
        positions.append(found_pos)
        search_start = found_pos + delimiter_len

    return positions


def _build_code_block_ranges(
    positions: list[int], text: str, delimiter_len: int
) -> list[tuple[int, int, bool]]:
    """Строит диапазоны блоков кода из позиций разделителей."""
    blocks: list[tuple[int, int, bool]] = []
    index = 0
    # Уровень вложенности: 0 — вне кода, 1 — внутри внешнего блока
    nesting_depth = 0

    while index < len(positions) - 1:
        if nesting_depth == 0:
            # Открываем внешний блок
            start = positions[index]
            # Ищем парную закрывающую кавычку
            close_index = _find_closing_delimiter(
                positions, index, text, delimiter_len
            )
            end = positions[close_index] + delimiter_len
            blocks.append((start, end, True))
            index = close_index + 1
        else:
            index += 1

    return blocks


def _find_closing_delimiter(
    positions: list[int],
    open_index: int,
    text: str,
    delimiter_len: int,
) -> int:
    """Находит индекс закрывающего ``` для открывающего."""
    # Закрывающий ``` — следующий, который стоит в начале строки
    # или после которого нет текста на той же строке (кроме пробелов)
    for candidate in range(open_index + 1, len(positions)):
        candidate_pos = positions[candidate]
        # Проверяем, что ``` стоит в начале строки
        # (перед ним либо начало текста, либо \n)
        is_line_start = (
            candidate_pos == 0
            or text[candidate_pos - 1] == "\n"
        )
        # Проверяем, что после ``` нет текста (кроме пробелов/переноса)
        after_pos = candidate_pos + delimiter_len
        after_text = text[after_pos:].split("\n", maxsplit=1)[0]
        is_closing = is_line_start and after_text.strip() == ""

        if is_closing:
            return candidate

    # Если парная не найдена — берём последнюю позицию
    return len(positions) - 1


def _parse_markdown_blocks(
    markdown_text: str,
) -> list[dict[str, str]]:
    """Разбирает Markdown-текст на структурные блоки."""
    code_blocks = _detect_nested_code_blocks(markdown_text)
    return _extract_blocks_from_text(markdown_text, code_blocks)


def _extract_blocks_from_text(
    text: str,
    code_blocks: list[tuple[int, int, bool]],
) -> list[dict[str, str]]:
    """Извлекает блоки из текста, учитывая позиции блоков кода."""
    blocks: list[dict[str, str]] = []
    current_pos = 0

    for start, end, is_outer in code_blocks:
        if not is_outer:
            continue

        # Текст до блока кода
        if current_pos < start:
            text_before = text[current_pos:start].strip()
            if text_before:
                blocks.extend(_parse_text_blocks(text_before))

        # Сам блок кода
        code_block = _parse_code_block_content(text[start:end])
        blocks.append(code_block)
        current_pos = end

    # Текст после последнего блока кода
    if current_pos < len(text):
        remaining = text[current_pos:].strip()
        if remaining:
            blocks.extend(_parse_text_blocks(remaining))

    return blocks


def _parse_code_block_content(raw_block: str) -> dict[str, str]:
    """Парсит содержимое блока кода, извлекая язык и код."""
    # Убираем открывающие и закрывающие ```
    delimiter_len = len(CODE_BLOCK_DELIMITER)
    inner = raw_block[delimiter_len:]

    # Убираем закрывающий ```
    closing_pos = inner.rfind(CODE_BLOCK_DELIMITER)
    if closing_pos != -1:
        inner = inner[:closing_pos]

    # Извлекаем язык из первой строки
    first_newline = inner.find("\n")
    if first_newline == -1:
        return {"type": "code_block", "content": inner, "language": ""}

    language = inner[:first_newline].strip()
    content = inner[first_newline + 1:]

    return {"type": "code_block", "content": content, "language": language}


def _parse_text_blocks(text: str) -> list[dict[str, str]]:
    """Разбивает обычный текст на параграфы, заголовки и списки."""
    # Разбиваем по двойному переносу строки (границы параграфов)
    paragraphs = re.split(r"\n\n+", text)
    blocks: list[dict[str, str]] = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        block = _classify_paragraph(paragraph)
        blocks.append(block)

    return blocks


def _classify_paragraph(paragraph: str) -> dict[str, str]:
    """Определяет тип параграфа: заголовок, список или обычный текст."""
    # Заголовок (# ... или ## ... или ### ...)
    heading_match = re.match(r"^#{1,6}\s+(.+)$", paragraph)
    if heading_match:
        return {"type": "heading", "content": heading_match.group(1)}

    # Список (строки начинаются с - , * , или 1. )
    if _is_list_block(paragraph):
        return {"type": "list_item", "content": paragraph}

    return {"type": "paragraph", "content": paragraph}


def _is_list_block(paragraph: str) -> bool:
    """Проверяет, является ли параграф списком."""
    first_line = paragraph.split("\n")[0].strip()
    is_unordered = bool(re.match(r"^[-*]\s+", first_line))
    is_ordered = bool(re.match(r"^\d+\.\s+", first_line))
    return is_unordered or is_ordered


def _split_one_part(html_text: str) -> tuple[str, str]:
    """Отрезает одну часть от HTML-текста с починкой тегов."""
    available_length = TELEGRAM_MESSAGE_LIMIT - MAX_TAG_OVERHEAD_PER_SPLIT
    split_point = _find_split_point(html_text, available_length)

    first_part = html_text[:split_point]
    remaining = html_text[split_point:]

    # Определяем незакрытые теги в первой части
    open_tags = _collect_open_tags(first_part)

    # Закрываем теги в конце первой части
    first_part += _close_tags(open_tags)
    # Открываем теги в начале оставшейся части
    remaining = _reopen_tags(open_tags) + remaining

    return first_part, remaining


def _find_split_point(html_text: str, max_length: int) -> int:
    """Находит оптимальную точку разбивки в HTML-тексте."""
    if len(html_text) <= max_length:
        return len(html_text)

    # Область поиска: от середины до max_length
    search_start = int(max_length * SPLIT_SEARCH_RATIO)
    search_area = html_text[search_start:max_length]

    # Ищем точку разбивки по приоритету (от лучшей к худшей)
    split_offset = _find_best_split_in_area(search_area)

    if split_offset is not None:
        candidate = search_start + split_offset
        if not _is_inside_html_tag(html_text, candidate):
            return candidate

    # Крайний случай — разрезаем на max_length
    return max_length


def _find_best_split_in_area(search_area: str) -> int | None:
    """Ищет лучшую точку разбивки в области поиска."""
    # Приоритеты точек разбивки
    split_markers = ["</pre>\n", "\n\n", "\n", " "]

    for marker in split_markers:
        last_pos = search_area.rfind(marker)
        if last_pos != -1:
            # Разбиваем после маркера
            return last_pos + len(marker)

    return None


def _is_inside_html_tag(text: str, position: int) -> bool:
    """Проверяет, попадает ли позиция внутрь HTML-тега."""
    # Ищем ближайший < перед позицией
    last_open = text.rfind("<", 0, position)
    if last_open == -1:
        return False

    # Если после < нет закрывающего > до позиции — мы внутри тега
    last_close = text.rfind(">", last_open, position)
    return last_close == -1


def _collect_open_tags(
    html_text: str,
) -> list[tuple[str, str]]:
    """Определяет незакрытые HTML-теги в конце фрагмента."""
    tag_pattern = re.compile(r"<(/?)(\w+)([^>]*)>")
    # Стек кортежей (имя_тега, полный_открывающий_тег)
    tag_stack: list[tuple[str, str]] = []

    for match in tag_pattern.finditer(html_text):
        is_closing = match.group(1) == "/"
        tag_name = match.group(2)
        attributes = match.group(3)

        if tag_name not in SUPPORTED_TAGS:
            continue

        # Пропускаем самозакрывающиеся теги (<br/>, и т.д.)
        if attributes.rstrip().endswith("/"):
            continue

        if is_closing:
            _remove_last_matching_tag(tag_stack, tag_name)
        else:
            full_tag = match.group(0)
            tag_stack.append((tag_name, full_tag))

    return tag_stack


def _remove_last_matching_tag(
    tag_stack: list[tuple[str, str]], tag_name: str
) -> None:
    """Убирает последний тег с заданным именем из стека."""
    for index in range(len(tag_stack) - 1, -1, -1):
        if tag_stack[index][0] == tag_name:
            tag_stack.pop(index)
            return


def _close_tags(open_tags: list[tuple[str, str]]) -> str:
    """Формирует закрывающие теги в обратном порядке."""
    closing_parts = [
        f"</{tag_name}>" for tag_name, _ in reversed(open_tags)
    ]
    return "".join(closing_parts)


def _reopen_tags(open_tags: list[tuple[str, str]]) -> str:
    """Формирует открывающие теги в прямом порядке (с атрибутами)."""
    return "".join(full_tag for _, full_tag in open_tags)
