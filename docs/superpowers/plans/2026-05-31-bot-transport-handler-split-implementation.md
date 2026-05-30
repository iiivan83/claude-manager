# Bot Transport Handler Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `src/claude_manager/bot.py` from a large Telegram transport god-module into a thin application facade without changing Telegram bot behavior.

**Architecture:** `bot.py` keeps application assembly, handler registration, global error handling, access checking, callback injection, and compatibility re-exports. User-facing Telegram behavior moves into domain handler modules: agent, session, input, and lifecycle. The active `process_manager.py` refactor remains separate and this plan does not change its public contract.

**Tech Stack:** Python 3.13, python-telegram-bot, asyncio, pytest, pytest-asyncio.

---

## Beginner Context

This part of the project is the Telegram front door for the local Claude Manager bot. Ivan sends a Telegram command or message, `bot.py` receives it, checks access, and routes the request to lower modules such as `session_manager`, `claude_interaction`, and `process_manager`.

Key terms for this plan:

- **Facade:** a thin public entry point. Here it means `bot.py` still exposes the old names, but no longer owns the detailed command logic.
- **Handler:** a Telegram callback function such as `handle_message` or `handle_stop`.
- **Gate:** a test command that must pass before the refactor can continue.
- **Checkpoint:** a clean saved state of another refactor, usually a commit or an explicitly preserved separate worktree state.

The practical consequence: this refactor should make `bot.py` easier to review and safer to change, but it must not run on top of unfinished `process_manager.py` edits in the same checkout.

## Source Inputs

- Spec: `docs/superpowers/specs/2026-05-31-bot-transport-handler-split-design.md`
- Active large-file plan to keep separate: `docs/superpowers/plans/2026-05-31-process-manager-state-split-implementation.md`
- Project rules: `CLAUDE.md`

## Coordination With Active `process_manager.py` Refactor

`process_manager.py` is currently the largest code file at 1188 lines. This plan is for the second large split, `bot.py`, and must not compete with the first one.

Execution rule:

- If `src/claude_manager/process_manager.py` or a new `src/claude_manager/process_state.py` has tracked or staged changes in the current checkout, stop before code edits.
- Continue only after the process-manager refactor has a checkpoint, or after creating an isolated worktree for the bot split.
- Do not edit `process_manager.py`, `process_state.py`, or their API in this plan.

## File Structure

- Create: `src/claude_manager/telegram_agent_handlers.py`
  Owns `/agent`, backend keyboard creation, callback parsing, and backend switch confirmation.
- Create: `src/claude_manager/telegram_session_handlers.py`
  Owns `/new`, `/sessions`, `/stop`, `/all`, and numeric `/N` session switching.
- Create: `src/claude_manager/telegram_input_handlers.py`
  Owns text, photo, document input, reply anchor candidate extraction, monitoring-mode warnings, and text `Silence on/off` interception.
- Create: `src/claude_manager/telegram_lifecycle_handlers.py`
  Owns `post_init`, restart confirmation, watcher callbacks, `/restart`, `/silence_on`, and `/silence_off`.
- Modify: `src/claude_manager/bot.py`
  Keep only constants needed by setup, `_application`, `_check_access`, handler callback injection, `_register_handlers`, `_global_error_handler`, `setup_bot`, and compatibility re-exports.
- Modify: `tests/test_bot.py`
  Keep access, setup, handler-registration, and re-export compatibility tests only.
- Create: `tests/test_telegram_agent_handlers.py`
  Focused tests for `telegram_agent_handlers.py`.
- Create: `tests/test_telegram_session_handlers.py`
  Focused tests for `telegram_session_handlers.py`.
- Create: `tests/test_telegram_input_handlers.py`
  Focused tests for `telegram_input_handlers.py`, including the current reply-anchor input tests.
- Create: `tests/test_telegram_lifecycle_handlers.py`
  Focused tests for `telegram_lifecycle_handlers.py`.
- Modify as needed: `tests/test_reply_anchor_stop.py`, `tests/test_stop_triggers_retry_whitebox.py`
  Update patch paths for `/stop` from `claude_manager.bot.*` to `claude_manager.telegram_session_handlers.*` where the implementation now lives.

## Size Guard

Current code-file baseline before implementation:

```text
src/claude_manager/bot.py lines=979 public_defs=15
src/claude_manager/telegram_project_handlers.py lines=285 public_defs=4
src/claude_manager/telegram_response_delivery.py lines=284 public_defs=4
src/claude_manager/process_manager.py lines=1188 public_defs=4
```

Before the first Python edit, run:

```bash
wc -l src/claude_manager/bot.py src/claude_manager/telegram_project_handlers.py src/claude_manager/telegram_response_delivery.py src/claude_manager/process_manager.py
for f in src/claude_manager/bot.py src/claude_manager/telegram_project_handlers.py src/claude_manager/telegram_response_delivery.py src/claude_manager/process_manager.py; do
  printf '%s public_defs=' "$f"
  grep -Ec '^(async )?def [A-Za-z][A-Za-z0-9_]*\(' "$f"
done
```

Expected before edits:

