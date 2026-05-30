# Reply Anchor v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Telegram responses from Claude/Codex should be sent as replies to the Telegram message that was actually accepted into the matching project/backend/session.

**Architecture:** Add a small in-memory `reply_anchor_registry` keyed by `project_path + backend + session_id`. The Telegram transport passes candidate `message_id` values inward, `claude_interaction` records an anchor only for accepted turns, and delivery paths read the anchor when sending direct, watcher, all-mode, and pending messages.

**Tech Stack:** Python 3.13, python-telegram-bot, pytest, pytest-asyncio, Telethon E2E tests.

---

## Source Spec

Implementation follows `dev/docs/specs/30.05_23.56-reply-anchor-v1-spec.md`.

Key terms:

- **Reply anchor:** Telegram `message_id` of Ivan's incoming message that started a real CLI-agent turn.
- **Accepted turn:** a message that passed command, monitoring-mode, and busy checks and entered `process_manager.send_message`.
- **Watcher:** the background poller that reads session files and delivers new assistant messages.
- **Pending delivery:** messages collected while a project was inactive and delivered after switching back.

## Size Gate Before Code

The project has an active rule: before editing code, count file lines and public functions. Several required files are already above thresholds:

- `src/claude_manager/bot.py`: 1459 lines, 21 public functions. This is above 500 lines and above 10 public functions.
- `src/claude_manager/claude_interaction.py`: 543 lines, 11 public functions. This is above 500 lines and above 10 public functions.
- `src/claude_manager/all_projects_monitor.py`: 632 lines. This is above 500 lines.
- `src/claude_manager/media_group_handler.py`: 376 lines. This is above 300 lines.

Do not silently add code to those files. Before implementation, Ivan must choose one of these paths:

1. **Refactor-first path:** extract delivery and input-anchor helpers into smaller modules, then implement reply anchors.
2. **Explicit narrow-edit consent:** make only minimal adapter changes in the oversized files for this feature, record the threshold breach in the final report, and schedule splitting separately.

Recommended path for this feature: option 2, because reply anchors require small wiring changes across existing handlers. Add most new logic to small new modules and keep oversized-file edits mechanical and narrow.

## File Map

- Create `src/claude_manager/reply_anchor_registry.py`: in-memory registry with set/get/clear/move operations.
- Create `tests/test_reply_anchor_registry.py`: focused registry unit tests.
- Modify `src/claude_manager/telegram_sender.py`: optional reply parameter and fallback without reply when Telegram rejects reply metadata.
- Modify `tests/test_telegram_sender.py`: reply pass-through, fallback, and first-chunk behavior support.
- Modify `src/claude_manager/claude_interaction.py`: accept incoming anchor candidate, set/move/read anchors for direct final/progress/retry delivery.
- Modify `tests/test_claude_interaction.py`: accepted-turn and busy/monitoring behavior.
- Modify `src/claude_manager/bot.py`: pass incoming `message_id`, resolve anchors for watcher/all/pending, and reply only on first chunk.
- Modify `tests/test_bot.py`: text/photo/document command behavior and delivery reply lookup.
- Modify `src/claude_manager/media_group_handler.py`: choose album anchor as caption message first, otherwise first album message.
- Modify tests touching media groups, likely `tests/test_media_group_handler.py` if present or `tests/test_bot.py` if media tests live there.
- Modify `src/claude_manager/all_projects_monitor.py`: include `project_path` in all-mode callback so bot can resolve anchors for the original project.
- Modify tests for all-mode monitor callbacks.
- Modify `tests/e2e/test_client.py`: return sent Telegram message objects and wait for bot message objects.
- Create `tests/e2e/test_reply_anchor.py`: mandatory real Telegram scenarios.

## Task 0: Confirm Workspace And Size Decision

**Files:**
- Read: `CLAUDE.md`
- Read: `dev/docs/specs/30.05_23.56-reply-anchor-v1-spec.md`
- Read: `docs/superpowers/plans/2026-05-30-reply-anchor-v1-implementation.md`

- [ ] **Step 1: Re-check git state**

Run:

```bash
git status --short
git branch --show-current
```

Expected: branch is not `main` or `master`; unrelated dirty files are identified and left untouched.

- [ ] **Step 2: Re-check required file sizes**

Run:

