"""Юнит-тесты для модуля message_splitter."""

import pytest

from claude_manager.message_splitter import (
    TELEGRAM_MESSAGE_LIMIT,
    MAX_TAG_OVERHEAD_PER_SPLIT,
    _collect_open_tags,
    _close_tags,
    _convert_inline_markdown,
    _detect_nested_code_blocks,
    _find_split_point,
    _is_inside_html_tag,
    _reopen_tags,
    _split_by_inline_code,
    markdown_to_html,
    prepare_message,
    split_html_message,
    strip_html_tags,
)


# --- Тесты конвертации Markdown в HTML ---


class TestMarkdownToHtml:
    """Тесты конвертации Markdown-разметки в HTML для Telegram."""

    def test_markdown_bold_to_html(self):
        """Конвертация жирного текста."""
        result = markdown_to_html("**важное сообщение**")
        assert result == "<b>важное сообщение</b>"

    def test_markdown_italic_to_html(self):
        """Конвертация курсива через звёздочку."""
        result = markdown_to_html("*курсивный текст*")
        assert result == "<i>курсивный текст</i>"

    def test_markdown_inline_code_to_html(self):
        """Конвертация inline-кода."""
        result = markdown_to_html("`main.py`")
        assert result == "<code>main.py</code>"

    def test_markdown_code_block_to_html(self):
        """Конвертация блока кода с языком."""
        input_text = "```python\nprint('hello')\n```"
        result = markdown_to_html(input_text)
        assert result == (
            '<pre><code class="language-python">'
            "print(&#x27;hello&#x27;)\n"
            "</code></pre>"
        )

    def test_markdown_code_block_without_language(self):
        """Блок кода без указания языка."""
        input_text = "```\nsome code\n```"
        result = markdown_to_html(input_text)
        assert result == "<pre><code>some code\n</code></pre>"

    def test_markdown_link_to_html(self):
        """Конвертация ссылки."""
        result = markdown_to_html("[документация](https://example.com)")
        assert result == '<a href="https://example.com">документация</a>'

    def test_markdown_heading_to_html(self):
        """Конвертация заголовка."""
        result = markdown_to_html("## Подзаголовок")
        assert result == "<b>Подзаголовок</b>"

    def test_markdown_underscore_italic_to_html(self):
        """Конвертация курсива через подчёркивания."""
        result = markdown_to_html("_курсивный текст_")
        assert result == "<i>курсивный текст</i>"

    def test_markdown_unordered_list_to_html(self):
        """Конвертация маркированного списка."""
        input_text = "- первый пункт\n- второй пункт"
        result = markdown_to_html(input_text)
        assert result == "  первый пункт\n  второй пункт"

    def test_markdown_numbered_list_to_html(self):
        """Конвертация нумерованного списка."""
        input_text = "1. первый пункт\n2. второй пункт"
        result = markdown_to_html(input_text)
        assert result == "1. первый пункт\n2. второй пункт"

    def test_markdown_mixed_formatting(self):
        """Комбинация жирного, курсива и кода."""
        input_text = "**Файл** `main.py` содержит *основную* логику"
        result = markdown_to_html(input_text)
        assert result == (
            "<b>Файл</b> <code>main.py</code>"
            " содержит <i>основную</i> логику"
        )

    def test_html_escape_in_text(self):
        """Экранирование спецсимволов в обычном тексте."""
        result = markdown_to_html("if a < b && c > d")
        assert result == "if a &lt; b &amp;&amp; c &gt; d"

    def test_html_escape_inside_code_block(self):
        """Экранирование спецсимволов внутри блока кода."""
        input_text = "```\na < b && c > d\n```"
        result = markdown_to_html(input_text)
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result


# --- Тесты разбивки HTML-сообщений ---


class TestSplitHtmlMessage:
    """Тесты разбивки длинных HTML-сообщений на части."""

    def test_split_short_message_no_split(self):
        """Короткое сообщение не разбивается."""
        result = split_html_message("Привет, мир!")
        assert result == ["Привет, мир!"]

    def test_split_at_paragraph_boundary(self):
        """Разбивка по границе параграфа."""
        # Создаём текст с границей параграфа примерно на 3500
        first_part = "a" * 3500
        second_part = "b" * 1500
        html_text = first_part + "\n\n" + second_part
        result = split_html_message(html_text)
        assert len(result) == 2
        assert len(result[0]) <= TELEGRAM_MESSAGE_LIMIT
        assert len(result[1]) <= TELEGRAM_MESSAGE_LIMIT

    def test_split_preserves_html_tags(self):
        """Теги закрываются и открываются при разбивке."""
        html_text = "<b>" + "a" * 5000 + "</b>"
        result = split_html_message(html_text)
        assert len(result) == 2
        # Первая часть должна иметь закрывающий </b>
        assert result[0].endswith("</b>")
        # Вторая часть должна начинаться с <b>
        assert result[1].startswith("<b>")

    def test_split_nested_tags(self):
        """Починка вложенных тегов при разбивке."""
        html_text = "<b><i>" + "слово " * 1000 + "</i></b>"
        result = split_html_message(html_text)
        assert len(result) >= 2
        # Первая часть закрывается в обратном порядке
        assert result[0].endswith("</i></b>")
        # Вторая часть открывается в прямом порядке
        assert result[1].startswith("<b><i>")


# --- Тесты удаления HTML-тегов ---


