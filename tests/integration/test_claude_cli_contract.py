"""Интеграционный тест контракта с Claude Code CLI.

Проверяет, что `_encode_project_path` (функция из `src/claude_manager/session_reader.py`,
которая превращает путь проекта в имя папки в `~/.claude/projects/`) воспроизводит
реальный алгоритм `sanitizePath()` из Claude CLI
(`claude-code-sourcecode/utils/sessionStoragePortable.ts:311`).

Зачем нужен этот тест. Юнит-тесты проверяют реализацию против ожиданий разработчика —
они останутся зелёными, даже если Claude CLI завтра поменяет свой алгоритм кодирования
путей. А этот тест реально запускает `claude -p` как подпроцесс и сверяет имя папки,
которую создал CLI, с тем, что вернула наша функция. Если Claude CLI обновится и
регулярка изменится, тест упадёт — это сигнал, что нужно чинить `_encode_project_path`.

Это материализация принципа «Контракты с внешними системами проверяются эмпирически,
а не по догадке» (см. `CLAUDE.md`, раздел «Важные детали для разработки»).

Тест скипается, если `claude` не установлен в PATH.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from claude_manager.claude_code_backend import ClaudeCodeBackend, _encode_project_path

# Таймаут на вызов `claude -p` в секундах. Если CLI зависнет, не ждём вечно —
# тест упадёт с TimeoutExpired, а не будет висеть.
CLAUDE_CLI_TIMEOUT_SECONDS = 60

# Минимальный промпт — Claude должен ответить одним словом и сразу завершиться.
MINIMAL_PROMPT = "say hi in one word"

# Папка, где Claude Code CLI хранит сессии проектов.
CLAUDE_PROJECTS_SUBDIR = ".claude/projects"

REAL_CLAUDE_BINARY = Path(
    os.environ.get("CLAUDE_REAL_BIN", "/Users/ivan/.npm-global/bin/claude")
)
CLAUDE_BINARY = (
    str(REAL_CLAUDE_BINARY)
    if REAL_CLAUDE_BINARY.exists()
    else None
)


@pytest.mark.skipif(
    CLAUDE_BINARY is None,
    reason="Реальный Claude CLI не найден — контрактный тест пропускается",
)
def test_encode_project_path_matches_real_claude_cli(tmp_path: Path) -> None:
    """Контрактный тест: `_encode_project_path` даёт то же имя папки,
    что и реальный Claude CLI для пути с подчёркиванием в имени."""
    # Создаём подпапку с подчёркиванием в имени — проблемный случай, на котором
    # раньше ломалась старая реализация _encode_project_path.
    project_dir = tmp_path / "my_test_project_claude_cli_contract"
    project_dir.mkdir()

    # На macOS `tmp_path` живёт под симлинком `/var/folders/...` → `/private/var/folders/...`.
    # Claude CLI резолвит cwd через realpath перед кодированием, поэтому мы тоже
    # резолвим путь — иначе ожидаемое имя не совпадёт с реальным.
    resolved_project_dir = project_dir.resolve()
    expected_folder_name = _encode_project_path(str(resolved_project_dir))

    claude_projects_root = Path.home() / CLAUDE_PROJECTS_SUBDIR
    backend = ClaudeCodeBackend()
    expected_folder_path = Path(
        backend.locate_session_files_directory_for_project(str(resolved_project_dir))
    )
    assert expected_folder_path.name == expected_folder_name

    # Если папка по какой-то причине уже существует от предыдущего прогона —
    # чистим, чтобы тест начал с нуля и проверил именно создание новой.
    if expected_folder_path.exists():
        shutil.rmtree(expected_folder_path, ignore_errors=True)

    cli_stdout = ""
    cli_stderr = ""
    try:
        # Запускаем `claude -p` с изолированным cwd. Набор флагов:
        # -p — неинтерактивный режим (напечатать ответ и выйти)
        # --output-format text — просто текст, без stream-json
        # --dangerously-skip-permissions — пропустить диалог разрешений
        # --max-budget-usd 1 — потолок расходов на случай зависания модели
        # --tools "" — отключить все инструменты (нам нужно только создание папки сессии)
        # --disable-slash-commands — отключить скиллы, чтобы не тянуть лишнее
        command = [
            CLAUDE_BINARY,
            "-p",
            "--output-format", "text",
            "--dangerously-skip-permissions",
            "--max-budget-usd", "1",
            "--tools", "",
            "--disable-slash-commands",
            MINIMAL_PROMPT,
        ]

        # env без CLAUDECODE — иначе CLI может думать, что он уже внутри Claude Code сессии.
        child_env = os.environ.copy()
        child_env.pop("CLAUDECODE", None)
        child_env["CLAUDE_REAL_BIN"] = str(REAL_CLAUDE_BINARY)

        completed = subprocess.run(
            command,
            cwd=str(resolved_project_dir),
            capture_output=True,
            text=True,
            timeout=CLAUDE_CLI_TIMEOUT_SECONDS,
            env=child_env,
            check=False,
        )
        cli_stdout = completed.stdout
        cli_stderr = completed.stderr

        # Собираем список папок, начинающихся на тот же префикс — это поможет
        # диагностировать, если имя отличается от ожидаемого.
        if not expected_folder_path.exists():
            siblings = []
            if claude_projects_root.exists():
                siblings = sorted(
                    entry.name
                    for entry in claude_projects_root.iterdir()
                    if "claude-cli-contract" in entry.name
                    or "my-test-project" in entry.name
                )
            pytest.fail(
                "Claude CLI не создал папку сессии по ожидаемому имени.\n"
                f"  Ожидали: {expected_folder_path}\n"
                f"  CLI binary: {CLAUDE_BINARY}\n"
                f"  CLI exit code: {completed.returncode}\n"
                f"  CLI stdout (первые 500 символов): {cli_stdout[:500]!r}\n"
                f"  CLI stderr (первые 500 символов): {cli_stderr[:500]!r}\n"
                f"  Похожие папки в ~/.claude/projects/: {siblings}\n"
                "Это значит, что реальный алгоритм sanitizePath() в Claude CLI "
                "изменился и _encode_project_path больше не соответствует ему — "
                "посмотри в siblings, какое имя папки CLI сгенерировал на самом деле, "
                "и обнови регулярку SANITIZE_PATH_PATTERN в session_reader.py."
            )
    except subprocess.TimeoutExpired as error:
        pytest.fail(
            f"Claude CLI не ответил за {CLAUDE_CLI_TIMEOUT_SECONDS} секунд — "
            f"возможно, завис или ждёт ввода.\n"
            f"  Командная строка: {error.cmd}"
        )
    finally:
        # Всегда подчищаем за собой, чтобы тест не оставлял мусор в ~/.claude/projects/.
        if expected_folder_path.exists():
            shutil.rmtree(expected_folder_path, ignore_errors=True)


@pytest.mark.skipif(
    CLAUDE_BINARY is None,
    reason="Реальный Claude CLI не найден — контрактный тест пропускается",
)
async def test_claude_backend_stream_json_and_session_file_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Контрактный тест: backend stdin, stream-json stdout и JSONL-файл совместимы."""
    backend = ClaudeCodeBackend()
    project_dir = tmp_path / "stream_json_claude_cli_contract"
    project_dir.mkdir()
    resolved_project_dir = project_dir.resolve()
    expected_folder_path = Path(
        backend.locate_session_files_directory_for_project(str(resolved_project_dir))
    )

    if expected_folder_path.exists():
        shutil.rmtree(expected_folder_path, ignore_errors=True)

    monkeypatch.setattr(
        "claude_manager.claude_code_backend._resolve_claude_binary_path",
        lambda: str(REAL_CLAUDE_BINARY),
    )
    command = backend.compose_subprocess_command_args(
        "_new_contract123",
        str(resolved_project_dir),
        MINIMAL_PROMPT,
        [],
    )
    child_env = os.environ.copy()
    child_env.pop("CLAUDECODE", None)
    child_env["CLAUDE_REAL_BIN"] = str(REAL_CLAUDE_BINARY)

    try:
        completed = subprocess.run(
            command,
            cwd=str(resolved_project_dir),
            input=backend.encode_user_message_for_cli_stdin(MINIMAL_PROMPT, []),
            capture_output=True,
            timeout=CLAUDE_CLI_TIMEOUT_SECONDS,
            env=child_env,
            check=False,
        )
        assert completed.returncode == 0, (
            "Claude CLI stream-json contract run failed.\n"
            f"  CLI binary: {REAL_CLAUDE_BINARY}\n"
            f"  stdout: {completed.stdout[:500]!r}\n"
            f"  stderr: {completed.stderr[:500]!r}"
        )

        events = [
            parsed_event
            for raw_line in completed.stdout.decode("utf-8").splitlines()
            if (parsed_event := backend.parse_stdout_line_into_event(raw_line))
        ]
        assert events, "Claude CLI produced no stream-json events"
        assert any(event.get("type") == "system" for event in events)
        result_events = [
            event for event in events if backend.is_turn_complete_event(event)
        ]
        assert result_events, "Claude CLI produced no terminal result event"
        session_id = backend.read_session_id_from_event(result_events[-1])
        assert session_id

        session_file = expected_folder_path / f"{session_id}.jsonl"
        assert session_file.exists()
        snapshot = await backend.read_session_file_snapshot(str(session_file))
        assert snapshot.raw_record_count > 0
        assert snapshot.last_record is not None
        assert snapshot.messages
    finally:
        if expected_folder_path.exists():
            shutil.rmtree(expected_folder_path, ignore_errors=True)