```bash
wc -l src/claude_manager/bot.py src/claude_manager/claude_interaction.py src/claude_manager/all_projects_monitor.py src/claude_manager/media_group_handler.py
for f in src/claude_manager/bot.py src/claude_manager/claude_interaction.py src/claude_manager/all_projects_monitor.py src/claude_manager/media_group_handler.py; do
  printf '%s ' "$f"
  grep -Ec '^(async )?def [A-Za-z][A-Za-z0-9_]*\(' "$f"
done
```

Expected: report the current threshold breaches before editing. If Ivan has not approved narrow edits, stop and ask.

- [ ] **Step 3: Decide worktree**

Run:

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" && pwd -P)
git rev-parse --show-superproject-working-tree 2>/dev/null
printf 'git_dir=%s\ngit_common=%s\n' "$GIT_DIR" "$GIT_COMMON"
```

Expected: if `GIT_DIR == GIT_COMMON`, ask whether to create an isolated worktree. If using `.worktrees/`, first ensure it is ignored or add an ignore entry with Ivan's consent.

## Task 1: Reply Anchor Registry

**Files:**
- Create: `src/claude_manager/reply_anchor_registry.py`
- Create: `tests/test_reply_anchor_registry.py`

- [ ] **Step 1: Write failing registry tests**

Create `tests/test_reply_anchor_registry.py`:

```python
"""Tests for in-memory Telegram reply anchors."""

from claude_manager import reply_anchor_registry
from claude_manager.coding_agent_backend import BackendName


PROJECT_A = "/tmp/project-a"
PROJECT_B = "/tmp/project-b"
SESSION_ID = "session-1"


def setup_function() -> None:
    """Clear registry state between tests."""
    reply_anchor_registry.clear_all()


def test_set_and_get_anchor_for_project_backend_session() -> None:
    reply_anchor_registry.set_anchor(
        PROJECT_A,
        BackendName.CLAUDE,
        SESSION_ID,
        101,
    )

    assert (
        reply_anchor_registry.get_anchor(
            PROJECT_A,
            BackendName.CLAUDE,
            SESSION_ID,
        )
        == 101
    )


def test_anchor_keys_do_not_mix_projects_or_backends() -> None:
    reply_anchor_registry.set_anchor(
        PROJECT_A,
        BackendName.CLAUDE,
        SESSION_ID,
        101,
    )
    reply_anchor_registry.set_anchor(
        PROJECT_B,
        BackendName.CODEX,
        SESSION_ID,
        202,
    )

    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CLAUDE, SESSION_ID) == 101
    assert reply_anchor_registry.get_anchor(PROJECT_B, BackendName.CODEX, SESSION_ID) == 202
    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CODEX, SESSION_ID) is None


def test_clear_anchor_removes_only_matching_key() -> None:
    reply_anchor_registry.set_anchor(PROJECT_A, BackendName.CLAUDE, SESSION_ID, 101)
    reply_anchor_registry.set_anchor(PROJECT_B, BackendName.CLAUDE, SESSION_ID, 202)

    reply_anchor_registry.clear_anchor(PROJECT_A, BackendName.CLAUDE, SESSION_ID)

    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CLAUDE, SESSION_ID) is None
    assert reply_anchor_registry.get_anchor(PROJECT_B, BackendName.CLAUDE, SESSION_ID) == 202


def test_move_anchor_transfers_temp_session_to_real_session() -> None:
    reply_anchor_registry.set_anchor(
        PROJECT_A,
        BackendName.CODEX,
        "_new_123",
        303,
    )

    reply_anchor_registry.move_anchor(
        PROJECT_A,
        BackendName.CODEX,
        "_new_123",
        "real-session",
    )

    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CODEX, "_new_123") is None
    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CODEX, "real-session") == 303
```

- [ ] **Step 2: Run red test**

Run:

```bash
python -m pytest tests/test_reply_anchor_registry.py -v
```

Expected: FAIL because `claude_manager.reply_anchor_registry` does not exist.

- [ ] **Step 3: Implement registry**

Create `src/claude_manager/reply_anchor_registry.py`:

```python
"""In-memory Telegram reply anchor registry."""

from dataclasses import dataclass
from pathlib import Path

from claude_manager.coding_agent_backend import BackendName


@dataclass(frozen=True)
class ReplyAnchorKey:
    """Stable key for one project/backend/session reply anchor."""

    project_path: str
    backend: BackendName
    session_id: str


_anchors: dict[ReplyAnchorKey, int] = {}


def _normalize_project_path(project_path: str) -> str:
    """Return a stable absolute project path string."""
    return str(Path(project_path).expanduser().resolve())