class TestStripHtmlTags:
    """Тесты удаления HTML-тегов и декодирования сущностей."""

    def test_strip_html_tags_basic(self):
        """Удаление тегов."""
        result = strip_html_tags("<b>жирный</b> и <i>курсив</i>")
        assert result == "жирный и курсив"

    def test_strip_html_tags_with_entities(self):
        """Декодирование HTML-сущностей."""
        result = strip_html_tags("a &amp; b &lt; c &gt; d")
        assert result == "a & b < c > d"


# --- Тесты prepare_message ---


class TestPrepareMessage:
    """Тесты полного конвейера: Markdown -> HTML -> разбивка."""

    def test_prepare_message_full_pipeline(self):
        """Полный конвейер для короткого сообщения."""
        result = prepare_message("**Заголовок**\n\nТекст параграфа.")
        assert result == ["<b>Заголовок</b>\n\nТекст параграфа."]


# --- Тесты _collect_open_tags ---


class TestCollectOpenTags:
    """Тесты определения незакрытых HTML-тегов."""

    def test_collect_open_tags_simple(self):
        """Определение одного незакрытого тега."""
        result = _collect_open_tags("<b>жирный текст без закрытия")
        assert result == [("b", "<b>")]

    def test_collect_open_tags_nested(self):
        """Определение нескольких незакрытых тегов."""
        result = _collect_open_tags("<b><i>текст")
        assert result == [("b", "<b>"), ("i", "<i>")]

    def test_collect_open_tags_partially_closed(self):
        """Часть тегов закрыта, часть нет."""
        result = _collect_open_tags("<b><i>курсив</i> ещё жирный")
        assert result == [("b", "<b>")]

    def test_collect_open_tags_with_attributes(self):
        """Тег с атрибутами сохраняет полную строку."""
        result = _collect_open_tags(
            '<pre><code class="language-python">print(1)'
        )
        assert result == [
            ("pre", "<pre>"),
            ("code", '<code class="language-python">'),
        ]


# --- Тесты внутренних функций тегов ---


class TestTagHelpers:
    """Тесты вспомогательных функций для работы с тегами."""

    def test_close_tags_order(self):
        """Закрывающие теги идут в обратном порядке."""
        tags = [("b", "<b>"), ("i", "<i>")]
        assert _close_tags(tags) == "</i></b>"

    def test_reopen_tags_order(self):
        """Открывающие теги идут в прямом порядке с атрибутами."""
        tags = [
            ("pre", "<pre>"),
            ("code", '<code class="language-python">'),
        ]
        result = _reopen_tags(tags)
        assert result == '<pre><code class="language-python">'


# --- Граничные случаи ---


class TestEdgeCases:
    """Граничные случаи."""

    def test_empty_string(self):
        """Пустая строка на входе."""
        assert markdown_to_html("") == ""
        assert split_html_message("") == [""]
        assert prepare_message("") == [""]

    def test_exactly_4096_chars(self):
        """Текст ровно 4096 символов — не разбивается."""
        text = "a" * TELEGRAM_MESSAGE_LIMIT
        result = split_html_message(text)
        assert result == [text]

    def test_4097_chars(self):
        """Текст на 1 символ длиннее лимита — разбивается на 2 части."""
        text = "a" * (TELEGRAM_MESSAGE_LIMIT + 1)
        result = split_html_message(text)
        assert len(result) == 2

    def test_html_escape_increases_length(self):
        """Экранирование делает текст длиннее лимита."""
        # 2000 символов & превращаются в &amp; (10000 символов)
        input_text = "&" * 2000
        result = prepare_message(input_text)
        # Должно разбиться на несколько частей
        assert len(result) >= 2
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT

    def test_nested_code_block_triple_backticks(self):
        """Тройные кавычки внутри блока кода."""
        input_text = (
            "```markdown\n"
            "Вот пример:\n"
            "```python\n"
            "print('hello')\n"
            "```\n"
            "Конец примера\n"
            "```"
        )
        result = markdown_to_html(input_text)
        # Весь текст должен быть внутри <pre><code>
        assert "<pre>" in result

    def test_no_space_or_newline_for_split(self):
        """Длинная строка без пробелов и переносов."""
        text = "a" * 5000
        result = split_html_message(text)
        assert len(result) == 2
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT

    def test_split_does_not_break_inside_html_tag(self):
        """Разбивка не попадает внутрь HTML-тега."""
        # Создаём текст, где на позиции ~4090 есть тег
        prefix = "x" * 4085
        html_text = prefix + "<code>some code</code>" + "y" * 100
        result = split_html_message(html_text)
        # Ни одна часть не должна содержать обрезанный тег
        for part in result:
            # Проверяем, что нет незакрытого < без >
            open_brackets = part.count("<")
            close_brackets = part.count(">")
            assert open_brackets == close_brackets

    def test_only_code_block_message(self):
        """Сообщение целиком из блока кода."""
        code_lines = "x = 1\n" * 1000
        input_text = f"```python\n{code_lines}```"
        result = prepare_message(input_text)
        # Каждая часть должна быть обёрнута в <pre><code>
        for part in result:
            assert "<pre>" in part or len(part) < 20

    def test_multiple_code_blocks(self):
        """Несколько блоков кода подряд."""
        input_text = "```python\ncode1\n```\n\n```javascript\ncode2\n```"
        result = markdown_to_html(input_text)
        assert 'language-python' in result
        assert 'language-javascript' in result

    def test_three_part_split(self):
        """Очень длинное сообщение разбивается на 3+ частей."""
        text = "слово " * 3000  # ~18000 символов
        result = split_html_message(text)
        assert len(result) >= 3
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT

    def test_cyrillic_text_length(self):
        """Корректный подсчёт длины кириллического текста."""
        text = "а" * TELEGRAM_MESSAGE_LIMIT
        result = split_html_message(text)
        assert len(result) == 1

    def test_markdown_with_no_formatting(self):
        """Обычный текст без Markdown-разметки."""
        input_text = "Простой текст без форматирования, просто предложение."
        result = markdown_to_html(input_text)
        # Текст должен быть экранирован, но без HTML-тегов
        assert "<b>" not in result
        assert "<i>" not in result

    def test_unclosed_markdown_bold(self):
        """Незакрытый жирный в Markdown — не ломает конвертацию."""
        result = markdown_to_html("**начало жирного без закрытия")
        # Не должно выбросить исключение; текст остаётся с **
        assert result is not None

    def test_pre_tag_split_reopens_correctly(self):
        """Починка <pre> тега при разбивке."""
        long_code = "x = 1\n" * 1000
        html_text = (
            '<pre><code class="language-python">'
            f"{long_code}"
            "</code></pre>"
        )
        result = split_html_message(html_text)
        assert len(result) >= 2
        # Первая часть закрывается </code></pre>
        assert "</code></pre>" in result[0]
        # Вторая часть открывается <pre><code ...>
        assert '<pre><code class="language-python">' in result[1]


