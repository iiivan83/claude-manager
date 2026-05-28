# All Projects Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a global `/all` mode that shows assistant messages from every available project, while preserving normal project unread delivery and blocking agent input until the user enters a concrete project session.

**Architecture:** Use a separate `all_projects_monitor` module with its own cursors and link registry. Keep `session_watcher` responsible only for the active project, and use `unread_buffer` snapshots so messages seen in global all mode are replayed after switching into the source project.

**Tech Stack:** Python 3.13, asyncio, python-telegram-bot, pytest, pytest-asyncio, existing `CodingAgentBackend` adapters, existing `unread_buffer` pending-delivery path.

---

## Superpowers Rules

- Use `superpowers:using-superpowers` for every execution session.
- Use `superpowers:using-git-worktrees` before production-code edits if the current checkout is not already an isolated worktree.
- Use `superpowers:test-driven-development` for each behavior change: write or restore the failing test first, run it, then write production code.
- Use `superpowers:verification-before-completion` before marking any task complete.
- Use `superpowers:requesting-code-review` after the full implementation passes targeted and full tests.
- Keep commits small. Commit after each task that leaves tests passing.

## Reference Commit

The reverted implementation in commit `520b42b` is useful reference material, especially:

- `src/claude_manager/all_projects_monitor.py`
- `tests/test_all_projects_monitor.py`
- the `/all`, `/projects`, `/3s12`, and all-mode input-blocking tests in `tests/test_bot.py`
- `project_manager.collect_pending_messages_for_project`

Do not restore `src/claude_manager/bot.py` wholesale from that commit. It can overwrite newer bot code after the revert. Copy only the specific test ideas and implementation pieces needed for the current files.

## File Structure

- Create: `src/claude_manager/all_projects_monitor.py`
  Separate global monitor. Tracks enabled chats, scans all projects and all backends, keeps all-mode cursors, stores link targets for `/<project>s<session>`, and saves unread snapshots before displaying all-mode messages.
- Create: `tests/test_all_projects_monitor.py`
  Unit tests for all-mode enable, failure rollback, polling, unread snapshot semantics, link registry, and scanner error isolation.
- Modify: `src/claude_manager/bot.py`
  Telegram integration: start the monitor loop, render `/all all`, block input in all-project mode, format all-mode messages, parse `/<project_number>s<session_number>`, switch project, bind exact session, and deliver pending messages.
- Modify: `tests/test_bot.py`
  Unit tests for `/all`, `/projects`, `/new`, text, photo, document, all-mode message formatting, `/<project_number>s<session_number>`, and already-active project pending collection.
- Modify: `src/claude_manager/project_manager.py`
  Add a public wrapper for collecting pending messages for an already active project.
- Modify: `tests/test_project_manager.py`
  Add a focused test for the public pending wrapper.
- Do not modify: `.claude/**`, root `CLAUDE.md`, root `AGENTS.md`, generated Codex mirrors under `.agents/**`.

---

### Task 0: Workspace And Baseline

**Files:**
- Modify: none
- Test: existing suite

- [ ] **Step 1: Confirm repository state**

Run:

```bash
git status --short --branch
git log --oneline -5
```

Expected:
- Branch is `codex-support-spec-implementation-cycle`, unless the user explicitly moved work to another branch.
- Existing unrelated changes may remain:
  - `dev/docs/docs-index.md`
  - `dev/docs/session-reports/13-05/14-53_restart-active-child-sessions-bug.md`
- The design commit `081deb2 docs: add all projects monitoring design` is in the last commits.

- [ ] **Step 2: Check whether this is an isolated worktree**

Run:

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" 2>/dev/null && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P)
git rev-parse --show-superproject-working-tree 2>/dev/null || true
printf 'GIT_DIR=%s\nGIT_COMMON=%s\n' "$GIT_DIR" "$GIT_COMMON"
```

Expected:
- If `GIT_DIR != GIT_COMMON` and no superproject path is printed, continue in the existing isolated worktree.
- If `GIT_DIR == GIT_COMMON`, ask the user before creating a new Superpowers worktree or before implementing on this checkout.

- [ ] **Step 3: Run baseline unit tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_all_projects_monitor.py tests/test_bot.py tests/test_project_manager.py -q
```

Expected:
- `tests/test_all_projects_monitor.py` does not exist yet, so this exact command fails with a file-not-found message.
- Existing `tests/test_bot.py` and `tests/test_project_manager.py` should still be runnable separately if needed:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_project_manager.py -q
```

Expected: all existing tests in those two files pass before implementation starts. If they fail, stop and decide whether to fix baseline first.

- [ ] **Step 4: Commit baseline-only changes**

No commit is expected in Task 0 unless the user asked to record baseline notes.

---

### Task 1: All-Projects Monitor Tests

**Files:**
- Create: `tests/test_all_projects_monitor.py`
- Test: `tests/test_all_projects_monitor.py`

- [ ] **Step 1: Create the failing monitor test file**

Create `tests/test_all_projects_monitor.py` with these test helpers and tests:

```python
"""Tests for global all-project monitoring."""

from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import all_projects_monitor, config, project_manager, unread_buffer
from claude_manager.coding_agent_backend import (
    BackendName,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    SessionUnreadState,
)


CHAT_ID = 12345