def _key(
    project_path: str,
    backend: BackendName,
    session_id: str,
) -> ReplyAnchorKey:
    """Build a normalized registry key."""
    return ReplyAnchorKey(_normalize_project_path(project_path), backend, session_id)


def set_anchor(
    project_path: str,
    backend: BackendName,
    session_id: str,
    message_id: int,
) -> None:
    """Store the active Telegram reply anchor for a session."""
    _anchors[_key(project_path, backend, session_id)] = message_id


def get_anchor(
    project_path: str,
    backend: BackendName,
    session_id: str,
) -> int | None:
    """Return the active Telegram reply anchor for a session."""
    return _anchors.get(_key(project_path, backend, session_id))


def clear_anchor(
    project_path: str,
    backend: BackendName,
    session_id: str,
) -> None:
    """Remove the active Telegram reply anchor for a session."""
    _anchors.pop(_key(project_path, backend, session_id), None)


def move_anchor(
    project_path: str,
    backend: BackendName,
    old_session_id: str,
    new_session_id: str,
) -> None:
    """Move a reply anchor when a temporary session id becomes real."""
    old_key = _key(project_path, backend, old_session_id)
    anchor = _anchors.pop(old_key, None)
    if anchor is not None:
        _anchors[_key(project_path, backend, new_session_id)] = anchor


def clear_all() -> None:
    """Clear all reply anchors."""
    _anchors.clear()
```

- [ ] **Step 4: Run green test**

Run:

```bash
python -m pytest tests/test_reply_anchor_registry.py -v
```

Expected: PASS.

## Task 2: Telegram Sender Reply Support

**Files:**
- Modify: `src/claude_manager/telegram_sender.py`
- Modify: `tests/test_telegram_sender.py`

- [ ] **Step 1: Write failing sender tests**

Append tests:

```python
    @pytest.mark.asyncio()
    async def test_reply_to_message_id_passed_as_reply_parameters(
        self, mock_bot: MagicMock,
    ) -> None:
        """reply_to_message_id becomes Telegram reply_parameters."""
        await send_telegram_message(
            mock_bot,
            TEST_CHAT_ID,
            "reply text",
            reply_to_message_id=777,
        )

        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["reply_parameters"].message_id == 777
        assert call_kwargs["reply_parameters"].allow_sending_without_reply is True

    @pytest.mark.asyncio()
    async def test_reply_bad_request_retries_without_reply(
        self, mock_bot: MagicMock,
    ) -> None:
        """If Telegram rejects reply metadata, the message is resent without reply."""
        mock_bot.send_message = AsyncMock(
            side_effect=[
                BadRequest("Message to be replied not found"),
                MagicMock(),
            ],
        )

        await send_telegram_message(
            mock_bot,
            TEST_CHAT_ID,
            "reply text",
            reply_to_message_id=777,
        )

        assert mock_bot.send_message.call_count == 2
        first_kwargs = mock_bot.send_message.call_args_list[0][1]
        second_kwargs = mock_bot.send_message.call_args_list[1][1]
        assert first_kwargs["reply_parameters"].message_id == 777
        assert "reply_parameters" not in second_kwargs
```

- [ ] **Step 2: Run red tests**

Run:

```bash
python -m pytest tests/test_telegram_sender.py::TestSendTelegramMessageExtended -v
```

Expected: FAIL because `reply_to_message_id` is not accepted.

- [ ] **Step 3: Implement reply parameter support**

Update `telegram_sender.py` to:

```python
from telegram import Bot, ReplyParameters
```

Add helpers:

```python
def _build_reply_parameters(reply_to_message_id: int | None) -> ReplyParameters | None:
    """Build Telegram reply parameters for an optional anchor."""
    if reply_to_message_id is None:
        return None
    return ReplyParameters(
        message_id=reply_to_message_id,
        allow_sending_without_reply=True,
    )


def _bad_request_is_reply_related(error: BadRequest) -> bool:
    """Return whether BadRequest likely came from invalid reply metadata."""
    text = str(error).lower()
    return "reply" in text or "replied" in text
```

Extend `send_raw`, `fallback_to_plain_text`, `handle_retry_after`, and `send_telegram_message` with `reply_to_message_id: int | None = None`. `send_raw` should include `reply_parameters` only when the value is not `None`:

```python
reply_parameters = _build_reply_parameters(reply_to_message_id)
kwargs = {
    "parse_mode": parse_mode,
    "reply_markup": reply_markup,
}
if reply_parameters is not None:
    kwargs["reply_parameters"] = reply_parameters