# --- Тесты ошибок ---


class TestErrorHandling:
    """Тесты обработки ошибок и некорректного ввода."""

    def test_malformed_html_does_not_crash(self):
        """Некорректный HTML не вызывает ошибку."""
        html_text = "<b>незакрытый <i>тоже незакрытый текст"
        # Не должно выбросить исключение
        result = split_html_message(html_text)
        assert isinstance(result, list)
        assert len(result) >= 1


# --- Тесты вложенных блоков кода ---


class TestNestedCodeBlocks:
    """Тесты парсинга блоков кода: вложенные ```, незакрытые, пустые."""

    def test_single_backtick_triplet_no_block(self):
        """Одиночный ``` без пары — нет блока кода."""
        blocks = _detect_nested_code_blocks("только ``` и ничего больше")
        assert blocks == []

    def test_code_block_with_inner_triple_backticks_on_same_line(self):
        """``` внутри строки кода (не в начале строки) — не закрывает блок."""
        text = "```python\nprint('```')\n```"
        result = markdown_to_html(text)
        assert "<pre>" in result
        assert "print" in result

    def test_empty_code_block(self):
        """Блок кода без содержимого — пустой <pre><code>."""
        text = "```\n```"
        result = markdown_to_html(text)
        assert "<pre><code>" in result
        assert "</code></pre>" in result

    def test_code_block_single_line_no_newline(self):
        """Блок ``` без переноса строки внутри."""
        text = "```x```"
        result = markdown_to_html(text)
        assert "<pre>" in result

    def test_unclosed_code_block_uses_last_delimiter(self):
        """Незакрытый блок кода — fallback на последний ```."""
        text = "```python\ncode here\nmore code"
        blocks = _detect_nested_code_blocks(text)
        assert blocks == []

    def test_two_separate_code_blocks(self):
        """Два отдельных блока кода парсятся независимо."""
        text = "```\nblock1\n```\n\nтекст между\n\n```\nblock2\n```"
        result = markdown_to_html(text)
        assert result.count("<pre>") == 2
        assert result.count("</pre>") == 2
        assert "block1" in result
        assert "block2" in result

    def test_code_block_with_html_entities(self):
        """HTML-сущности внутри блока кода экранируются."""
        text = '```\n<div class="test">&amp;</div>\n```'
        result = markdown_to_html(text)
        assert "&lt;div" in result
        assert "&amp;amp;" in result

    def test_code_block_preserves_indentation(self):
        """Отступы внутри блока кода сохраняются."""
        text = "```python\ndef foo():\n    return 42\n```"
        result = markdown_to_html(text)
        assert "    return 42" in result

    def test_code_block_with_blank_lines(self):
        """Пустые строки внутри блока кода сохраняются."""
        text = "```\nline1\n\n\nline4\n```"
        result = markdown_to_html(text)
        assert "line1\n\n\nline4" in result


# --- Тесты починки тегов при разрезе ---