```text
bot.py is above the 700-line stop threshold and above 10 public top-level functions.
process_manager.py is above 1000 lines, but it is owned by the separate active refactor.
telegram_project_handlers.py and telegram_response_delivery.py are below 300 lines and must not receive unrelated handler logic.
```

After each code task, rerun the same count for touched `.py` files. If any new handler module approaches 300 lines, split that module before continuing. If `bot.py` remains above 500 lines after Task 5, stop and revise the module boundaries.

---

### Task 0: Workspace And Baseline Gate

**Files:**
- Read: `CLAUDE.md`
- Read: `docs/superpowers/specs/2026-05-31-bot-transport-handler-split-design.md`
- Read: `docs/superpowers/plans/2026-05-31-process-manager-state-split-implementation.md`

- [ ] **Step 1: Check branch and dirty state**

Run:

```bash
git branch --show-current
git status --short --untracked-files=all
git diff --name-only -- src/claude_manager/process_manager.py src/claude_manager/process_state.py
git diff --name-only --cached -- src/claude_manager/process_manager.py src/claude_manager/process_state.py
```

Expected:

```text
No tracked or staged process_manager/process_state changes in the current checkout.
Untracked documentation or RCA artifacts may exist and must be left untouched.
```

If the last two commands print either process-manager path, stop and get a checkpoint or use a separate worktree.

- [ ] **Step 2: Create an isolated worktree if process-manager work is still active**

Run this only when the current checkout cannot be used safely:

```bash
git worktree add ../claude_manager-bot-handler-split -b refactor/bot-handler-split
cd ../claude_manager-bot-handler-split
```

Expected:

```text
A separate checkout exists for the bot split.
The original checkout keeps the active process_manager refactor untouched.
```

- [ ] **Step 3: Run the narrow baseline gate**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_project_switch_handlers_behavior.py tests/test_reply_anchor_input_candidates.py tests/test_reply_anchor_stop.py -q
```

Expected:

```text
PASS, or a pre-existing unrelated failure documented before editing.
```

- [ ] **Step 4: Record size baseline**

Run:

```bash
wc -l src/claude_manager/bot.py src/claude_manager/process_manager.py
for f in src/claude_manager/bot.py src/claude_manager/process_manager.py; do
  printf '%s public_defs=' "$f"
  grep -Ec '^(async )?def [A-Za-z][A-Za-z0-9_]*\(' "$f"
done
```

Expected:

```text
bot.py starts around 979 lines and 15 public top-level functions.
process_manager.py is reported as existing separate technical debt, not edited here.
```

### Task 1: Extract Agent Handlers

**Files:**
- Create: `src/claude_manager/telegram_agent_handlers.py`
- Modify: `src/claude_manager/bot.py`
- Create: `tests/test_telegram_agent_handlers.py`
- Modify: `tests/test_bot.py`

- [ ] **Step 1: Run the agent-handler red/green baseline**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py -k 'HandleAgent' -q
```

Expected:

```text
PASS before movement.
```

- [ ] **Step 2: Create the agent handler module**

Move these definitions from `bot.py` into `src/claude_manager/telegram_agent_handlers.py` with unchanged behavior:

- `_get_backend_display_name`
- `_get_backend_plain_name`
- `_build_agent_keyboard`
- `_parse_agent_callback_data`
- `_build_agent_switch_confirmation`
- `handle_agent`
- `handle_agent_callback`

Add this callback wiring at the top of the new module:

```python
"""Telegram handlers for CLI-agent backend selection."""

import logging
from collections.abc import Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from claude_manager import (
    coding_agent_backend,
    current_backend_registry,
    daily_session_registry,
    session_manager,
    telegram_sender,
)
from claude_manager.coding_agent_backend import BackendName

logger = logging.getLogger(__name__)

_ApplicationGetter = Callable[[], Application | None]
_AccessChecker = Callable[[Update], bool]
_application_getter: _ApplicationGetter | None = None
_access_checker: _AccessChecker | None = None


def init_callbacks(
    application_getter: _ApplicationGetter,
    access_checker: _AccessChecker,
) -> None:
    """Inject bot-owned callbacks needed by agent handlers."""
    global _application_getter, _access_checker
    _application_getter = application_getter
    _access_checker = access_checker


def _get_application() -> Application:
    if _application_getter is None:
        raise RuntimeError("telegram agent handlers are not initialized")
    application = _application_getter()
    if application is None:
        raise RuntimeError("telegram application is not initialized")
    return application


def _has_access(update: Update) -> bool:
    if _access_checker is None:
        raise RuntimeError("telegram agent access checker is not initialized")
    return _access_checker(update)
```

In moved handlers, replace `_check_access(update)` with `_has_access(update)` and `_application.bot` with `_get_application().bot`.

- [ ] **Step 3: Re-export agent names from `bot.py`**

In `bot.py`, import the new module and expose compatibility names:

```python
from claude_manager import telegram_agent_handlers

handle_agent = telegram_agent_handlers.handle_agent
handle_agent_callback = telegram_agent_handlers.handle_agent_callback
```