class FakeBackend:
    """Small backend double for all-project monitor tests."""

    def __init__(
        self,
        name: BackendName,
        files_by_project: dict[str, list[SessionFileInfo]],
    ) -> None:
        self.name = name
        self.display_name = name.value
        self.files_by_project = files_by_project
        self.snapshots: dict[str, SessionFileSnapshot] = {}

    async def list_all_session_files_for_project(
        self,
        project_dir: str,
    ) -> list[SessionFileInfo]:
        return self.files_by_project.get(project_dir, [])

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        return self.snapshots[file_path]


class FailingBackend:
    """Backend double that fails while listing project session files."""

    name = BackendName.CODEX
    display_name = "Codex"

    async def list_all_session_files_for_project(
        self,
        _project_dir: str,
    ) -> list[SessionFileInfo]:
        raise OSError("backend unavailable")


def _project(
    name: str,
    path: str,
    is_current: bool = False,
) -> project_manager.ProjectInfo:
    """Build a project info test value."""
    return project_manager.ProjectInfo(
        name=name,
        absolute_path=path,
        is_current=is_current,
    )


def _file(
    session_id: str,
    file_path: str,
    mtime: float = 1.0,
) -> SessionFileInfo:
    """Build session file metadata."""
    return SessionFileInfo(
        session_id=session_id,
        file_path=file_path,
        last_modified_at=mtime,
        preview="preview",
    )


def _snapshot(
    messages: list[SessionMessage],
    raw_count: int | None = None,
    is_turn_active: bool = False,
) -> SessionFileSnapshot:
    """Build a backend-neutral session snapshot."""
    return SessionFileSnapshot(
        messages=messages,
        raw_record_count=raw_count if raw_count is not None else len(messages),
        last_record={},
        is_turn_active=is_turn_active,
    )


def _message(role: str, text: str) -> SessionMessage:
    """Build a session message."""
    return SessionMessage(
        role=role,
        text=text,
        timestamp=None,
        is_empty_response=False,
    )


@pytest.fixture(autouse=True)
def _reset_monitor_state():
    """Keep module globals isolated between tests."""
    all_projects_monitor.reset_state()
    unread_buffer._snapshots.clear()
    yield
    all_projects_monitor.reset_state()
    unread_buffer._snapshots.clear()


@pytest.mark.asyncio()
async def test_enable_for_chat_pauses_current_watcher_and_marks_mode() -> None:
    """Enabling all mode pauses normal watcher so it cannot mark messages as read."""
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/alpha": []})
    projects = [_project("alpha", "/projects/alpha", is_current=True)]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ) as pause_all_mock:
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    pause_all_mock.assert_called_once()
    assert all_projects_monitor.is_enabled_for_chat(CHAT_ID) is True


@pytest.mark.asyncio()
async def test_enable_for_chat_resumes_watcher_when_baseline_fails() -> None:
    """A failed all-mode entry must not leave the normal watcher globally paused."""
    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(side_effect=RuntimeError("scan failed")),
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ) as pause_all_mock, patch(
        "claude_manager.all_projects_monitor.session_watcher.resume_all",
    ) as resume_all_mock:
        with pytest.raises(RuntimeError, match="scan failed"):
            await all_projects_monitor.enable_for_chat(CHAT_ID)

    pause_all_mock.assert_called_once()
    resume_all_mock.assert_called_once()
    assert all_projects_monitor.is_enabled_for_chat(CHAT_ID) is False


@pytest.mark.asyncio()
async def test_poll_delivers_all_project_message_and_keeps_unread_snapshot() -> None:
    """All monitor delivers a new assistant message and leaves it unread for project switch."""
    file_info = _file("sess-beta", "/sessions/beta.jsonl", mtime=20.0)
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "answer"),
    ])
    callback = AsyncMock()

    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ):
        await all_projects_monitor.poll_once(callback)

    callback.assert_awaited_once()
    call = callback.call_args.args
    assert call[0] == CHAT_ID
    assert call[1] == 1
    assert call[2] == 1
    assert call[3] == "beta"
    assert call[4] == "sess-beta"
    assert call[5] == BackendName.CLAUDE
    assert call[6] == "answer"

    unread_state = unread_buffer.restore_snapshot("sess-beta", BackendName.CLAUDE)
    assert unread_state == SessionUnreadState(
        raw_record_count=1,
        last_delivered_idx=0,
    )