class TestTagRepairOnSplit:
    """Тесты: теги корректно закрываются и переоткрываются при разрезе."""

    def test_link_tag_reopens_with_href(self):
        """<a href="..."> переоткрывается с полным атрибутом."""
        long_text = "слово " * 800
        html_text = f'<a href="https://example.com">{long_text}</a>'
        result = split_html_message(html_text)
        assert len(result) >= 2
        assert result[0].endswith("</a>")
        assert result[1].startswith('<a href="https://example.com">')

    def test_triple_nesting_repair(self):
        """Тройная вложенность: <pre><code><b> — все три чинятся."""
        inner = "x" * 5000
        html_text = f"<pre><code><b>{inner}</b></code></pre>"
        result = split_html_message(html_text)
        assert len(result) >= 2
        assert "</b></code></pre>" in result[0]
        assert result[1].startswith("<pre><code><b>")

    def test_cascading_splits_all_within_limit(self):
        """Каскадные разрезы: переоткрытые теги увеличивают длину, все части ≤4096."""
        long_content = "a" * 12000
        html_text = (
            '<pre><code class="language-python">'
            f"{long_content}"
            "</code></pre>"
        )
        result = split_html_message(html_text)
        assert len(result) >= 3
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT

    def test_split_reopens_only_supported_tags(self):
        """Неподдерживаемые Telegram-теги не попадают в стек починки."""
        tags = _collect_open_tags("<div><b>текст")
        tag_names = [name for name, _ in tags]
        assert "div" not in tag_names
        assert "b" in tag_names

    def test_self_closing_tag_ignored(self):
        """Самозакрывающийся тег (<br/>) не попадает в стек."""
        tags = _collect_open_tags("<b>текст<br/> ещё текст")
        assert len(tags) == 1
        assert tags[0][0] == "b"

    def test_close_tags_empty_stack(self):
        """Пустой стек тегов — пустая строка закрытия."""
        assert _close_tags([]) == ""

    def test_reopen_tags_empty_stack(self):
        """Пустой стек тегов — пустая строка открытия."""
        assert _reopen_tags([]) == ""

    def test_interleaved_tags_repair(self):
        """Чередующиеся теги: <b><i></b></i> — стек корректен."""
        tags = _collect_open_tags("<b><i>текст</b>")
        tag_names = [name for name, _ in tags]
        assert tag_names == ["i"]

    def test_tag_repair_does_not_double_close(self):
        """Уже закрытые теги не закрываются повторно."""
        html_text = "<b>текст</b>" + "a" * 5000
        result = split_html_message(html_text)
        first_part = result[0]
        assert first_part.count("</b>") == 1


# --- Тесты точки разрезки ---


class TestSplitPointSelection:
    """Тесты выбора оптимальной точки разреза."""

    def test_prefers_pre_close_over_double_newline(self):
        """</pre>\\n имеет приоритет над \\n\\n."""
        padding = "a" * 2000
        html_text = f"{padding}</pre>\n{padding}\n\n{'b' * 2000}"
        split_point = _find_split_point(
            html_text,
            TELEGRAM_MESSAGE_LIMIT - MAX_TAG_OVERHEAD_PER_SPLIT,
        )
        cut = html_text[:split_point]
        assert cut.endswith("</pre>\n")

    def test_prefers_double_newline_over_single(self):
        """\\n\\n приоритетнее одиночного \\n."""
        padding = "a" * 3000
        html_text = f"{padding}\n\n{'b' * 500}\n{'c' * 2000}"
        split_point = _find_split_point(
            html_text,
            TELEGRAM_MESSAGE_LIMIT - MAX_TAG_OVERHEAD_PER_SPLIT,
        )
        cut = html_text[:split_point]
        assert cut.endswith("\n\n")

    def test_falls_back_to_space_when_no_newlines(self):
        """Если нет переносов строк — разрезает по пробелу."""
        words = "слово " * 700
        split_point = _find_split_point(
            words,
            TELEGRAM_MESSAGE_LIMIT - MAX_TAG_OVERHEAD_PER_SPLIT,
        )
        cut = words[:split_point]
        assert cut.endswith(" ")

    def test_avoids_splitting_inside_html_tag(self):
        """_is_inside_html_tag: позиция внутри <...> обнаруживается."""
        text = 'текст <a href="long_url"> ещё'
        pos_inside = text.index("href")
        assert _is_inside_html_tag(text, pos_inside) is True

    def test_position_after_tag_is_outside(self):
        """Позиция сразу после > — вне тега."""
        text = "<b>текст</b> вне"
        pos_after = text.index(">") + 1
        assert _is_inside_html_tag(text, pos_after) is False

    def test_position_with_no_tags_is_outside(self):
        """Текст без тегов — любая позиция вне тега."""
        assert _is_inside_html_tag("просто текст", 5) is False

    def test_message_at_limit_minus_overhead_no_split(self):
        """Текст ровно на лимите — не разбивается."""
        text = "x" * TELEGRAM_MESSAGE_LIMIT
        result = split_html_message(text)
        assert len(result) == 1


# --- Тесты inline-markdown конвертации ---


class TestInlineMarkdownEdgeCases:
    """Граничные случаи конвертации inline-разметки."""

    def test_bold_inside_inline_code_not_converted(self):
        """**жирный** внутри `inline code` — не конвертируется."""
        result = markdown_to_html("`**не жирный**`")
        assert "<b>" not in result
        assert "<code>" in result
        assert "**не жирный**" in strip_html_tags(result)

    def test_multiple_inline_codes_in_one_line(self):
        """Несколько `code` в одной строке."""
        result = markdown_to_html("Запусти `cmd1` и потом `cmd2`")
        assert result.count("<code>") == 2
        assert result.count("</code>") == 2

    def test_adjacent_inline_codes(self):
        """Два `code` без пробела между ними."""
        result = markdown_to_html("`first``second`")
        assert "<code>first</code>" in result
        assert "<code>second</code>" in result

    def test_bold_and_italic_combined(self):
        """Жирный внутри курсива и наоборот."""
        result = markdown_to_html("**жирный и *курсив внутри* тут**")
        assert "<b>" in result
        assert "<i>" in result

    def test_underscore_italic_not_triggered_inside_word(self):
        """Подчёркивания внутри слова (snake_case) — не курсив."""
        result = markdown_to_html("переменная my_var_name тут")
        assert "<i>" not in result
        assert "my_var_name" in result

    def test_heading_with_inline_formatting(self):
        """Заголовок с жирным и кодом внутри."""
        result = markdown_to_html("## Метод `run()` — **основной**")
        assert "<b>" in result
        assert "<code>run()</code>" in result

    def test_link_with_special_chars_in_url(self):
        """Ссылка с & и ? в URL."""
        result = markdown_to_html("[поиск](https://x.com?q=a&b=c)")
        assert 'href="https://x.com?q=a&amp;b=c"' in result
        assert ">поиск</a>" in result

    def test_split_by_inline_code_no_code(self):
        """Текст без inline-кода — один сегмент, is_code=False."""
        segments = _split_by_inline_code("просто текст")
        assert segments == [("просто текст", False)]

    def test_split_by_inline_code_only_code(self):
        """Весь текст — inline-код."""
        segments = _split_by_inline_code("`весь код`")
        assert segments == [("весь код", True)]

    def test_convert_inline_markdown_empty_string(self):
        """Пустая строка — пустой результат."""
        assert _convert_inline_markdown("") == ""


