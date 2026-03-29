"""Юнит-тесты для модуля message_splitter."""

import pytest

from claude_manager.message_splitter import (
    TELEGRAM_MESSAGE_LIMIT,
    _collect_open_tags,
    _close_tags,
    _reopen_tags,
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