Add or update a single callback initializer in `bot.py`:

```python
def _get_application_for_handlers() -> Application | None:
    return _application


def _has_access_for_handlers(update: Update) -> bool:
    return _check_access(update)


def _init_handler_callbacks() -> None:
    telegram_agent_handlers.init_callbacks(
        _get_application_for_handlers,
        _has_access_for_handlers,
    )
    telegram_project_handlers.init_callbacks(
        _get_application_for_handlers,
        _has_access_for_handlers,
    )
```

Call `_init_handler_callbacks()` once after `_check_access` is defined and again in `setup_bot()` after `_application = application`. This keeps tests that patch `bot._check_access` working because `_has_access_for_handlers()` resolves the current `bot._check_access` global at call time.

- [ ] **Step 4: Move agent tests**

Create `tests/test_telegram_agent_handlers.py` by moving `TestHandleAgent` from `tests/test_bot.py`.

Use focused imports:

```python
from claude_manager import telegram_agent_handlers as agent_handlers
```

Update calls from:

```python
await bot_module.handle_agent(update, context)
await bot_module.handle_agent_callback(update, context)
```

to:

```python
await agent_handlers.handle_agent(update, context)
await agent_handlers.handle_agent_callback(update, context)
```

Update patch targets from `claude_manager.bot.*` to `claude_manager.telegram_agent_handlers.*` when they patch moved module imports. Keep registry patches such as `patch.object(current_backend_registry, ...)` unchanged.

- [ ] **Step 5: Add a bot re-export compatibility test**

Keep this in `tests/test_bot.py`:

```python
def test_bot_reexports_agent_handlers() -> None:
    """Old imports from claude_manager.bot keep working for agent handlers."""
    from claude_manager import bot as bot_module
    from claude_manager import telegram_agent_handlers

    assert bot_module.handle_agent is telegram_agent_handlers.handle_agent
    assert bot_module.handle_agent_callback is telegram_agent_handlers.handle_agent_callback
```

- [ ] **Step 6: Run the agent gate and size count**

Run:

```bash
.venv/bin/python -m pytest tests/test_telegram_agent_handlers.py tests/test_bot.py -q
wc -l src/claude_manager/bot.py src/claude_manager/telegram_agent_handlers.py
```

Expected:

```text
PASS.
telegram_agent_handlers.py is below 300 lines.
bot.py line count decreases.
```

### Task 2: Extract Session Handlers

**Files:**
- Create: `src/claude_manager/telegram_session_handlers.py`
- Modify: `src/claude_manager/bot.py`
- Create: `tests/test_telegram_session_handlers.py`
- Modify: `tests/test_bot.py`
- Modify: `tests/test_reply_anchor_stop.py`
- Modify: `tests/test_stop_triggers_retry_whitebox.py`

- [ ] **Step 1: Run the session-handler baseline**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_reply_anchor_stop.py tests/test_stop_triggers_retry_whitebox.py -k 'HandleNew or HandleSessions or HandleStop or HandleAll or HandleSwitchSession or reply_anchor or HandleStopDuringRetry' -q
```

Expected:

```text
PASS before movement.
```

- [ ] **Step 2: Create the session handler module**

Move these definitions from `bot.py` into `src/claude_manager/telegram_session_handlers.py`:

- `SESSION_LIST_LIMIT`
- `ALL_PROJECTS_MODE_ENABLED_MESSAGE`
- `handle_new`
- `handle_sessions`
- `handle_stop`
- `handle_all`
- `handle_switch_session`

Use the same callback wiring pattern as Task 1, with module-specific runtime messages:

```python
"""Telegram handlers for session commands and all-project mode."""

import logging
from collections.abc import Callable

from telegram import Update
from telegram.ext import Application, ContextTypes