# --- Тесты для списков ---


class TestListConversion:
    """Тесты конвертации списков с inline-разметкой."""

    def test_list_with_bold_items(self):
        """Элементы списка с жирным текстом."""
        text = "- **первый** пункт\n- **второй** пункт"
        result = markdown_to_html(text)
        assert "<b>первый</b>" in result
        assert "<b>второй</b>" in result

    def test_list_with_inline_code(self):
        """Элементы списка с inline-кодом."""
        text = "- Запусти `pip install`\n- Потом `pytest`"
        result = markdown_to_html(text)
        assert "<code>pip install</code>" in result
        assert "<code>pytest</code>" in result

    def test_nested_list_indentation(self):
        """Вложенный список с отступами."""
        text = "- верхний\n  - вложенный"
        result = markdown_to_html(text)
        assert "верхний" in result
        assert "вложенный" in result

    def test_mixed_ordered_and_unordered(self):
        """Нумерованный и маркированный списки в разных параграфах."""
        text = "1. первый\n2. второй\n\n- маркер\n- ещё маркер"
        result = markdown_to_html(text)
        assert "1. первый" in result
        assert "маркер" in result

    def test_list_item_with_asterisk_marker(self):
        """Список с маркером * вместо -."""
        text = "* пункт один\n* пункт два"
        result = markdown_to_html(text)
        assert "пункт один" in result
        assert "пункт два" in result


# --- Тесты strip_html_tags расширенные ---


class TestStripHtmlTagsExtended:
    """Расширенные тесты удаления HTML-тегов."""

    def test_strip_nested_tags(self):
        """Вложенные теги полностью удаляются."""
        result = strip_html_tags("<b><i>вложенный</i></b>")
        assert result == "вложенный"

    def test_strip_tag_with_attributes(self):
        """Теги с атрибутами удаляются целиком."""
        result = strip_html_tags(
            '<a href="https://example.com">ссылка</a>'
        )
        assert result == "ссылка"

    def test_strip_pre_code_block(self):
        """<pre><code> удаляется, содержимое остаётся."""
        result = strip_html_tags(
            '<pre><code class="language-python">x = 1</code></pre>'
        )
        assert result == "x = 1"

    def test_strip_empty_string(self):
        """Пустая строка — пустой результат."""
        assert strip_html_tags("") == ""

    def test_strip_only_entities_no_tags(self):
        """Только HTML-сущности, без тегов."""
        result = strip_html_tags("5 &gt; 3 &amp;&amp; 2 &lt; 4")
        assert result == "5 > 3 && 2 < 4"

    def test_strip_preserves_newlines(self):
        """Переносы строк сохраняются после удаления тегов."""
        result = strip_html_tags("<b>строка1</b>\n<i>строка2</i>")
        assert result == "строка1\nстрока2"


# --- Тесты prepare_message расширенные ---


class TestPrepareMessageExtended:
    """Расширенные тесты полного конвейера Markdown → HTML → split."""

    def test_long_markdown_with_code_blocks_splits_correctly(self):
        """Длинный Markdown с блоками кода — разбивка с починкой."""
        code = "line = True\n" * 500
        text = f"# Заголовок\n\n```python\n{code}```\n\nИтог."
        result = prepare_message(text)
        assert len(result) >= 2
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT

    def test_prepare_message_preserves_formatting_after_split(self):
        """После разбивки каждая часть содержит валидный HTML."""
        long_text = "**жирный** " * 600
        result = prepare_message(long_text)
        for part in result:
            open_count = part.count("<b>")
            close_count = part.count("</b>")
            assert open_count == close_count

    def test_prepare_message_single_word(self):
        """Одно слово — одна часть без тегов."""
        result = prepare_message("привет")
        assert result == ["привет"]

    def test_prepare_message_only_whitespace(self):
        """Только пробелы — пустой результат."""
        result = prepare_message("   \n\n   ")
        assert result == [""]

    def test_prepare_message_heading_then_paragraph(self):
        """Заголовок + параграф разделяются двойным переносом."""
        result = prepare_message("# Заголовок\n\nТекст.")
        assert result == ["<b>Заголовок</b>\n\nТекст."]

    def test_prepare_message_code_with_html_special_chars(self):
        """Спецсимволы в коде экранируются, не ломают HTML."""
        text = '```\nif (a < b && c > d) { return "ok"; }\n```'
        result = prepare_message(text)
        combined = "".join(result)
        assert "&lt;" in combined
        assert "&gt;" in combined
        assert "&amp;" in combined


# --- Тесты разрезки на границе лимита ---