@pytest.mark.asyncio()
async def test_existing_unread_snapshot_is_not_overwritten() -> None:
    """All mode must not replace older unread cursors captured by project switching."""
    unread_buffer.save_snapshot(
        "sess-beta",
        BackendName.CLAUDE,
        raw_record_count=3,
        last_delivered_idx=2,
    )
    file_info = _file("sess-beta", "/sessions/beta.jsonl")
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "old"),
        _message("assistant", "old answer"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    assert unread_buffer.restore_snapshot(
        "sess-beta",
        BackendName.CLAUDE,
    ) == SessionUnreadState(
        raw_record_count=3,
        last_delivered_idx=2,
    )


@pytest.mark.asyncio()
async def test_resolve_link_returns_project_and_session_target() -> None:
    """The displayed /<project>s<session> command resolves back to the exact session."""
    file_info = _file("sess-beta", "/sessions/beta.jsonl")
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    target = all_projects_monitor.resolve_link(
        project_number=1,
        session_number=1,
    )
    assert target == all_projects_monitor.AllProjectSessionLink(
        project_number=1,
        session_number=1,
        project_name="beta",
        project_path="/projects/beta",
        session_id="sess-beta",
        backend=BackendName.CLAUDE,
    )


@pytest.mark.asyncio()
async def test_poll_continues_when_one_backend_scan_fails() -> None:
    """A failing backend does not prevent other backends from delivering messages."""
    file_info = _file("sess-alpha", "/sessions/alpha.jsonl")
    working_backend = FakeBackend(BackendName.CLAUDE, {"/projects/alpha": [file_info]})
    working_backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("alpha", "/projects/alpha")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[FailingBackend(), working_backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    working_backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "answer"),
    ])
    callback = AsyncMock()

    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[FailingBackend(), working_backend],
    ):
        await all_projects_monitor.poll_once(callback)

    callback.assert_awaited_once()
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_all_projects_monitor.py -q
```

Expected: FAIL because `claude_manager.all_projects_monitor` does not exist yet.

- [ ] **Step 3: Commit only if no production code was touched**

Do not commit a failing test by itself unless the user explicitly wants red-state commits. Continue to Task 2 in the same working tree.

---

### Task 2: All-Projects Monitor Implementation

**Files:**
- Create: `src/claude_manager/all_projects_monitor.py`
- Test: `tests/test_all_projects_monitor.py`

- [ ] **Step 1: Add the monitor module**

Implement `src/claude_manager/all_projects_monitor.py` with these public names:

```python
"""Global all-project session monitoring.

This module scans session files across every configured project while keeping
its own delivery cursors. It intentionally does not advance the normal
project watcher state, so messages shown in all-project mode remain pending
when the user switches into the concrete project.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from claude_manager import (
    coding_agent_backend,
    config,
    project_manager,
    session_watcher,
    unread_buffer,
)
from claude_manager.coding_agent_backend import (
    BackendName,
    CodingAgentBackend,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = session_watcher.POLL_INTERVAL_SECONDS
ERROR_RETRY_DELAY_SECONDS = session_watcher.ERROR_RETRY_DELAY_SECONDS
REGISTRY_FILENAME = "daily_sessions.json"
DATE_FORMAT = "%Y-%m-%d"

AllProjectsMessageCallback = Callable[
    [int, int, int, str, str, BackendName, str, bool],
    Awaitable[None],
]


@dataclass(frozen=True)
class AllProjectSessionLink:
    """Target encoded by a /<project>s<session> command in all mode."""

    project_number: int
    session_number: int
    project_name: str
    project_path: str
    session_id: str
    backend: BackendName


@dataclass(frozen=True)
class _ProjectSession:
    """One visible session with all numbering needed for display and routing."""

    project_number: int
    project_name: str
    project_path: str
    session_number: int
    file_info: SessionFileInfo
    backend: CodingAgentBackend


@dataclass
class _AllMonitorState:
    """Delivery cursor for one project/session/backend in all mode."""

    raw_record_count: int = 0
    parsed_message_count: int = 0
    last_delivered_idx: int = -1
    is_turn_active: bool = False


_enabled_chat_ids: set[int] = set()
_states: dict[tuple[str, str, BackendName], _AllMonitorState] = {}
_links: dict[tuple[int, int], AllProjectSessionLink] = {}
_lock = asyncio.Lock()
```

Then add the behavior in small functions:

- `reset_state()` clears `_enabled_chat_ids`, `_states`, and `_links`.
- `is_enabled_for_chat(chat_id)` returns membership in `_enabled_chat_ids`.
- `has_enabled_chats()` returns whether any chat is enabled.
- `disable_for_chat(chat_id)` removes a chat and returns whether it was enabled.
- `resolve_link(project_number, session_number)` returns `_links.get((project_number, session_number))`.
- `_state_key(project_path, session_id, backend)` returns `(project_path, session_id, backend)`.
- `_message_should_be_delivered(message)` returns true only for non-empty assistant messages.
- `_load_project_today_numbers(project_path)` reads `daily_sessions.json` for `date.today().strftime(DATE_FORMAT)` without mutating `daily_session_registry`.
- `_assign_session_numbers(...)` prefers registry numbers and uses stable fallback numbers above the registry max for unregistered session files.
- `_collect_project_sessions()` scans `project_manager.scan_available_projects()` and every `coding_agent_backend.get_all_backends()` backend. It logs and continues when one backend fails.
- `_build_baseline_states(project_sessions)` reads snapshots and initializes all-mode cursors. For the current active project, use `session_watcher.get_seen_counts_snapshot(backend)` so all mode starts from the same unread point as the normal watcher.
- `enable_for_chat(chat_id)` calls `session_watcher.pause_all()` first, builds baseline state, marks chat enabled, and calls `session_watcher.resume_all()` if any error happens before enable succeeds.
- `_candidate_indices(previous, snapshot)` mirrors `session_watcher` active-turn behavior by not delivering the last parsed message while a turn is still active.
- `_ensure_unread_snapshot(session_id, backend, previous)` writes an `unread_buffer.save_snapshot(...)` only if `unread_buffer.restore_snapshot(...)` is absent.
- `_next_state_from_snapshot(previous, snapshot)` mirrors `session_watcher` cursor advancement.
- `_deliver_project_session_delta(...)` saves the unread snapshot before callback delivery and then calls the callback for each enabled chat.
- `_check_project_session(...)` reads one session file snapshot, compares it to previous state, delivers delta, and advances only `_states`.
- `poll_once(callback)` scans all project sessions and returns immediately when no chats are enabled.
- `start(callback)` runs an infinite loop with the same sleep and retry behavior as `session_watcher.start`.

Use commit `520b42b:src/claude_manager/all_projects_monitor.py` as the reference implementation for exact control flow, but keep these guardrails:

- Do not import `daily_session_registry`; all-project scans must not switch or mutate the current project registry.
- Do not call `session_watcher.reset_state()` from this module.
- Do not call `session_watcher.resume_all()` from `disable_for_chat`; `bot.py` owns resume timing after project/session switching.
- Do not overwrite an existing `unread_buffer` snapshot.

- [ ] **Step 2: Run monitor tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_all_projects_monitor.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit monitor module**

Run:

```bash
git add src/claude_manager/all_projects_monitor.py tests/test_all_projects_monitor.py
git commit -m "feat: add all projects monitor core"
```

Expected: commit contains only the new monitor module and its tests.

---

### Task 3: Pending Collection Wrapper

**Files:**
- Modify: `src/claude_manager/project_manager.py`
- Modify: `tests/test_project_manager.py`
- Test: `tests/test_project_manager.py`

- [ ] **Step 1: Add failing wrapper test**

Append this test to `TestSwitchProject` in `tests/test_project_manager.py`:

```python
    @pytest.mark.asyncio()
    async def test_collect_pending_messages_for_active_project_uses_existing_collector(
        self,
        projects_root: Path,
        last_project_file: Path,
    ) -> None:
        """Public wrapper collects pending messages without switching project."""
        target = projects_root / "project_alpha"
        session_id = "claude-session"
        file_path = str(target / "session.jsonl")
        session_file = _session_file(session_id, file_path)
        snapshot = SessionFileSnapshot(
            messages=[
                _assistant_message("old"),
                _assistant_message("new"),
            ],
            raw_record_count=2,
            last_record=None,
            is_turn_active=False,
        )
        fake_backend = FakeProjectBackend(
            BackendName.CLAUDE,
            session_files=[session_file],
            snapshots={file_path: snapshot},
        )
        unread_buffer.save_snapshot(
            session_id,
            BackendName.CLAUDE,
            raw_record_count=1,
            last_delivered_idx=0,
        )

        patches = _patch_config_paths(projects_root, target, last_project_file)
        with patches[0], patches[1], patches[2], patch.object(
            coding_agent_backend,
            "get_all_backends",
            return_value=[fake_backend],
        ):
            count, pending = await project_manager.collect_pending_messages_for_project(
                str(target)
            )

        assert count == 1
        assert pending[0].session_id == session_id
        assert pending[0].text == "new"
        assert pending[0].backend == BackendName.CLAUDE
        assert pending[0].is_final is True
```

- [ ] **Step 2: Run the wrapper test and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_project_manager.py::TestSwitchProject::test_collect_pending_messages_for_active_project_uses_existing_collector -q
```

Expected: FAIL because `collect_pending_messages_for_project` does not exist.

- [ ] **Step 3: Add the public wrapper**

In `src/claude_manager/project_manager.py`, add this function immediately after `_collect_pending_messages(...)`:

```python
async def collect_pending_messages_for_project(
    target_path: str,
) -> tuple[int, list[PendingDeliveryItem]]:
    """Public wrapper collecting unread messages for an already active project."""
    return await _collect_pending_messages(target_path)
```

- [ ] **Step 4: Run project manager tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_project_manager.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit wrapper**

Run:

```bash
git add src/claude_manager/project_manager.py tests/test_project_manager.py
git commit -m "feat: expose active project pending collection"
```

Expected: commit contains only `project_manager` and its tests.

---

### Task 4: Bot Tests For All Mode

**Files:**
- Modify: `tests/test_bot.py`
- Test: `tests/test_bot.py`

- [ ] **Step 1: Add imports for the new monitor**

In `tests/test_bot.py`, import `all_projects_monitor` from `claude_manager`, and import these bot symbols:

```python
    ALL_PROJECTS_MODE_INPUT_WARNING,
    ALL_PROJECTS_MODE_LINE,
    handle_switch_project_session,
    send_all_projects_watcher_message,
```

- [ ] **Step 2: Add `/new` blocking test**

Add this test to `TestHandleNew`:

```python
    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "create_new_session", new_callable=AsyncMock)
    async def test_handle_new_blocked_in_all_projects_mode(
        self,
        mock_create_session: AsyncMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """In global all mode /new must not create a hidden session."""
        mock_is_all_projects.return_value = True

        update = _make_update(text="/new")
        context = _make_context()
        await handle_new(update, context)

        mock_create_session.assert_not_awaited()
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()
```

- [ ] **Step 3: Replace `/all` behavior test**

Update the existing `TestHandleAll.test_handle_all_switches_to_monitoring` so it patches and asserts `all_projects_monitor.enable_for_chat(TEST_CHAT_ID)`:

```python
    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "enable_for_chat", new_callable=AsyncMock)
    @patch.object(session_manager, "unbind_session", new_callable=AsyncMock)
    async def test_handle_all_switches_to_monitoring(
        self,
        mock_unbind: AsyncMock,
        mock_enable_all_projects: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Command /all enables global monitoring across projects."""
        update = _make_update(text="/all")
        context = _make_context()
        await handle_all(update, context)

        mock_unbind.assert_called_once_with(TEST_CHAT_ID)
        mock_enable_all_projects.assert_awaited_once_with(TEST_CHAT_ID)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "all" in sent_text.lower()
        assert "проект" in sent_text.lower()