await bot.send_message(chat_id, text, **kwargs)
```

In `send_telegram_message`, catch reply-related `BadRequest` first:

```python
except BadRequest as error:
    if reply_to_message_id is not None and _bad_request_is_reply_related(error):
        await send_raw(bot, chat_id, text, parse_mode, reply_markup, None)
        return
    if await fallback_to_plain_text(
        bot,
        chat_id,
        text,
        parse_mode,
        reply_markup,
        reply_to_message_id,
    ):
        return
    raise
```

- [ ] **Step 4: Run sender tests**

Run:

```bash
python -m pytest tests/test_telegram_sender.py -v
```

Expected: PASS.

## Task 3: Direct Response Reply Wiring

**Files:**
- Modify: `src/claude_manager/bot.py`
- Modify: `src/claude_manager/claude_interaction.py`
- Modify: `tests/test_bot.py`
- Modify: `tests/test_claude_interaction.py`

- [ ] **Step 1: Write failing first-chunk test**

Add a `send_response` test in `tests/test_bot.py`:

```python
@pytest.mark.asyncio()
@patch("claude_manager.bot.telegram_sender.send_telegram_message", new_callable=AsyncMock)
async def test_send_response_replies_only_first_chunk(
    mock_send: AsyncMock,
) -> None:
    """Long response uses reply only for the first Telegram chunk."""
    with patch.object(bot_module.message_splitter, "prepare_message", return_value=["one", "two"]):
        await send_response(
            TEST_CHAT_ID,
            "long",
            3,
            BackendName.CLAUDE,
            is_final=True,
            reply_to_message_id=555,
        )

    assert mock_send.await_args_list[0].kwargs["reply_to_message_id"] == 555
    assert mock_send.await_args_list[1].kwargs.get("reply_to_message_id") is None
```

- [ ] **Step 2: Run red test**

Run:

```bash
python -m pytest tests/test_bot.py::test_send_response_replies_only_first_chunk -v
```

Expected: FAIL because `send_response` has no `reply_to_message_id` parameter.

- [ ] **Step 3: Implement first-chunk reply parameter**

Update `send_response`, `send_watcher_message`, and `send_all_projects_watcher_message` signatures to accept:

```python
reply_to_message_id: int | None = None
```

In each send loop:

```python
part_reply_to_message_id = reply_to_message_id if index == 0 else None
await telegram_sender.send_telegram_message(
    _application.bot,
    chat_id,
    part,
    reply_markup=markup,
    reply_to_message_id=part_reply_to_message_id,
)
```

For loops that currently do not enumerate parts, switch to `for index, part in enumerate(parts):`.

- [ ] **Step 4: Write failing accepted-anchor tests**

Add tests in `tests/test_claude_interaction.py`:

```python
@pytest.mark.asyncio()
@patch.object(process_manager, "send_message", new_callable=AsyncMock)
@patch.object(session_manager, "get_active_session")
@patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
async def test_send_to_claude_sets_anchor_for_accepted_turn(
    mock_register: AsyncMock,
    mock_get_active: MagicMock,
    mock_send_message: AsyncMock,
) -> None:
    """Accepted user message becomes reply anchor for final response."""
    from claude_manager import reply_anchor_registry

    reply_anchor_registry.clear_all()
    mock_get_active.return_value = ActiveSession(TEST_SESSION_ID, BackendName.CLAUDE)
    mock_register.return_value = 7
    mock_send_message.return_value = SendResult(
        text="done",
        session_id=TEST_SESSION_ID,
        is_error=False,
        retries_used=0,
        backend=BackendName.CLAUDE,
    )

    with patch.object(ci_module.session_watcher, "pause_session"), \
         patch.object(ci_module.session_watcher, "resume_session", new_callable=AsyncMock), \
         patch.object(ci_module.session_watcher, "clear_handler_owns_final_delivery"), \
         patch.object(ci_module, "start_agent_silence_watchdog"), \
         patch.object(ci_module, "cancel_agent_silence_watchdog"), \
         patch("claude_manager.bot.send_response", new_callable=AsyncMock) as mock_response:
        await send_to_claude_and_respond(
            TEST_CHAT_ID,
            "hello",
            reply_to_message_id=444,
        )

    assert reply_anchor_registry.get_anchor(
        config_module.WORKING_DIR,
        BackendName.CLAUDE,
        TEST_SESSION_ID,
    ) == 444
    assert mock_response.await_args.kwargs["reply_to_message_id"] == 444