class TestBoundaryLimitSplits:
    """Тесты поведения на точных границах 4096 символов."""

    def test_text_at_4096_with_tag_still_fits(self):
        """Текст + теги ровно 4096 — не разбивается."""
        tag_len = len("<b></b>")
        content_len = TELEGRAM_MESSAGE_LIMIT - tag_len
        html_text = f"<b>{'a' * content_len}</b>"
        assert len(html_text) == TELEGRAM_MESSAGE_LIMIT
        result = split_html_message(html_text)
        assert len(result) == 1

    def test_text_at_4096_tags_push_over(self):
        """Текст влезает, но теги добавляют 1 символ сверх лимита."""
        tag_len = len("<b></b>")
        content_len = TELEGRAM_MESSAGE_LIMIT - tag_len + 1
        html_text = f"<b>{'a' * content_len}</b>"
        assert len(html_text) == TELEGRAM_MESSAGE_LIMIT + 1
        result = split_html_message(html_text)
        assert len(result) == 2

    def test_exactly_double_limit(self):
        """Текст ровно 2 * 4096 — разбивается на 2 части."""
        text = "a " * (TELEGRAM_MESSAGE_LIMIT)
        result = split_html_message(text)
        assert len(result) >= 2
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT

    def test_split_with_long_reopened_tags_stays_within_limit(self):
        """Переоткрытые длинные атрибуты не выводят часть за лимит."""
        long_url = "https://example.com/" + "x" * 50
        inner = "слово " * 700
        html_text = f'<a href="{long_url}">{inner}</a>'
        result = split_html_message(html_text)
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT


# --- Тесты _detect_nested_code_blocks ---


class TestDetectNestedCodeBlocks:
    """Тесты парсера вложенных блоков кода."""

    def test_no_delimiters_returns_empty(self):
        """Текст без ``` — пустой список."""
        assert _detect_nested_code_blocks("просто текст") == []

    def test_one_delimiter_returns_empty(self):
        """Один ``` без пары — пустой список."""
        assert _detect_nested_code_blocks("до ``` после") == []

    def test_basic_code_block_detection(self):
        """Простой блок — одна пара [start, end, True]."""
        text = "```\ncode\n```"
        blocks = _detect_nested_code_blocks(text)
        assert len(blocks) == 1
        start, end, is_outer = blocks[0]
        assert is_outer is True
        assert text[start:end] == text

    def test_inner_delimiter_not_at_line_start_ignored(self):
        """``` внутри строки (не в начале) — не считается закрывающим."""
        text = "```\nprint('```')\n```"
        blocks = _detect_nested_code_blocks(text)
        assert len(blocks) == 1

    def test_multiple_blocks_detected_separately(self):
        """Два блока — два элемента в списке."""
        text = "```\na\n```\nтекст\n```\nb\n```"
        blocks = _detect_nested_code_blocks(text)
        assert len(blocks) == 2


# --- Тесты _is_inside_html_tag ---


class TestIsInsideHtmlTag:
    """Тесты определения, попадает ли позиция внутрь HTML-тега."""

    def test_inside_opening_tag(self):
        """Позиция между < и >."""
        assert _is_inside_html_tag("<b>", 1) is True

    def test_outside_after_close(self):
        """Позиция после >."""
        assert _is_inside_html_tag("<b>text", 3) is False

    def test_inside_tag_with_attributes(self):
        """Позиция внутри тега с атрибутами."""
        text = '<a href="url">'
        assert _is_inside_html_tag(text, 5) is True

    def test_between_two_tags(self):
        """Позиция между двумя тегами — вне тега."""
        text = "<b></b> text <i></i>"
        pos = text.index("text")
        assert _is_inside_html_tag(text, pos) is False

    def test_at_very_start_no_tags(self):
        """Позиция 0 в тексте без тегов."""
        assert _is_inside_html_tag("hello", 0) is False


# --- Тесты: содержимое code block не конвертируется как Markdown ---


class TestCodeBlockContentProtection:
    """Markdown-разметка внутри ``` остаётся литеральным текстом."""

    @pytest.mark.parametrize("inner_markdown", [
        "# heading",
        "**bold text**",
        "*italic text*",
        "_underscore italic_",
        "[link](http://url)",
        "- list item",
    ])
    def test_markdown_inside_code_block_not_converted(self, inner_markdown):
        """Markdown-разметка внутри code block не обрабатывается."""
        text = f"```\n{inner_markdown}\n```"
        result = markdown_to_html(text)
        plain = strip_html_tags(result)
        assert inner_markdown in plain

    def test_heading_inside_code_not_wrapped_in_bold(self):
        """# внутри code block — НЕ становится <b>."""
        result = markdown_to_html("```\n# not a heading\n```")
        assert "<b>" not in result

    def test_link_inside_code_not_converted_to_anchor(self):
        """[text](url) внутри code block — НЕ становится <a>."""
        result = markdown_to_html("```\n[text](http://url)\n```")
        assert "<a " not in result

    def test_bold_inside_code_block_stays_as_stars(self):
        """**bold** внутри code block — звёздочки сохраняются."""
        result = markdown_to_html("```\n**not bold**\n```")
        plain = strip_html_tags(result)
        assert "**not bold**" in plain


# --- Тесты: починка тегов через ВСЕ части многочастного split ---


