# Process Manager State Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move only process in-memory state from `process_manager.py` into `process_state.py` without changing launch, retry, `/stop`, or public imports.

**Architecture:** `process_state.py` becomes the single owner of process dictionaries, lock, process-key helpers, alias resolution, and session-id remap. `process_manager.py` stays the orchestrator and re-exports the same private state names by importing the actual mutable objects, so existing tests and runtime callers still see the same API surface.

**Tech Stack:** Python 3.13, asyncio, pytest, pytest-asyncio.

---

## Beginner Context

This part of the project starts and controls Claude/Codex CLI processes for the Telegram bot. A **process state** is the in-memory map that says "which CLI subprocess belongs to which session", "is it busy", and "was `/stop` requested". An **alias** is the temporary session id -> real session id mapping used when a new Claude/Codex session receives its real id. A **gate** is a test command that must pass before we trust the change.

The practical risk is concurrency: if the lock or alias helpers move incorrectly, `/stop`, retry, or temp -> real remap can break even though the code still imports.

## Source Spec

- `docs/superpowers/specs/2026-05-31-process-manager-state-split-design.md`

## File Structure

- Create: `src/claude_manager/process_state.py`
  Owns process state, process keys, alias helpers, `is_busy`, `has_process`, and `update_session_id`.
- Modify: `src/claude_manager/process_manager.py`
  Imports state objects/helpers/functions from `process_state.py`; keeps launch, event reading, retry, cleanup, `/stop`, and public result dataclasses.
- Optional test add: `tests/test_process_manager.py`
  Add a tiny re-export identity test only if the existing tests do not clearly fail on broken re-exports.

## Size Guard

- Current `src/claude_manager/process_manager.py`: 1328 lines, above the 1000-line urgent split threshold.
- This task is still allowed because it reduces the large file rather than adding new behavior.
- Before code edits, count lines and top-level public functions for touched `.py` files.
- After code edits, count again and report the delta.
- If the exact spec-bounded move reduces `process_manager.py` by less than the expected 250-350 lines, do not widen scope silently; report the mismatch and keep behavior unchanged.

---

### Task 1: Baseline And Compatibility Test

**Files:**
- Modify: none by default
- Optional modify: `tests/test_process_manager.py`
- Test: `tests/test_process_manager.py`

- [ ] **Step 1: Check git and file sizes**

Run:

```bash
git status --short
wc -l src/claude_manager/process_manager.py
rg '^def [a-zA-Z][a-zA-Z0-9_]*\(' -n src/claude_manager/process_manager.py
```

Expected:

```text
Only known unrelated untracked RCA/session-report artifacts may appear.
process_manager.py is currently around 1328 lines.
Top-level public functions are counted and reported before editing.
```

- [ ] **Step 2: Run the narrow state/process baseline**

Run:

```bash
.venv/bin/python -m pytest tests/test_process_manager.py tests/test_stop_triggers_retry_blackbox.py tests/test_stop_triggers_retry_whitebox.py tests/integration/test_cwd_pinning_across_retries.py tests/integration/test_message_path.py -q
```

Expected:

```text
PASS, or an unrelated pre-existing failure documented before editing.
```

- [ ] **Step 3: Add a re-export identity test only if needed**

Use this exact test if a guard is needed after the move:

```python
def test_process_manager_reexports_process_state_objects() -> None:
    """process_manager exposes the same mutable state objects after the split."""
    from claude_manager import process_manager
    from claude_manager import process_state

    assert process_manager._processes is process_state._processes
    assert process_manager._busy_flags is process_state._busy_flags
    assert process_manager._busy_lock is process_state._busy_lock
    assert process_manager._stop_events is process_state._stop_events
    assert process_manager._session_id_aliases is process_state._session_id_aliases
```

Do not rewrite existing tests to import `process_state`; compatibility requires old `process_manager` access to keep working.

### Task 2: Extract `process_state.py`

**Files:**
- Create: `src/claude_manager/process_state.py`
- Modify: `src/claude_manager/process_manager.py`
- Test: `tests/test_process_manager.py`

- [ ] **Step 1: Create the new state module**

Create `src/claude_manager/process_state.py` by moving these definitions from `process_manager.py` unchanged in behavior:

```python
"""In-memory state for CLI processes managed by process_manager."""

import asyncio
import logging

from claude_manager.coding_agent_backend import BackendName
from claude_manager.claude_runner import BackendSubprocess, ClaudeProcess

logger = logging.getLogger(__name__)

type ProcessKey = str | tuple[str, BackendName]
type ManagedProcess = ClaudeProcess | BackendSubprocess

_processes: dict[ProcessKey, ManagedProcess] = {}
_busy_flags: dict[ProcessKey, bool] = {}
_busy_lock: asyncio.Lock = asyncio.Lock()
_stop_events: dict[ProcessKey, asyncio.Event] = {}
_session_id_aliases: dict[ProcessKey, ProcessKey] = {}
```

