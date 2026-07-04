"""Отбрасывание недописанной последней строки JSONL-файла сессии.

CLI дописывает файл сессии на лету: последняя строка может быть оборвана
посреди JSON-записи (без завершающего перевода строки). Если посчитать её в
raw_record_count, дописанная позже запись получит уже «прочитанный» raw-индекс
и не будет доставлена никогда (P2-26 полного ревью 04.07.2026).
"""

import json


def drop_incomplete_trailing_jsonl_line(raw_lines: list[str]) -> list[str]:
    """Убирает последнюю строку без перевода строки, если она не парсится как JSON."""
    if not raw_lines:
        return raw_lines
    last_line = raw_lines[-1]
    if last_line.endswith("\n"):
        return raw_lines
    stripped_last_line = last_line.strip()
    if not stripped_last_line:
        return raw_lines
    try:
        json.loads(stripped_last_line)
    except json.JSONDecodeError:
        return raw_lines[:-1]
    # Без '\n', но валидный JSON: запись завершена, просто writer не добавил
    # перевод строки после финальной записи файла.
    return raw_lines