class TestMultiPartTagRepairCompleteness:
    """Каждая часть многочастного сообщения имеет корректные теги."""

    def test_three_part_all_parts_have_open_and_close_b(self):
        """3 части с <b>: каждая начинается <b>, заканчивается </b>."""
        inner = "x " * 6000
        html_text = f"<b>{inner}</b>"
        result = split_html_message(html_text)
        assert len(result) >= 3, f"Ожидали 3+ частей, получили {len(result)}"
        for i, part in enumerate(result):
            assert part.lstrip().startswith("<b>"), (
                f"Часть {i} не начинается с <b>"
            )
            assert part.rstrip().endswith("</b>"), (
                f"Часть {i} не заканчивается </b>"
            )

    def test_four_plus_parts_pre_code_all_wrapped(self):
        """4+ частей с <pre><code>: каждая обёрнута корректно."""
        inner = "line\n" * 5000
        html_text = (
            f'<pre><code class="language-py">{inner}</code></pre>'
        )
        result = split_html_message(html_text)
        assert len(result) >= 4
        for i, part in enumerate(result):
            assert "<pre>" in part, f"Часть {i} без <pre>"
            assert "</pre>" in part, f"Часть {i} без </pre>"
            assert "<code" in part, f"Часть {i} без <code>"
            assert "</code>" in part, f"Часть {i} без </code>"

    def test_inline_code_tag_repaired_without_pre(self):
        """<code> без <pre> чинится при разрезе."""
        inner = "a" * 5000
        html_text = f"<code>{inner}</code>"
        result = split_html_message(html_text)
        assert len(result) >= 2
        assert result[0].endswith("</code>")
        assert result[1].startswith("<code>")

    def test_four_level_nesting_repaired(self):
        """4 уровня вложенности: <b><i><pre><code> — все чинятся."""
        inner = "x" * 5000
        html_text = (
            f"<b><i><pre><code>{inner}</code></pre></i></b>"
        )
        result = split_html_message(html_text)
        assert len(result) >= 2
        assert result[0].endswith("</code></pre></i></b>")
        assert result[1].startswith("<b><i><pre><code>")

    def test_content_not_lost_after_split(self):
        """Склейка частей (без repair-тегов) = исходный контент."""
        inner = "слово " * 2000
        html_text = f"<b>{inner}</b>"
        result = split_html_message(html_text)
        reconstructed = ""
        for part in result:
            content = part
            if content.startswith("<b>"):
                content = content[3:]
            if content.endswith("</b>"):
                content = content[:-4]
            reconstructed += content
        assert reconstructed.strip() == inner.strip()

    def test_all_parts_have_no_unclosed_tags(self):
        """Каждая часть после split — все теги закрыты."""
        inner = "x = 1\n" * 1000
        html_text = (
            f'<pre><code class="language-python">'
            f"{inner}"
            f"</code></pre>"
        )
        result = split_html_message(html_text)
        for i, part in enumerate(result):
            unclosed = _collect_open_tags(part)
            assert unclosed == [], (
                f"Часть {i} имеет незакрытые теги: {unclosed}"
            )


# --- Тесты: точные граничные значения длины ---


class TestSplitExactBoundaries:
    """Поведение на точных граничных значениях длины."""

    def test_4095_chars_no_split(self):
        """4095 символов — не разбивается."""
        text = "a" * (TELEGRAM_MESSAGE_LIMIT - 1)
        result = split_html_message(text)
        assert len(result) == 1

    def test_entity_expansion_forces_split(self):
        """& (1 символ) → &amp; (5 символов): 900 штук = 4500 > 4096."""
        text = "&" * 900
        result = prepare_message(text)
        assert len(result) >= 2
        combined = "".join(result)
        assert combined.count("&amp;") == 900
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT

    def test_deep_nesting_with_long_tags_stays_within_limit(self):
        """Длинные repair-теги не выводят часть за 4096."""
        long_url = "https://example.com/" + "x" * 80
        inner = "слово " * 2000
        html_text = (
            f'<a href="{long_url}"><b><i>{inner}</i></b></a>'
        )
        result = split_html_message(html_text)
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT, (
                f"Часть длиной {len(part)} > {TELEGRAM_MESSAGE_LIMIT}"
            )

    def test_no_empty_parts_after_split(self):
        """Ни одна часть после split не пустая."""
        text = "a" * 10000
        result = split_html_message(text)
        for i, part in enumerate(result):
            assert len(part) > 0, f"Часть {i} пустая"


# --- Тесты: парсер блоков кода — крайние вводы ---


class TestCodeBlockParserEdgeInputs:
    """Крайние случаи парсинга блоков кода."""

    def test_language_only_no_content(self):
        """```python без содержимого — язык определяется."""
        result = markdown_to_html("```python\n```")
        assert "language-python" in result

    def test_consecutive_blocks_no_separator(self):
        """Два блока подряд без текста между ними."""
        text = "```\nfirst\n```\n```\nsecond\n```"
        result = markdown_to_html(text)
        assert "first" in result
        assert "second" in result
        assert result.count("<pre>") == 2

    def test_language_with_plus_chars(self):
        """Язык c++ в блоке кода."""
        result = markdown_to_html("```c++\nint x = 0;\n```")
        assert "language-c++" in result

    def test_odd_delimiter_count(self):
        """3 разделителя ``` — первая пара = блок, третий без пары."""
        text = "```\ncode\n```\n\n```"
        blocks = _detect_nested_code_blocks(text)
        assert len(blocks) == 1

    def test_closing_delimiter_with_trailing_whitespace(self):
        """Пробелы после закрывающего ``` — блок закрывается."""
        text = "```\ncode\n```   "
        blocks = _detect_nested_code_blocks(text)
        assert len(blocks) == 1


# --- Тесты: inline-код — устойчивость к крайним вводам ---