from claude_manager import (
    all_projects_monitor,
    coding_agent_backend,
    config,
    current_backend_registry,
    daily_session_registry,
    process_manager,
    reply_anchor_registry,
    session_manager,
    telegram_sender,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.session_manager import ActiveSession

logger = logging.getLogger(__name__)

SESSION_LIST_LIMIT = 15
ALL_PROJECTS_MODE_ENABLED_MESSAGE = (
    "Режим all включён: показываю сообщения из всех проектов.\n"
    "Писать агенту отсюда нельзя — сначала выберите проект и сессию."
)
```

Add a local backend-name helper:

```python
def _get_backend_display_name(backend: BackendName) -> str:
    """Возвращает человекочитаемое имя CLI-backend-а."""
    return coding_agent_backend.get_backend(backend).display_name
```

In moved handlers, replace `_check_access(update)` with `_has_access(update)` and `_application.bot` with `_get_application().bot`.

- [ ] **Step 3: Preserve `process_manager` boundary**

In `handle_stop`, keep the existing public calls exactly:

```python
process_manager.has_process(session_id, backend)
process_manager.is_busy(session_id, backend)
await process_manager.stop_process(session_id, backend)
```

Do not import or call `process_state`. Do not change `/stop` branching. Do not move `/stop` logic into `process_manager`.

- [ ] **Step 4: Wire and re-export session handlers from `bot.py`**

Add:

```python
from claude_manager import telegram_session_handlers

ALL_PROJECTS_MODE_ENABLED_MESSAGE = (
    telegram_session_handlers.ALL_PROJECTS_MODE_ENABLED_MESSAGE
)
SESSION_LIST_LIMIT = telegram_session_handlers.SESSION_LIST_LIMIT
handle_new = telegram_session_handlers.handle_new
handle_sessions = telegram_session_handlers.handle_sessions
handle_stop = telegram_session_handlers.handle_stop
handle_all = telegram_session_handlers.handle_all
handle_switch_session = telegram_session_handlers.handle_switch_session
```

Add the module to `_init_handler_callbacks()`:

```python
telegram_session_handlers.init_callbacks(
    _get_application_for_handlers,
    _has_access_for_handlers,
)
```

- [ ] **Step 5: Move session tests**

Create `tests/test_telegram_session_handlers.py` by moving these sections from `tests/test_bot.py`:

- `FakeBackendForSessionList`
- `_session_file`
- `TestHandleNew`
- `TestHandleSessions`
- `TestHandleStop`
- `TestHandleAll`
- `TestHandleSwitchSession`

Update handler calls to use:

```python
from claude_manager import telegram_session_handlers as session_handlers
```

Update direct calls:

```python
await session_handlers.handle_new(update, context)
await session_handlers.handle_sessions(update, context)
await session_handlers.handle_stop(update, context)
await session_handlers.handle_all(update, context)
await session_handlers.handle_switch_session(update, context)
```

Update patch targets for moved imports:

```text
claude_manager.bot.telegram_sender.send_telegram_message
```

becomes:

```text
claude_manager.telegram_session_handlers.telegram_sender.send_telegram_message
```

- [ ] **Step 6: Update `/stop` adjacency tests**

In `tests/test_reply_anchor_stop.py`, import the new module:

```python
from claude_manager import telegram_session_handlers as session_handlers
```

Call:

```python
await session_handlers.handle_stop(_make_update(), _make_context())
```

In `tests/test_stop_triggers_retry_whitebox.py`, update moved patch targets:

```python
patch("claude_manager.telegram_session_handlers.telegram_sender.send_telegram_message", new_callable=AsyncMock)
```

Patch access through `bot._check_access` only if the handler was initialized through `bot._has_access_for_handlers`. Otherwise patch `telegram_session_handlers._has_access` directly:

```python
patch.object(session_handlers, "_has_access", return_value=True)
```

Prefer the `bot._has_access_for_handlers` path when possible, because it verifies compatibility through the facade.

- [ ] **Step 7: Add bot re-export compatibility tests**

Keep this in `tests/test_bot.py`:

```python
def test_bot_reexports_session_handlers() -> None:
    """Old imports from claude_manager.bot keep working for session handlers."""
    from claude_manager import bot as bot_module
    from claude_manager import telegram_session_handlers

    assert bot_module.handle_new is telegram_session_handlers.handle_new
    assert bot_module.handle_sessions is telegram_session_handlers.handle_sessions
    assert bot_module.handle_stop is telegram_session_handlers.handle_stop
    assert bot_module.handle_all is telegram_session_handlers.handle_all
    assert bot_module.handle_switch_session is telegram_session_handlers.handle_switch_session
```

- [ ] **Step 8: Run the session gate and size count**

Run:

```bash
.venv/bin/python -m pytest tests/test_telegram_session_handlers.py tests/test_reply_anchor_stop.py tests/test_stop_triggers_retry_whitebox.py tests/test_bot.py -q
wc -l src/claude_manager/bot.py src/claude_manager/telegram_session_handlers.py src/claude_manager/process_manager.py
```

Expected:

```text
PASS.
telegram_session_handlers.py is below 300 lines.
process_manager.py line count is unchanged by this task.
```

### Task 3: Extract Input Handlers

**Files:**
- Create: `src/claude_manager/telegram_input_handlers.py`
- Modify: `src/claude_manager/bot.py`
- Create: `tests/test_telegram_input_handlers.py`
- Modify: `tests/test_reply_anchor_input_candidates.py` or fold it into `tests/test_telegram_input_handlers.py`
- Modify: `tests/test_bot.py`

- [ ] **Step 1: Run the input baseline**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_reply_anchor_input_candidates.py -k 'HandleMessage or HandlePhoto or HandleDocument or anchor' -q
```

Expected:

```text
PASS before movement.
```

- [ ] **Step 2: Create the input handler module**

Move these definitions from `bot.py` into `src/claude_manager/telegram_input_handlers.py`:

- `ALL_PROJECTS_MODE_INPUT_WARNING`
- `_monitoring_mode_message_for_chat`
- `_reply_anchor_kwargs`
- `handle_message`
- `_handle_single_photo`
- `handle_photo`
- `handle_document`

Start the module with:

```python
"""Telegram handlers for user text, photo, and document input."""

import logging
from collections.abc import Callable
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes

from claude_manager import (
    all_projects_monitor,
    claude_interaction,
    file_delivery,
    media_group_handler,
    session_manager,
    silence_mode_registry,
    telegram_file_downloader,
    telegram_sender,
)

logger = logging.getLogger(__name__)

ALL_PROJECTS_MODE_INPUT_WARNING = (
    "Вы в режиме all по всем проектам. Чтобы писать агенту, сначала войдите "
    "в проект и сессию: выберите проект через /projects или нажмите команду "
    "вида /1s2 в сообщении all."
)
```

Use the same callback wiring pattern as Task 1. In moved handlers, replace `_check_access(update)` with `_has_access(update)` and `_application.bot` with `_get_application().bot`.

- [ ] **Step 3: Keep reply-anchor behavior unchanged**

The moved `_reply_anchor_kwargs` must keep this exact behavior:

```python
def _reply_anchor_kwargs(update: Update) -> dict[str, int]:
    """Return send kwargs for a real Telegram message_id anchor candidate."""
    message_id = update.message.message_id
    if not isinstance(message_id, int):
        return {}
    return {"reply_to_message_id": message_id}
```

Calls to `claude_interaction.send_to_claude_and_respond` must still pass `**_reply_anchor_kwargs(update)` for text, single photo, and document input.

- [ ] **Step 4: Wire and re-export input handlers from `bot.py`**

Add:

```python
from claude_manager import telegram_input_handlers

ALL_PROJECTS_MODE_INPUT_WARNING = telegram_input_handlers.ALL_PROJECTS_MODE_INPUT_WARNING
handle_message = telegram_input_handlers.handle_message
handle_photo = telegram_input_handlers.handle_photo
handle_document = telegram_input_handlers.handle_document
```

Add the module to `_init_handler_callbacks()`:

```python
telegram_input_handlers.init_callbacks(
    _get_application_for_handlers,
    _has_access_for_handlers,
)
```

- [ ] **Step 5: Move input tests**

Create `tests/test_telegram_input_handlers.py` by moving:

- `TestHandleMessage`
- `TestHandlePhoto`
- `TestHandleDocument`
- the three input-anchor tests from `tests/test_reply_anchor_input_candidates.py`

Update imports and patch targets:

```python
from claude_manager import telegram_input_handlers as input_handlers
```

Examples:

```python
await input_handlers.handle_message(update, context)
await input_handlers.handle_photo(update, context)
await input_handlers.handle_document(update, context)
```

Patch moved dependencies through:

```text
claude_manager.telegram_input_handlers.claude_interaction.send_to_claude_and_respond
claude_manager.telegram_input_handlers.telegram_file_downloader.download_and_save_file
claude_manager.telegram_input_handlers.silence_mode_registry
```

Keep the two `media_group_handler.select_album_anchor_message_id` tests in `tests/test_reply_anchor_input_candidates.py` or move them to `tests/test_media_group_handler.py`; they test `media_group_handler`, not the new input module.

- [ ] **Step 6: Add bot re-export compatibility tests**

Keep this in `tests/test_bot.py`:

```python
def test_bot_reexports_input_handlers() -> None:
    """Old imports from claude_manager.bot keep working for input handlers."""
    from claude_manager import bot as bot_module
    from claude_manager import telegram_input_handlers

    assert bot_module.handle_message is telegram_input_handlers.handle_message
    assert bot_module.handle_photo is telegram_input_handlers.handle_photo
    assert bot_module.handle_document is telegram_input_handlers.handle_document
```

- [ ] **Step 7: Run the input gate and size count**

Run:

```bash
.venv/bin/python -m pytest tests/test_telegram_input_handlers.py tests/test_reply_anchor_input_candidates.py tests/test_bot.py -q
wc -l src/claude_manager/bot.py src/claude_manager/telegram_input_handlers.py src/claude_manager/telegram_response_delivery.py
```

Expected:

```text
PASS.
telegram_input_handlers.py is below 300 lines.
telegram_response_delivery.py is unchanged.
```

### Task 4: Extract Lifecycle Handlers

**Files:**
- Create: `src/claude_manager/telegram_lifecycle_handlers.py`
- Modify: `src/claude_manager/bot.py`
- Create: `tests/test_telegram_lifecycle_handlers.py`
- Modify: `tests/test_bot.py`

- [ ] **Step 1: Run the lifecycle baseline**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py -k 'PostInit or HandleRestart or post_init or restart' -q
```

Expected:

```text
PASS before movement.
```

- [ ] **Step 2: Create the lifecycle module**

Move these definitions from `bot.py` into `src/claude_manager/telegram_lifecycle_handlers.py`:

- `BOT_COMMANDS`
- `RESTART_DELAY_BEFORE_SYSTEMCTL_SECONDS`
- `RESTART_MARKER_PATH`
- `_notify_restart_complete`
- `post_init`
- `_watcher_callback`
- `_all_projects_watcher_callback`
- `_get_current_session_async`
- `handle_restart`
- `handle_silence_on`
- `handle_silence_off`

Start the module with:

```python
"""Telegram application lifecycle and service command handlers."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from telegram import BotCommand, Update
from telegram.ext import Application, ContextTypes

from claude_manager import (
    all_projects_monitor,
    config,
    current_backend_registry,
    daily_session_registry,
    session_manager,
    session_watcher,
    silence_mode_registry,
    telegram_file_downloader,
    telegram_response_delivery,
    telegram_sender,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.session_manager import ActiveSession

logger = logging.getLogger(__name__)
```

Use the same callback wiring pattern as Task 1 for command handlers. In `post_init`, prefer the `application` argument for Telegram sends:

```python
await telegram_sender.send_telegram_message(
    application.bot,
    chat_id,
    "Не удалось загрузить реестр дневных сессий после 10 попыток. "
    "Нумерация сессий может начаться заново. "
    "Попробуй перезапустить бота.",
    parse_mode=None,
)
```

This keeps behavior the same while removing lifecycle dependence on `bot._application`.

- [ ] **Step 3: Wire and re-export lifecycle names from `bot.py`**

Add:

```python
from claude_manager import telegram_lifecycle_handlers

BOT_COMMANDS = telegram_lifecycle_handlers.BOT_COMMANDS
RESTART_DELAY_BEFORE_SYSTEMCTL_SECONDS = (
    telegram_lifecycle_handlers.RESTART_DELAY_BEFORE_SYSTEMCTL_SECONDS
)
RESTART_MARKER_PATH = telegram_lifecycle_handlers.RESTART_MARKER_PATH
post_init = telegram_lifecycle_handlers.post_init
handle_restart = telegram_lifecycle_handlers.handle_restart
handle_silence_on = telegram_lifecycle_handlers.handle_silence_on
handle_silence_off = telegram_lifecycle_handlers.handle_silence_off
```

Add the module to `_init_handler_callbacks()`:

```python
telegram_lifecycle_handlers.init_callbacks(
    _get_application_for_handlers,
    _has_access_for_handlers,
)
```

Keep `setup_bot()` using the re-export:

```python
.post_init(post_init)
```

- [ ] **Step 4: Move lifecycle tests**

Create `tests/test_telegram_lifecycle_handlers.py` by moving:

- `TestPostInit`
- `TestHandleRestart`
- E2E-user `post_init` tests from `TestE2eTestUserAccess`
- current-backend-load `post_init` test from `TestE2eTestUserAccess`

Update patch targets:

```text
claude_manager.bot.telegram_file_downloader.clean_old_received_files
claude_manager.bot.asyncio.create_task
claude_manager.bot.session_manager
claude_manager.bot.silence_mode_registry
claude_manager.bot.current_backend_registry
```

become:

```text
claude_manager.telegram_lifecycle_handlers.telegram_file_downloader.clean_old_received_files
claude_manager.telegram_lifecycle_handlers.asyncio.create_task
claude_manager.telegram_lifecycle_handlers.session_manager
claude_manager.telegram_lifecycle_handlers.silence_mode_registry
claude_manager.telegram_lifecycle_handlers.current_backend_registry
```

In restart tests, patch:

```python
monkeypatch.setattr(lifecycle_handlers, "RESTART_MARKER_PATH", marker_path)
```

not `bot.RESTART_MARKER_PATH`, because the implementation now reads the lifecycle module constant.

- [ ] **Step 5: Add bot re-export compatibility tests**

Keep this in `tests/test_bot.py`:

```python
def test_bot_reexports_lifecycle_handlers() -> None:
    """Old imports from claude_manager.bot keep working for lifecycle handlers."""
    from claude_manager import bot as bot_module
    from claude_manager import telegram_lifecycle_handlers

    assert bot_module.post_init is telegram_lifecycle_handlers.post_init
    assert bot_module.handle_restart is telegram_lifecycle_handlers.handle_restart
    assert bot_module.handle_silence_on is telegram_lifecycle_handlers.handle_silence_on
    assert bot_module.handle_silence_off is telegram_lifecycle_handlers.handle_silence_off
    assert bot_module.BOT_COMMANDS is telegram_lifecycle_handlers.BOT_COMMANDS
```

- [ ] **Step 6: Run the lifecycle gate and size count**

Run:

```bash
.venv/bin/python -m pytest tests/test_telegram_lifecycle_handlers.py tests/test_bot.py -q
wc -l src/claude_manager/bot.py src/claude_manager/telegram_lifecycle_handlers.py
```

Expected:

```text
PASS.
telegram_lifecycle_handlers.py is below 300 lines.
bot.py is near facade size.
```

### Task 5: Finish `bot.py` As Facade

**Files:**
- Modify: `src/claude_manager/bot.py`
- Modify: `tests/test_bot.py`

- [ ] **Step 1: Remove moved imports and logic from `bot.py`**

After Tasks 1-4, `bot.py` should keep imports for:

```python
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from claude_manager import (
    claude_interaction,
    config,
    media_group_handler,
    telegram_agent_handlers,
    telegram_input_handlers,
    telegram_lifecycle_handlers,
    telegram_project_handlers,
    telegram_response_delivery,
    telegram_sender,
    telegram_session_handlers,
)
```

Remove imports that are only used by moved handlers, such as `all_projects_monitor`, `coding_agent_backend`, `current_backend_registry`, `daily_session_registry`, `file_delivery`, `process_manager`, `reply_anchor_registry`, `session_manager`, `session_reader`, `session_watcher`, `silence_mode_registry`, and `telegram_file_downloader`.

- [ ] **Step 2: Keep handler registration order unchanged**

`_register_handlers()` must keep this order:

```python
application.add_handler(CommandHandler("new", handle_new))
application.add_handler(CommandHandler("agent", handle_agent))
application.add_handler(
    CallbackQueryHandler(handle_agent_callback, pattern=r"^agent:(claude|codex)$")
)
application.add_handler(CommandHandler("sessions", handle_sessions))
application.add_handler(CommandHandler("stop", handle_stop))
application.add_handler(CommandHandler(["all", "all_projects"], handle_all))
application.add_handler(CommandHandler("projects", handle_projects))
application.add_handler(CommandHandler("silence_on", handle_silence_on))
application.add_handler(CommandHandler("silence_off", handle_silence_off))
application.add_handler(CommandHandler("restart", handle_restart))
application.add_handler(
    MessageHandler(filters.Regex(r"^/\d+s\d+$"), handle_switch_project_session)
)
application.add_handler(MessageHandler(filters.Regex(r"^/\d+$"), handle_switch_session))
application.add_handler(MessageHandler(filters.Regex(r"^/p\d+$"), handle_switch_project))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
```

The `/pN` handler must remain before the general text handler.

- [ ] **Step 3: Keep setup callback wiring unchanged**

`setup_bot()` must still initialize response delivery, `claude_interaction`, and `media_group_handler`:

```python
telegram_response_delivery.init_application(application)
_init_handler_callbacks()

claude_interaction.init_callbacks(
    send_response_module=telegram_response_delivery,
    send_response_attr="send_response",
    send_telegram_message_module=telegram_response_delivery,
    send_telegram_message_attr="_send_telegram_message_bridge",
)
```

Keep the media group callbacks in `bot.py` because they are setup wiring:

```python
async def _send_chat_action_for_media_group(chat_id: int) -> None:
    await _application.bot.send_chat_action(chat_id, ChatAction.TYPING)


async def _send_telegram_message_for_media_group(
    chat_id: int,
    text: str,
    parse_mode: str | None,
) -> None:
    await telegram_sender.send_telegram_message(
        _application.bot,
        chat_id,
        text,
        parse_mode=parse_mode,
    )
```

- [ ] **Step 4: Add a handler registration test**

Keep or add a focused test in `tests/test_bot.py`:

```python
@patch("claude_manager.bot.ApplicationBuilder")
def test_setup_bot_registers_handlers_in_expected_order(
    mock_builder_class: MagicMock,
) -> None:
    """setup_bot registers command handlers before broad text handlers."""
    mock_app = MagicMock()
    mock_app.add_handler = MagicMock()
    mock_builder = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.post_init.return_value = mock_builder
    mock_builder.concurrent_updates.return_value = mock_builder
    mock_builder.connect_timeout.return_value = mock_builder
    mock_builder.read_timeout.return_value = mock_builder
    mock_builder.write_timeout.return_value = mock_builder
    mock_builder.pool_timeout.return_value = mock_builder
    mock_builder.connection_pool_size.return_value = mock_builder
    mock_builder.build.return_value = mock_app
    mock_builder_class.return_value = mock_builder

    bot_module.setup_bot()

    handlers = [call.args[0] for call in mock_app.add_handler.call_args_list]
    command_sets = [getattr(handler, "commands", set()) for handler in handlers]
    assert {"new"} in command_sets
    assert {"all", "all_projects"} in command_sets
    assert mock_app.add_error_handler.called
```

Keep existing `/all_projects` alias assertion.

- [ ] **Step 5: Run facade gate and size count**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py -q
wc -l src/claude_manager/bot.py
grep -Ec '^(async )?def [A-Za-z][A-Za-z0-9_]*\(' src/claude_manager/bot.py
```

Expected:

```text
PASS.
bot.py is below 300 lines.
bot.py has no more than 10 public top-level functions.
```

### Task 6: Narrow Regression Gates

**Files:**
- Modify only if a moved import or patch path is broken.

- [ ] **Step 1: Run the Telegram handler gate**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_telegram_agent_handlers.py tests/test_telegram_session_handlers.py tests/test_telegram_input_handlers.py tests/test_telegram_lifecycle_handlers.py tests/test_project_switch_handlers_behavior.py tests/test_reply_anchor_input_candidates.py tests/test_reply_anchor_stop.py -q
```

Expected:

```text
PASS.
```

- [ ] **Step 2: Run the process boundary gate**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_claude_interaction.py tests/test_process_manager.py tests/test_stop_triggers_retry_blackbox.py tests/test_stop_triggers_retry_whitebox.py -q
```

Expected:

```text
PASS.
No process_manager API changes were required.
```

- [ ] **Step 3: Run the project/input adjacency gate**

Run:

```bash
.venv/bin/python -m pytest tests/test_project_switch_handlers_behavior.py tests/test_reply_anchor_direct_delivery.py tests/test_reply_anchor_background_delivery.py tests/test_project_pending_delivery_behavior.py tests/integration/test_project_switching.py tests/integration/test_watcher_handler_coordination.py -q
```

Expected:

```text
PASS.
Project switching, reply anchors, pending delivery, and watcher coordination still work.
```

### Task 7: Full Non-E2E Gate And Size Report

**Files:**
- Modify only if verification reveals a narrow mechanical issue.

- [ ] **Step 1: Run the full non-E2E suite**

Run:

```bash
.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q
```

Expected:

```text
PASS.
```

- [ ] **Step 2: Produce final size report**

Run:

```bash
wc -l \
  src/claude_manager/bot.py \
  src/claude_manager/telegram_agent_handlers.py \
  src/claude_manager/telegram_session_handlers.py \
  src/claude_manager/telegram_input_handlers.py \
  src/claude_manager/telegram_lifecycle_handlers.py \
  src/claude_manager/telegram_project_handlers.py \
  src/claude_manager/telegram_response_delivery.py \
  src/claude_manager/process_manager.py

for f in \
  src/claude_manager/bot.py \
  src/claude_manager/telegram_agent_handlers.py \
  src/claude_manager/telegram_session_handlers.py \
  src/claude_manager/telegram_input_handlers.py \
  src/claude_manager/telegram_lifecycle_handlers.py \
  src/claude_manager/telegram_project_handlers.py \
  src/claude_manager/telegram_response_delivery.py \
  src/claude_manager/process_manager.py; do
  printf '%s public_defs=' "$f"
  grep -Ec '^(async )?def [A-Za-z][A-Za-z0-9_]*\(' "$f"
done
```

Expected:

```text
bot.py is below 300 lines.
Every new handler module is below 300 lines.
telegram_project_handlers.py and telegram_response_delivery.py did not grow into handler dumping grounds.
process_manager.py is either unchanged by this plan or already reduced by its own checkpointed refactor.
```

- [ ] **Step 3: Check imports and formatting**

Run:

```bash
.venv/bin/python -m compileall src/claude_manager
git diff --check
```

Expected:

```text
No import syntax failures.
No whitespace errors.
```

### Task 8: Review And Commit

**Files:**
- Commit: new handler modules
- Commit: `src/claude_manager/bot.py`
- Commit: focused tests

- [ ] **Step 1: Review diff boundaries**

Run:

```bash
git diff -- \
  src/claude_manager/bot.py \
  src/claude_manager/telegram_agent_handlers.py \
  src/claude_manager/telegram_session_handlers.py \
  src/claude_manager/telegram_input_handlers.py \
  src/claude_manager/telegram_lifecycle_handlers.py \
  tests/test_bot.py \
  tests/test_telegram_agent_handlers.py \
  tests/test_telegram_session_handlers.py \
  tests/test_telegram_input_handlers.py \
  tests/test_telegram_lifecycle_handlers.py \
  tests/test_reply_anchor_stop.py \
  tests/test_stop_triggers_retry_whitebox.py
```

Expected:

```text
Diff is mechanical extraction plus test patch-path updates.
No Telegram text, command behavior, process_manager API, project switching behavior, or response delivery behavior is rewritten.
```

- [ ] **Step 2: Confirm process-manager files are untouched by this plan**

Run:

```bash
git diff --name-only -- src/claude_manager/process_manager.py src/claude_manager/process_state.py
git diff --name-only --cached -- src/claude_manager/process_manager.py src/claude_manager/process_state.py
```

Expected:

```text
No output, unless the implementation intentionally ran after the separate process-manager checkpoint and those files were changed by that already-completed work outside this plan.
```

- [ ] **Step 3: Commit the bot split**

Run:

```bash
git add \
  src/claude_manager/bot.py \
  src/claude_manager/telegram_agent_handlers.py \
  src/claude_manager/telegram_session_handlers.py \
  src/claude_manager/telegram_input_handlers.py \
  src/claude_manager/telegram_lifecycle_handlers.py \
  tests/test_bot.py \
  tests/test_telegram_agent_handlers.py \
  tests/test_telegram_session_handlers.py \
  tests/test_telegram_input_handlers.py \
  tests/test_telegram_lifecycle_handlers.py \
  tests/test_reply_anchor_stop.py \
  tests/test_stop_triggers_retry_whitebox.py
git commit -m "refactor: split telegram bot handlers"
```

If a listed test file was not changed, omit it from `git add`.

## Self-Review

- Spec coverage: `bot.py` facade, four domain handler modules, project handler preservation, response delivery preservation, process-manager boundary, tests, and size guard are all covered.
- Active-refactor coordination: Task 0 and Task 8 explicitly prevent silent overlap with the `process_manager.py` split.
- Compatibility: every old public handler name remains re-exported through `claude_manager.bot`.
- Behavior preservation: no Telegram texts, command names, handler order, or process-manager calls are intentionally changed.
- Placeholder scan: no open implementation slots are left; every task names exact files, moved definitions, commands, and expected outcomes.