```

- [ ] **Step 5: Run red accepted-anchor test**

Run:

```bash
python -m pytest tests/test_claude_interaction.py::test_send_to_claude_sets_anchor_for_accepted_turn -v
```

Expected: FAIL because `send_to_claude_and_respond` has no `reply_to_message_id` parameter and does not use the registry.

- [ ] **Step 6: Implement direct anchor write/read/move**

Update `claude_interaction.py`:

```python
from claude_manager import reply_anchor_registry
```

Change:

```python
async def send_to_claude_and_respond(
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
) -> None:
```

Before `process_manager.send_message`, after active session and watcher setup:

```python
if reply_to_message_id is not None:
    reply_anchor_registry.set_anchor(
        original_project_path,
        backend,
        session_id,
        reply_to_message_id,
    )
```

In `_on_session_id_changed`, after successful backend/session validation:

```python
reply_anchor_registry.move_anchor(
    original_project_path,
    backend,
    old_id,
    new_id,
)
```

When calling `_get_send_response()` for progress and final:

```python
anchor_message_id = reply_anchor_registry.get_anchor(
    original_project_path,
    backend,
    session_id,
)
await _get_send_response()(
    chat_id,
    progress_text,
    day_number,
    backend,
    is_final=False,
    reply_to_message_id=anchor_message_id,
)
```

If `process_manager.ProcessManagerError` is caught, clear the anchor for the attempted session before sending the busy message. This prevents the visible post-call state from recording a rejected message.

- [ ] **Step 7: Run direct wiring tests**

Run:

```bash
python -m pytest tests/test_claude_interaction.py tests/test_bot.py -v
```

Expected: PASS for changed tests. Existing unrelated failures must be investigated before continuing.

## Task 4: Incoming Text, Photo, Document, And Album Anchor Candidates

**Files:**
- Modify: `src/claude_manager/bot.py`
- Modify: `src/claude_manager/media_group_handler.py`
- Modify: `tests/test_bot.py`
- Modify: media group tests if present

- [ ] **Step 1: Write failing text message test**

Add:

```python
@pytest.mark.asyncio()
@patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
async def test_handle_message_passes_incoming_message_id_as_anchor(
    mock_send: AsyncMock,
) -> None:
    """Text messages pass their Telegram message_id as anchor candidate."""
    update = _make_update("hello")
    update.message.message_id = 321
    context = _make_context()

    await handle_message(update, context)

    mock_send.assert_awaited_once_with(TEST_CHAT_ID, "hello", reply_to_message_id=321)
```

- [ ] **Step 2: Run red text test**

Run:

```bash
python -m pytest tests/test_bot.py::test_handle_message_passes_incoming_message_id_as_anchor -v
```

Expected: FAIL because `handle_message` calls `send_to_claude_and_respond(chat_id, text)`.

- [ ] **Step 3: Implement text candidate**

Update `handle_message`:

```python
await claude_interaction.send_to_claude_and_respond(
    chat_id,
    text,
    reply_to_message_id=update.message.message_id,
)
```

- [ ] **Step 4: Add photo and document tests**

Patch download and task builders, then assert `reply_to_message_id` is passed:

```python
@pytest.mark.asyncio()
@patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
@patch("claude_manager.bot.telegram_file_downloader.download_and_save_file", new_callable=AsyncMock)
async def test_handle_single_photo_passes_photo_message_id_as_anchor(
    mock_download: AsyncMock,
    mock_send: AsyncMock,
) -> None:
    mock_download.return_value = "/tmp/photo.jpg"
    update = _make_update()
    update.message.message_id = 456
    update.message.caption = "describe"
    context = _make_context()

    await _handle_single_photo(update, context)

    assert mock_send.await_args.kwargs["reply_to_message_id"] == 456
```

Add the same shape for `handle_document` with `message_id = 457`.

- [ ] **Step 5: Implement photo and document candidates**

Pass `reply_to_message_id=update.message.message_id` from `_handle_single_photo` and `handle_document`.

- [ ] **Step 6: Add album anchor selection tests**

Create or update media group tests:

```python
def test_select_album_anchor_prefers_caption_message() -> None:
    first = MagicMock()
    first.message.message_id = 10
    first.message.caption = None
    second = MagicMock()
    second.message.message_id = 11
    second.message.caption = "caption"

    assert media_group_handler.select_album_anchor_message_id([first, second]) == 11


def test_select_album_anchor_falls_back_to_first_message() -> None:
    first = MagicMock()
    first.message.message_id = 10
    first.message.caption = None
    second = MagicMock()
    second.message.message_id = 11
    second.message.caption = None

    assert media_group_handler.select_album_anchor_message_id([first, second]) == 10