class TestInlineCodeRobustness:
    """Устойчивость inline-кода к нестандартным вводам."""

    def test_unclosed_backtick_no_code_tag(self):
        """Незакрытый ` — не создаёт <code>."""
        result = markdown_to_html("hello `world")
        assert "<code>" not in result

    def test_empty_backticks_produce_no_code(self):
        """`` (пустые) — не создают code-сегмент."""
        segments = _split_by_inline_code("text `` more")
        code_segments = [s for s in segments if s[1]]
        assert code_segments == []

    def test_special_chars_inside_inline_code_escaped(self):
        """<, >, & внутри `code` экранируются."""
        result = markdown_to_html("Проверь `a < b && c > d`")
        assert "<code>" in result
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result

    def test_backtick_inside_bold_code_takes_precedence(self):
        """Inline-код внутри **bold** — код приоритетнее, ** не матчатся."""
        result = markdown_to_html("**жирный `код` тут**")
        assert "<code>код</code>" in result
        assert "<b>" not in result


# --- Тесты: типичные паттерны ответов Claude ---


class TestClaudeResponsePatterns:
    """Типичные паттерны ответов Claude — heading → code → text."""

    def test_heading_code_paragraph_sequence(self):
        """Заголовок → блок кода → параграф."""
        text = (
            "## Решение\n\n"
            "```python\nprint('hello')\n```\n\n"
            "Готово."
        )
        result = markdown_to_html(text)
        assert "<b>Решение</b>" in result
        assert "<pre>" in result
        assert "Готово." in result

    @pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
    def test_all_heading_levels_become_bold(self, level):
        """H1–H6 — все конвертируются в <b>."""
        text = f"{'#' * level} Заголовок"
        result = markdown_to_html(text)
        assert "<b>Заголовок</b>" in result

    def test_list_code_list_sequence(self):
        """Список → код → список."""
        text = (
            "- пункт 1\n- пункт 2\n\n"
            "```\ncode\n```\n\n"
            "- пункт 3"
        )
        result = markdown_to_html(text)
        assert "пункт 1" in result
        assert "<pre>" in result
        assert "пункт 3" in result

    def test_long_mixed_content_all_parts_within_limit(self):
        """Длинный смешанный контент — все части ≤ 4096."""
        sections = []
        for i in range(5):
            sections.append(f"## Секция {i}")
            sections.append(f"Описание секции {i}. " * 20)
            sections.append(f"```python\n{'x = 1\n' * 30}```")
        text = "\n\n".join(sections)
        result = prepare_message(text)
        for part in result:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT

    def test_horizontal_rule_does_not_crash(self):
        """--- (горизонтальная линия) — парсер не ломается."""
        text = "Текст выше\n\n---\n\nТекст ниже"
        result = markdown_to_html(text)
        assert "Текст выше" in result
        assert "Текст ниже" in result


# --- Тесты: стек незакрытых тегов — нестандартные случаи ---


class TestCollectOpenTagsRobustness:
    """Устойчивость стека тегов к нестандартным вводам."""

    def test_duplicate_same_tag_one_closed(self):
        """<b><b>text</b> — один <b> остаётся незакрытым."""
        tags = _collect_open_tags("<b><b>текст</b>")
        tag_names = [name for name, _ in tags]
        assert tag_names == ["b"]

    def test_all_tags_properly_closed(self):
        """Все теги закрыты — пустой стек."""
        tags = _collect_open_tags(
            "<b>жирный</b><i>курсив</i>"
        )
        assert tags == []

    def test_closing_tag_without_opening(self):
        """</b> без открывающего — не ломает стек."""
        tags = _collect_open_tags("</b>текст<i>курсив")
        tag_names = [name for name, _ in tags]
        assert tag_names == ["i"]

    def test_three_levels_same_tag(self):
        """<b><b><b>text</b></b> — один <b> остаётся."""
        tags = _collect_open_tags("<b><b><b>текст</b></b>")
        tag_names = [name for name, _ in tags]
        assert tag_names == ["b"]


# --- Тесты: декодирование HTML-сущностей (параметризация) ---


class TestStripHtmlEntityDecoding:
    """Декодирование разных типов HTML-сущностей."""

    @pytest.mark.parametrize("entity,expected", [
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#x27;", "'"),
        ("&#39;", "'"),
        ("&#60;", "<"),
    ])
    def test_entity_decoded_correctly(self, entity, expected):
        """HTML-сущность декодируется в соответствующий символ."""
        result = strip_html_tags(f"a{entity}b")
        assert result == f"a{expected}b"

    def test_mixed_entities_and_tags_decoded(self):
        """Теги удаляются, сущности декодируются одновременно."""
        result = strip_html_tags(
            "<b>a &amp; b</b> &lt; <i>c</i>"
        )
        assert result == "a & b < c"


# --- Тесты: классификация параграфов через публичный API ---


class TestParagraphClassificationEdgeCases:
    """Классификация параграфов: заголовки, списки, обычный текст."""

    def test_hash_without_text_not_heading(self):
        """'# ' без текста — не заголовок, <b> не появляется."""
        result = markdown_to_html("# ")
        assert "<b></b>" not in result
        assert "<b>" not in result

    def test_multiline_starting_with_hash_is_paragraph(self):
        """Текст с # и переносом строки без \n\n — один параграф."""
        result = markdown_to_html(
            "# Заголовок\nпродолжение строки"
        )
        assert result is not None
        assert "# Заголовок" in result

    def test_triple_newline_separates_paragraphs(self):
        """Тройной перенос разделяет параграфы так же, как двойной."""
        result = markdown_to_html("Первый\n\n\nВторой")
        assert "Первый" in result
        assert "Второй" in result