```

- [ ] **Step 4: Add all-mode text warning test**

Add this test to `TestHandleMessage`:

```python
    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_in_all_projects_mode_mentions_project(
        self,
        mock_is_monitoring: MagicMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """All-project mode text is blocked with a project/session warning."""
        mock_is_monitoring.return_value = True
        mock_is_all_projects.return_value = True

        update = _make_update(text="запрос")
        context = _make_context()
        await handle_message(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()
```

- [ ] **Step 5: Add photo and document all-mode warning tests**

Add one test to `TestHandlePhoto` and one to `TestHandleDocument`:

```python
    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_photo_in_all_projects_mode_mentions_project(
        self,
        mock_is_monitoring: MagicMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Photo input is blocked in global all mode."""
        mock_is_monitoring.return_value = True
        mock_is_all_projects.return_value = True

        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context()
        await handle_photo(update, context)

        sent_text = _setup_application.bot.send_message.call_args.args[1]
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()
```

```python
    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_document_in_all_projects_mode_mentions_project(
        self,
        mock_is_monitoring: MagicMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Document input is blocked in global all mode."""
        mock_is_monitoring.return_value = True
        mock_is_all_projects.return_value = True

        update = _make_update()
        update.message.document = MagicMock()
        context = _make_context()
        await handle_document(update, context)

        sent_text = _setup_application.bot.send_message.call_args.args[1]
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()
```

- [ ] **Step 6: Add all-mode message-formatting test**

Add a new `TestSendAllProjectsWatcherMessage` class near `TestSendWatcherMessage`:

```python
class TestSendAllProjectsWatcherMessage:
    """Tests for global all-mode message sending."""

    @pytest.mark.asyncio()
    async def test_header_starts_with_project_and_session_command(
        self,
        _setup_application: MagicMock,
    ) -> None:
        """All message starts with /<project>s<session> project-name."""
        await send_all_projects_watcher_message(
            TEST_CHAT_ID,
            project_number=3,
            session_number=12,
            project_name="bloger",
            session_id=TEST_SESSION_ID,
            backend=BackendName.CLAUDE,
            text="Ответ из другого проекта",
            is_final=True,
        )

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert sent_text.startswith("/3s12 bloger")
        assert "Ответ из другого проекта" in sent_text
```

- [ ] **Step 7: Add `/projects` all line tests**

Update `TestHandleProjects.test_shows_all_projects` so it asserts `"/all all"` is present. Add this new test:

```python
    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    async def test_marks_all_mode_when_enabled(
        self,
        mock_is_all_projects: MagicMock,
    ) -> None:
        """Project list marks global all mode instead of marking a concrete project."""
        mock_is_all_projects.return_value = True
        projects = [_make_project_info("alpha", is_current=True)]
        update = _make_update()
        context = MagicMock()

        with patch.object(
            project_manager,
            "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ):
            await handle_projects(update, context)

        sent_text = bot_module._application.bot.send_message.call_args.args[1]
        lines = sent_text.splitlines()
        assert lines[0] == f"{PROJECT_CURRENT_MARKER} {ALL_PROJECTS_MODE_LINE}"
        assert lines[1] == "/p1 alpha"
```

- [ ] **Step 8: Add already-active project pending test**

Add this test to `TestHandleSwitchProject`:

```python
    @pytest.mark.asyncio()
    async def test_all_mode_same_project_collects_pending_messages(self) -> None:
        """Exiting all into the already active project still delivers all-mode messages."""
        projects = [_make_project_info("alpha", path="/fake/alpha", is_current=True)]
        update = _make_update(text="/p1")
        context = MagicMock()
        pending = [
            project_manager.PendingDeliveryItem(
                session_id="sess-alpha",
                backend=BackendName.CLAUDE,
                text="Ответ из all",
                is_final=True,
            )
        ]
        switch_result = project_manager.SwitchResult(
            success=True,
            already_active=True,
            old_path="/fake/alpha",
            new_path="/fake/alpha",
            pending_messages_count=0,
            pending_messages=[],
            error_message="",
        )

        with patch.object(
            all_projects_monitor,
            "disable_for_chat",
            return_value=True,
        ), patch.object(
            all_projects_monitor,
            "has_enabled_chats",
            return_value=False,
        ), patch.object(
            session_watcher,
            "resume_all",
        ), patch.object(
            project_manager,
            "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager,
            "switch_project",
            new=AsyncMock(return_value=switch_result),
        ), patch.object(
            project_manager,
            "collect_pending_messages_for_project",
            new=AsyncMock(return_value=(1, pending)),
        ) as collect_mock, patch.object(
            bot_module,
            "_deliver_pending_messages",
            new=AsyncMock(),
        ) as deliver_mock:
            await handle_switch_project(update, context)

        collect_mock.assert_awaited_once_with("/fake/alpha")
        deliver_mock.assert_awaited_once_with(TEST_CHAT_ID, pending)
        sent = bot_module._application.bot.send_message.call_args.args[1]
        assert "Переключено на проект" in sent
        assert "Непрочитанных сообщений: 1" in sent
```

- [ ] **Step 9: Add `/<project>s<session>` switch test**

Add a new class after `TestHandleSwitchProject`:

```python
class TestHandleSwitchProjectSession:
    """Tests for clickable /<project>s<session> commands from all mode."""

    @pytest.mark.asyncio()
    async def test_switches_project_and_binds_session_from_all_link(self) -> None:
        """All-mode command switches project and binds the exact linked session."""
        target = all_projects_monitor.AllProjectSessionLink(
            project_number=2,
            session_number=9,
            project_name="beta",
            project_path="/fake/beta",
            session_id="sess-beta",
            backend=BackendName.CODEX,
        )
        projects = [
            _make_project_info("alpha"),
            _make_project_info("beta", path="/fake/beta"),
        ]
        update = _make_update(text="/2s9")
        context = MagicMock()
        switch_result = project_manager.SwitchResult(
            success=True,
            already_active=False,
            old_path="/fake/alpha",
            new_path="/fake/beta",
            pending_messages_count=0,
            pending_messages=[],
            error_message="",
        )

        with patch.object(
            all_projects_monitor,
            "resolve_link",
            return_value=target,
        ), patch.object(
            all_projects_monitor,
            "disable_for_chat",
            return_value=True,
        ) as disable_mock, patch.object(
            project_manager,
            "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager,
            "switch_project",
            new=AsyncMock(return_value=switch_result),
        ) as switch_mock, patch.object(
            session_manager,
            "set_active_session",
            new=AsyncMock(return_value=9),
        ) as set_active_mock:
            await handle_switch_project_session(update, context)

        disable_mock.assert_called_once_with(TEST_CHAT_ID)
        switch_mock.assert_awaited_once_with("/fake/beta")
        set_active_mock.assert_awaited_once_with(
            TEST_CHAT_ID,
            "sess-beta",
            BackendName.CODEX,
        )
        sent_text = bot_module._application.bot.send_message.call_args.args[1]
        assert "beta" in sent_text
        assert "#9" in sent_text
```

- [ ] **Step 10: Run bot tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py -q
```

Expected: FAIL because `bot.py` does not yet import or implement the new all-project monitor integration.

---

### Task 5: Bot Integration

**Files:**
- Modify: `src/claude_manager/bot.py`
- Test: `tests/test_bot.py`

- [ ] **Step 1: Add imports and constants**

In `src/claude_manager/bot.py`:

- Add `import re`.
- Add `all_projects_monitor` to the `from claude_manager import (...)` import list.
- Change the `/all` command description in `BOT_COMMANDS` to `"Мониторинг всех проектов"`.
- Add these constants after project switch message templates:

```python
ALL_PROJECTS_MODE_LINE = "/all all"
ALL_PROJECTS_MODE_ENABLED_MESSAGE = (
    "Режим all включён: показываю сообщения из всех проектов.\n"
    "Писать агенту отсюда нельзя — сначала выберите проект и сессию."
)
ALL_PROJECTS_MODE_INPUT_WARNING = (
    "Вы в режиме all по всем проектам. Чтобы писать агенту, сначала войдите "
    "в проект и сессию: выберите проект через /projects или нажмите команду "
    "вида /1s2 в сообщении all."
)
PROJECT_SESSION_COMMAND_PATTERN = re.compile(
    r"^/(?P<project>\d+)s(?P<session>\d+)$"
)
```

- [ ] **Step 2: Add helper functions**

Add these helpers near the existing formatting helpers:

```python
def _monitoring_mode_message_for_chat(chat_id: int) -> str:
    """Return the right warning for local monitoring or global all mode."""
    if all_projects_monitor.is_enabled_for_chat(chat_id):
        return ALL_PROJECTS_MODE_INPUT_WARNING
    return claude_interaction.MONITORING_MODE_MESSAGE


def _parse_project_session_command(raw_text: str) -> tuple[int, int] | None:
    """Parse an all-mode command like /3s12."""
    match = PROJECT_SESSION_COMMAND_PATTERN.match(raw_text)
    if match is None:
        return None
    return int(match.group("project")), int(match.group("session"))
```

- [ ] **Step 3: Add all-mode message sender**

Add `send_all_projects_watcher_message(...)` after `send_watcher_message(...)`:

```python
async def send_all_projects_watcher_message(
    chat_id: int,
    *,
    project_number: int,
    session_number: int,
    project_name: str,
    session_id: str,
    backend: BackendName,
    text: str,
    is_final: bool,
) -> None:
    """Send a watcher message from global all-project mode."""
    del session_id

    if not is_final and silence_mode_registry.is_enabled():
        return

    if is_final:
        text = await file_delivery.process_file_markers(_application.bot, chat_id, text)
        text = await file_delivery.process_show_file_markers(_application.bot, chat_id, text)

    parts = message_splitter.prepare_message(text)
    if not is_final:
        parts = [f"<i>{part}</i>" for part in parts]

    status_icon = "\u2705" if is_final else "\u23f3"
    backend_label = _get_backend_display_name(backend)
    header = (
        f"/{project_number}s{session_number} "
        f"{project_name} {backend_label} {status_icon} "
    )
    parts[0] = header + parts[0]

    for part in parts:
        await telegram_sender.send_telegram_message(_application.bot, chat_id, part)
```

- [ ] **Step 4: Start all-project monitor in `post_init`**

After the existing `session_watcher.start(...)` task, add:

```python
    asyncio.create_task(
        all_projects_monitor.start(_all_projects_watcher_callback)
    )
```

Add this callback near `_watcher_callback`:

```python
async def _all_projects_watcher_callback(
    chat_id: int,
    project_number: int,
    session_number: int,
    project_name: str,
    session_id: str,
    backend: BackendName,
    text: str,
    is_final: bool,
) -> None:
    """Callback for the global all-project monitor."""
    await send_all_projects_watcher_message(
        chat_id,
        project_number=project_number,
        session_number=session_number,
        project_name=project_name,
        session_id=session_id,
        backend=backend,
        text=text,
        is_final=is_final,
    )
```

- [ ] **Step 5: Block `/new` in all-project mode**

In `handle_new(...)`, after `chat_id = update.effective_chat.id`, add:

```python
    if all_projects_monitor.is_enabled_for_chat(chat_id):
        await telegram_sender.send_telegram_message(
            _application.bot,
            chat_id,
            ALL_PROJECTS_MODE_INPUT_WARNING,
            parse_mode=None,
        )
        return
```

- [ ] **Step 6: Change `/all` to enable global monitor**

Replace `handle_all(...)` body after `chat_id` calculation with:

```python
    await session_manager.unbind_session(chat_id)
    await all_projects_monitor.enable_for_chat(chat_id)
    await telegram_sender.send_telegram_message(
        _application.bot,
        chat_id,
        ALL_PROJECTS_MODE_ENABLED_MESSAGE,
        parse_mode=None,
    )
```

- [ ] **Step 7: Render `/all all` in `/projects`**

Change `_format_project_line(...)` signature to:

```python
def _format_project_line(
    project: project_manager.ProjectInfo,
    number: int,
    *,
    suppress_current_marker: bool = False,
) -> str:
```

Inside it, compute marker like this:

```python
    marker = (
        PROJECT_CURRENT_MARKER + " "
        if project.is_current and not suppress_current_marker
        else ""
    )
```

In `handle_projects(...)`, replace `lines = [...]` with:

```python
    all_mode_enabled = all_projects_monitor.is_enabled_for_chat(chat_id)
    all_marker = PROJECT_CURRENT_MARKER + " " if all_mode_enabled else ""
    lines = [f"{all_marker}{ALL_PROJECTS_MODE_LINE}"]
    lines.extend(
        _format_project_line(
            project,
            number,
            suppress_current_marker=all_mode_enabled,
        )
        for number, project in enumerate(projects, start=1)
    )
```

- [ ] **Step 8: Add all-mode same-project pending helper**

Add this helper before `handle_switch_project(...)`:

```python
async def _include_pending_for_all_mode_same_project(
    result: project_manager.SwitchResult,
    target_project: project_manager.ProjectInfo,
    was_all_projects_mode: bool,
) -> project_manager.SwitchResult:
    """Collect pending messages when all mode exits into the already active project."""
    if not was_all_projects_mode or not result.success or not result.already_active:
        return result

    pending_count, pending_messages = await project_manager.collect_pending_messages_for_project(
        target_project.absolute_path
    )
    return project_manager.SwitchResult(
        success=True,
        already_active=False,
        old_path=result.old_path,
        new_path=result.new_path,
        pending_messages_count=pending_count,
        pending_messages=pending_messages,
        error_message=result.error_message,
    )
```

- [ ] **Step 9: Update `/pN` project switching**

In `handle_switch_project(...)`:

- Call `was_all_projects_mode = all_projects_monitor.disable_for_chat(chat_id)` after `chat_id`.
- Wrap switching in `try/finally`.
- After `project_manager.switch_project(...)`, call `_include_pending_for_all_mode_same_project(...)`.
- In `finally`, call `session_watcher.resume_all()` only when `was_all_projects_mode` is true and `not all_projects_monitor.has_enabled_chats()`.

Keep invalid project handling clear. If the number is invalid, send `INVALID_PROJECT_NUMBER_TEMPLATE` and let the `finally` resume the normal watcher.

- [ ] **Step 10: Add `/<project>s<session>` handler**

Add `handle_switch_project_session(...)` before `handle_switch_session(...)`.

Required behavior:

- Parse `/<project_number>s<session_number>`.
- Resolve `all_projects_monitor.resolve_link(...)`.
- Disable all-project mode for the chat.
- Resolve project number through `_resolve_project_by_number(...)`.
- If link registry has a target, use its exact `project_path`, `session_id`, `backend`, and `project_name`.
- Call `project_manager.switch_project(...)`.
- If the target project was already active and all mode was enabled, call `_include_pending_for_all_mode_same_project(...)`.
- If link registry had a target, call `session_manager.set_active_session(chat_id, link_target.session_id, link_target.backend)`.
- If link registry did not have a target, call `session_manager.switch_to_session(chat_id, session_number)` after switching project.
- If session is missing, send `Сессия #{session_number} не найдена в проекте {target_project.name}`.
- On success, send:

```python
response_text = (
    f"Переключено на проект: {target_project.name}\n"
    f"Подключён к сессии #{bound_number} ({display_name})"
)
```

- Append `PROJECT_SWITCH_PENDING_TEMPLATE` when visible pending messages exist.
- Deliver pending messages through `_deliver_pending_messages(...)`.
- Resume normal watcher in `finally` when leaving all mode and no all-mode chats remain.

- [ ] **Step 11: Use all-mode warning for text, photo, and document**

Replace these three uses:

```python
chat_id, claude_interaction.MONITORING_MODE_MESSAGE, parse_mode=None
```

with:

```python
chat_id, _monitoring_mode_message_for_chat(chat_id), parse_mode=None
```

Do this in:

- `handle_message(...)`
- `handle_photo(...)`
- `handle_document(...)`

- [ ] **Step 12: Register all-mode command handler**

In `_register_handlers(...)`, register the project-session command before `^/\d+$` and before `/pN`:

```python
    application.add_handler(
        MessageHandler(
            filters.Regex(r"^/\d+s\d+$"),
            handle_switch_project_session,
        )
    )
```

- [ ] **Step 13: Run bot tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py -q
```

Expected: PASS.

- [ ] **Step 14: Commit bot integration**

Run:

```bash
git add src/claude_manager/bot.py tests/test_bot.py
git commit -m "feat: wire all projects monitoring into bot"
```

Expected: commit contains only bot integration and bot tests.

---

### Task 6: Targeted Regression Suite

**Files:**
- Modify: none
- Test: targeted suite

- [ ] **Step 1: Run all affected unit tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_all_projects_monitor.py tests/test_bot.py tests/test_project_manager.py tests/test_unread_buffer.py tests/test_session_watcher.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full unit suite**

Run:

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS. If E2E tests are included and require credentials, run unit tests first and then run documented E2E checks separately.

- [ ] **Step 3: Inspect git diff**

Run:

```bash
git status --short
git diff --stat
git diff -- src/claude_manager/all_projects_monitor.py src/claude_manager/bot.py src/claude_manager/project_manager.py tests/test_all_projects_monitor.py tests/test_bot.py tests/test_project_manager.py
```

Expected:
- Diffs are limited to the all-project monitoring feature.
- Existing unrelated dirty files remain untouched:
  - `dev/docs/docs-index.md`
  - `dev/docs/session-reports/13-05/14-53_restart-active-child-sessions-bug.md`
- No `.claude/**` or `.agents/**` files changed.

- [ ] **Step 4: Commit final fixes if needed**

If Task 6 required fixes after previous commits, commit them:

```bash
git add src/claude_manager/all_projects_monitor.py src/claude_manager/bot.py src/claude_manager/project_manager.py tests/test_all_projects_monitor.py tests/test_bot.py tests/test_project_manager.py
git commit -m "fix: stabilize all projects monitoring behavior"
```

Expected: no commit is created if there are no remaining changes after earlier task commits.

---

### Task 7: Optional Live Smoke Test

**Files:**
- Modify: none
- Test: local bot behavior

- [ ] **Step 1: Restart through the documented script only when the user approves**

The project warns that restarts should use the documented script with preflight and post-flight checks. Ask the user before restarting the local Telegram bot.

If approved, run:

```bash
./restart-claude-manager.sh
```

Expected: restart preflight and post-flight succeed.

- [ ] **Step 2: Manual Telegram smoke path**

From Telegram:

```text
/projects
/all
```

Expected:
- `/projects` shows `/all all` as the first line.
- `/all` confirms global all mode.
- Sending ordinary text in all mode returns a warning that says to enter a concrete project and session.
- A message detected from another project starts with a command like `/3s12 bloger`.
- Clicking `/3s12` switches to project `3`, binds session `12` or the exact link target, and then replays pending messages.

Do not mark live smoke as failed if no background project has new assistant messages during the test window. Record it as "manual path available, no new all-mode message observed".

---

## Self-Review Checklist

- [ ] **Spec coverage:** Every requirement in `docs/superpowers/specs/2026-05-14-all-projects-monitoring-design.md` maps to a task:
  - `/projects` includes `/all all`: Task 4 Step 7, Task 5 Step 7.
  - all mode sees all projects: Task 1, Task 2.
  - all-mode prefix is `/<project_number>s<session_number> <project_name>`: Task 4 Step 6, Task 5 Step 3.
  - clicking the prefix switches project and session: Task 4 Step 9, Task 5 Step 10.
  - all-mode delivery does not mark target project read: Task 1 Step 1, Task 2 unread snapshot guard.
  - text, photos, documents, `/new` blocked in all mode: Task 4 Steps 2, 4, 5 and Task 5 Steps 5, 11.
  - same active project still collects pending: Task 3, Task 4 Step 8, Task 5 Step 8.
  - scanning errors are isolated: Task 1 `test_poll_continues_when_one_backend_scan_fails`, Task 2 scan error handling.
- [ ] **Placeholder scan:** Search this plan for unfinished-work markers from the Writing Plans skill. Expected: no matches.
- [ ] **Type consistency:** Confirm all new callback and link types use `BackendName`, not raw strings.
- [ ] **Handler ordering:** Confirm `/<project>s<session>` is registered before `/N`, `/pN`, and generic text handlers.
- [ ] **Unread semantics:** Confirm all-project monitor writes `unread_buffer` before callback delivery and advances only `_states`.
- [ ] **Watcher resume:** Confirm `enable_for_chat` resumes normal watcher on failed entry, and bot resumes normal watcher after leaving all mode.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-14-all-projects-monitoring-implementation.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution - execute tasks in this session using `executing-plans`, with checkpoints after each task.

Ask the user which execution mode to use before touching production code.