```

- [ ] **Step 7: Implement album anchor selection**

Add to `media_group_handler.py`:

```python
def select_album_anchor_message_id(updates: list[Update]) -> int:
    """Choose the Telegram message_id used as the album reply anchor."""
    for update in updates:
        if update.message.caption:
            return update.message.message_id
    return updates[0].message.message_id
```

In `finalize_photo_group`, call:

```python
reply_to_message_id = select_album_anchor_message_id(updates)
await _send_to_claude_callback(
    chat_id,
    task_text,
    reply_to_message_id=reply_to_message_id,
)
```

Ensure the callback injected from `bot.setup_bot()` accepts the keyword.

- [ ] **Step 8: Run input-anchor tests**

Run:

```bash
python -m pytest tests/test_bot.py tests/test_media_group_handler.py -v
```

Expected: PASS. If `tests/test_media_group_handler.py` does not exist, run the specific file where media group tests were added.

## Task 5: Stop Command Clears Anchor

**Files:**
- Modify: `src/claude_manager/bot.py`
- Modify: `tests/test_bot.py`

- [ ] **Step 1: Write failing stop test**

Add:

```python
@pytest.mark.asyncio()
@patch.object(process_manager, "stop_process", new_callable=AsyncMock)
@patch.object(process_manager, "has_process", return_value=True)
@patch.object(process_manager, "is_busy", return_value=True)
@patch.object(session_manager, "get_active_session")
async def test_handle_stop_clears_reply_anchor(
    mock_get_active: MagicMock,
    mock_is_busy: MagicMock,
    mock_has_process: MagicMock,
    mock_stop: AsyncMock,
) -> None:
    """Stopping a turn removes the active reply anchor for that session."""
    from claude_manager import reply_anchor_registry

    reply_anchor_registry.clear_all()
    reply_anchor_registry.set_anchor(
        config_module.WORKING_DIR,
        BackendName.CLAUDE,
        TEST_SESSION_ID,
        123,
    )
    mock_get_active.return_value = ActiveSession(TEST_SESSION_ID, BackendName.CLAUDE)
    mock_stop.return_value = StopResult(True, False, BackendName.CLAUDE)

    await handle_stop(_make_update("/stop"), _make_context())

    assert reply_anchor_registry.get_anchor(
        config_module.WORKING_DIR,
        BackendName.CLAUDE,
        TEST_SESSION_ID,
    ) is None
```

- [ ] **Step 2: Run red stop test**

Run:

```bash
python -m pytest tests/test_bot.py::test_handle_stop_clears_reply_anchor -v
```

Expected: FAIL because `/stop` does not clear anchors.

- [ ] **Step 3: Implement stop clear**

In `handle_stop`, after resolving `session_id` and `backend`, clear anchor when a process is actually stopped:

```python
reply_anchor_registry.clear_anchor(config.WORKING_DIR, backend, session_id)
await process_manager.stop_process(session_id, backend)
```

Import `reply_anchor_registry` in `bot.py`.

- [ ] **Step 4: Run stop tests**

Run:

```bash
python -m pytest tests/test_bot.py::test_handle_stop_clears_reply_anchor tests/e2e/test_stop_command.py -v
```

Expected: unit test PASS. E2E may SKIP if Telethon credentials are unavailable.

## Task 6: Watcher, All-Mode, And Pending Delivery Anchor Lookup

**Files:**
- Modify: `src/claude_manager/bot.py`
- Modify: `src/claude_manager/all_projects_monitor.py`
- Modify: `src/claude_manager/project_pending_delivery.py` only if project path must be carried explicitly
- Modify: `tests/test_bot.py`
- Modify: all-project monitor tests

- [ ] **Step 1: Write failing watcher delivery test**

Add:

```python
@pytest.mark.asyncio()
@patch("claude_manager.bot.telegram_sender.send_telegram_message", new_callable=AsyncMock)
async def test_send_watcher_message_uses_existing_reply_anchor(
    mock_send: AsyncMock,
) -> None:
    """Watcher delivery reads anchor for project/backend/session without creating one."""
    from claude_manager import reply_anchor_registry

    reply_anchor_registry.clear_all()
    reply_anchor_registry.set_anchor(
        config_module.WORKING_DIR,
        BackendName.CLAUDE,
        TEST_SESSION_ID,
        909,
    )

    await send_watcher_message(
        TEST_CHAT_ID,
        "watcher text",
        TEST_SESSION_ID,
        BackendName.CLAUDE,
        4,
        False,
    )

    assert mock_send.await_args.kwargs["reply_to_message_id"] == 909