Then move the exact current bodies from `process_manager.py` into `process_state.py`:

- `process_manager.py:147` `_make_process_key`
- `process_manager.py:157` `_make_backend_process_key`
- `process_manager.py:162` `_split_process_key`
- `process_manager.py:169` `_resolve_process_key_alias_unlocked`
- `process_manager.py:187` `_prefer_existing_process_key_unlocked`
- `process_manager.py:202` `_resolve_session_id_alias_unlocked`
- `process_manager.py:209` `_remove_session_id_aliases_unlocked`
- `process_manager.py:1270` `is_busy`
- `process_manager.py:1279` `has_process`
- `process_manager.py:1291` `update_session_id`

Do not rewrite their logic while moving. Keep docstrings, lock usage, alias-cycle logging, and tuple-key preservation exactly as they are.

- [ ] **Step 2: Import state into `process_manager.py`**

Replace the moved local definitions with imports:

```python
from claude_manager.process_state import (
    ManagedProcess,
    ProcessKey,
    _busy_flags,
    _busy_lock,
    _make_backend_process_key,
    _make_process_key,
    _prefer_existing_process_key_unlocked,
    _processes,
    _remove_session_id_aliases_unlocked,
    _resolve_process_key_alias_unlocked,
    _resolve_session_id_alias_unlocked,
    _session_id_aliases,
    _split_process_key,
    _stop_events,
    has_process,
    is_busy,
    update_session_id,
)
```

Remove unused imports from `process_manager.py` only if they become unused after the move. Keep `_generate_temp_session_id`, `create_process`, `send_message`, retry, `/stop`, and cleanup logic in `process_manager.py`.

- [ ] **Step 3: Run the process-manager unit test**

Run:

```bash
.venv/bin/python -m pytest tests/test_process_manager.py -q
```

Expected:

```text
PASS
```

### Task 3: Retry/Stop Gates And Size Report

**Files:**
- Modify: none unless Task 2 revealed a narrow import issue
- Test: process-manager and Telegram-adjacent suites

- [ ] **Step 1: Run the spec minimum gate**

Run:

```bash
.venv/bin/python -m pytest tests/test_process_manager.py tests/test_stop_triggers_retry_blackbox.py tests/test_stop_triggers_retry_whitebox.py tests/integration/test_cwd_pinning_across_retries.py tests/integration/test_message_path.py -q
```

Expected:

```text
PASS
```

- [ ] **Step 2: Run the wider orchestration gate**

Run:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_claude_interaction.py tests/test_process_manager.py -q
```

Expected:

```text
PASS
```

- [ ] **Step 3: Run full non-E2E suite before declaring done**

Run:

```bash
.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q
```

Expected:

```text
PASS
```

- [ ] **Step 4: Report size and public-function counts**

Run:

```bash
wc -l src/claude_manager/process_manager.py src/claude_manager/process_state.py
rg '^def [a-zA-Z][a-zA-Z0-9_]*\(' -n src/claude_manager/process_manager.py src/claude_manager/process_state.py
```

Expected:

```text
process_manager.py line count decreases.
process_state.py remains focused and well below 300 lines.
Any file still above 1000 lines is explicitly reported as remaining technical debt.
```

### Task 4: Commit The Implementation

**Files:**
- Commit: `src/claude_manager/process_state.py`
- Commit: `src/claude_manager/process_manager.py`
- Optional commit: `tests/test_process_manager.py`

- [ ] **Step 1: Review diff**

Run:

```bash
git diff -- src/claude_manager/process_manager.py src/claude_manager/process_state.py tests/test_process_manager.py
```

Expected:

```text
Diff only moves state/helpers and import wiring.
No retry, launch, `/stop`, event-reader, or Telegram behavior is rewritten.
```

- [ ] **Step 2: Commit**

Run:

```bash
git add src/claude_manager/process_manager.py src/claude_manager/process_state.py tests/test_process_manager.py
git commit -m "refactor: split process manager state"
```

If `tests/test_process_manager.py` was not changed, omit it from `git add`.

## Self-Review

- Spec coverage: state dictionaries, process keys, alias helpers, `is_busy`, `has_process`, and `update_session_id` are covered by Task 2.
- Compatibility: old imports and private test access are covered by importing the same mutable objects into `process_manager.py`.
- Out of scope: event reader, retry loop, stop strategy, legacy Claude-only path, and Telegram behavior remain in `process_manager.py`.
- Placeholder scan: no placeholder markers or open-ended "add tests" steps remain.