```

- [ ] **Step 2: Implement watcher lookup**

In `send_watcher_message`, before the send loop:

```python
reply_to_message_id = reply_anchor_registry.get_anchor(
    config.WORKING_DIR,
    backend,
    session_id,
)
```

Use it only for the first part.

- [ ] **Step 3: Write failing all-mode project path test**

Update the all-mode callback signature test so callback receives `project_path`:

```python
await callback(
    chat_id,
    project_session.project_number,
    project_session.session_number,
    project_session.project_name,
    project_session.project_path,
    file_info.session_id,
    backend_name,
    message.text,
    is_final,
)
```

Expected old code fails because callback receives no project path.

- [ ] **Step 4: Implement all-mode project path propagation**

Update `AllProjectsMessageCallback` type from 8 arguments to 9 arguments:

```python
AllProjectsMessageCallback = Callable[
    [int, int, int, str, str, str, BackendName, str, bool],
    Awaitable[None],
]
```

Update `_deliver_project_session_delta` callback call to include `project_session.project_path`.

Update `bot._all_projects_watcher_callback` and `send_all_projects_watcher_message` to accept `project_path`. Resolve anchor with:

```python
reply_to_message_id = reply_anchor_registry.get_anchor(
    project_path,
    backend,
    session_id,
)
```

- [ ] **Step 5: Add pending delivery lookup**

In `_deliver_pending_messages`, compute:

```python
reply_to_message_id = reply_anchor_registry.get_anchor(
    config.WORKING_DIR,
    backend,
    pending.session_id,
)
await send_response(
    chat_id,
    pending.text,
    day_number,
    backend,
    is_final=is_final,
    reply_to_message_id=reply_to_message_id,
)
```

- [ ] **Step 6: Run delivery tests**

Run:

```bash
python -m pytest tests/test_bot.py tests/test_all_projects_monitor.py tests/integration/test_project_switching.py -v
```

Expected: PASS. If the all-project test file has a different name, use `rg -n "AllProjectsMessageCallback|send_all_projects_watcher_message|_all_projects_watcher_callback" tests`.

## Task 7: E2E Client And Reply Anchor Scenarios

**Files:**
- Modify: `tests/e2e/test_client.py`
- Create: `tests/e2e/test_reply_anchor.py`

- [ ] **Step 1: Extend E2E client with sent and received message objects**

Change `send_message` to return the Telethon sent message while preserving old callers:

```python
async def send_message(self, text: str):
    """Send a text message to the bot and return the Telegram message."""
    self._reset_response_state()
    sent_message = None

    async def _do_send() -> None:
        nonlocal sent_message
        sent_message = await self._client.send_message(self._bot_username, text)

    await self._send_with_reconnect(_do_send, "send_message")
    logger.info("Отправлено сообщение боту: %s", text[:100])
    return sent_message
```

Add:

```python
async def wait_for_matching_response_message(
    self,
    match_text: str,
    timeout: int = DEFAULT_RESPONSE_TIMEOUT_SECONDS,
):
    """Wait for a bot response containing match_text and return the message object."""
```

Implementation should mirror `wait_for_matching_response`, but store response message objects in a parallel `_all_response_messages` list.

- [ ] **Step 2: Write E2E happy path**

Create `tests/e2e/test_reply_anchor.py`:

```python
"""E2E tests for Telegram reply anchors."""

import asyncio
import re

from tests.e2e.test_client import (
    TelegramTestClient,
    build_current_session_final_response_pattern,
)


CLAUDE_RESPONSE_TIMEOUT_SECONDS = 90
BOT_COMMAND_TIMEOUT_SECONDS = 15
PROCESS_STARTUP_SECONDS = 3
STOP_CLEANUP_SECONDS = 3


def _extract_session_number(response: str) -> str:
    match = re.search(r"#(\d+)", response)
    assert match, f"Не найден номер сессии (#N) в ответе: {response}"
    return match.group(1)


async def test_reply_anchor_happy_path(
    telegram_client: TelegramTestClient,
) -> None:
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    session_number = _extract_session_number(response)

    sent = await telegram_client.send_message("Ответь одним словом: якорь")
    bot_message = await telegram_client.wait_for_regex_response_message(
        build_current_session_final_response_pattern(session_number),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    assert bot_message.reply_to_msg_id == sent.id
```

- [ ] **Step 3: Write E2E busy scenario**

Add:

```python
async def test_busy_message_does_not_steal_reply_anchor(
    telegram_client: TelegramTestClient,
) -> None:
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    session_number = _extract_session_number(response)

    sent_a = await telegram_client.send_message(
        "Прочитай файл src/claude_manager/bot.py и ответь числом функций."
    )
    await asyncio.sleep(PROCESS_STARTUP_SECONDS)
    sent_b = await telegram_client.send_message("Это сообщение должно получить busy")
    busy = await telegram_client.wait_for_matching_response_message(
        "обрабатывает",
        timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )
    final_a = await telegram_client.wait_for_regex_response_message(
        build_current_session_final_response_pattern(session_number),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    assert busy.reply_to_msg_id in (None, sent_b.id)
    assert final_a.reply_to_msg_id == sent_a.id
```

The busy response itself is allowed to be plain or reply to B later only if Ivan explicitly asks for command/busy replies. It must not alter A's final anchor.

- [ ] **Step 4: Write E2E stop scenario**

Add:

```python
async def test_new_request_after_stop_uses_new_reply_anchor(
    telegram_client: TelegramTestClient,
) -> None:
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    session_number = _extract_session_number(response)

    sent_a = await telegram_client.send_message(
        "Прочитай файл src/claude_manager/config.py и перечисли переменные окружения."
    )
    await asyncio.sleep(PROCESS_STARTUP_SECONDS)
    await telegram_client.send_command("/stop")
    await telegram_client.wait_for_matching_response(
        "остановлен",
        timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )
    await asyncio.sleep(STOP_CLEANUP_SECONDS)

    sent_b = await telegram_client.send_message("Скажи одним словом: после")
    final_b = await telegram_client.wait_for_regex_response_message(
        build_current_session_final_response_pattern(session_number),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    assert sent_a.id != sent_b.id
    assert final_b.reply_to_msg_id == sent_b.id
```

- [ ] **Step 5: Run E2E reply-anchor tests**

Run:

```bash
python -m pytest tests/e2e/test_reply_anchor.py -v
```

Expected: PASS when Telethon credentials and the bot service are available; SKIP only if E2E environment variables or Telethon session are missing.

## Task 8: Full Verification

**Files:**
- Verify all changed files

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
python -m pytest \
  tests/test_reply_anchor_registry.py \
  tests/test_telegram_sender.py \
  tests/test_claude_interaction.py \
  tests/test_bot.py \
  tests/test_all_projects_monitor.py \
  tests/integration/test_project_switching.py \
  -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
python -m pytest tests/ -v
```

Expected: PASS or E2E SKIP only for missing external Telegram credentials.

- [ ] **Step 3: Re-check code file sizes**

Run:

```bash
wc -l src/claude_manager/reply_anchor_registry.py src/claude_manager/telegram_sender.py src/claude_manager/bot.py src/claude_manager/claude_interaction.py src/claude_manager/media_group_handler.py src/claude_manager/all_projects_monitor.py
for f in src/claude_manager/reply_anchor_registry.py src/claude_manager/telegram_sender.py src/claude_manager/bot.py src/claude_manager/claude_interaction.py src/claude_manager/media_group_handler.py src/claude_manager/all_projects_monitor.py; do
  printf '%s ' "$f"
  grep -Ec '^(async )?def [A-Za-z][A-Za-z0-9_]*\(' "$f"
done
```

Expected: explicitly report any file still above 300, above 500, or above 10 public functions.

- [ ] **Step 4: Acceptance checklist**

Verify each item from the spec:

- Accepted agent responses reply to the last accepted Telegram message for the same project/backend/session.
- Busy, monitoring mode, and commands do not create anchors.
- `/stop` clears the stopped turn anchor.
- Project and session switching do not mix anchors.
- Only the first chunk of a long response carries reply metadata.
- Telegram reply errors retry without reply.
- After bot restart, old requests deliver without reply because the registry is in memory.
- Unit tests and required E2E scenarios pass or E2E is skipped only because the external environment is unavailable.

## Notes For The Implementer

- Keep new logic out of `bot.py` where possible. `bot.py` is already a god-module: it has too many responsibilities and too many public functions.
- Do not change `.claude/**` or generated `.agents/**` mirrors.
- Do not restart the bot via `restart-claude-manager.sh` from inside the bot subprocess tree.
- Do not claim tests pass until fresh verification output has been read.
