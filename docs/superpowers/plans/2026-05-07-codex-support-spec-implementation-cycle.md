# Codex Support Spec Implementation Cycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Codex-support specs in `dev/docs/specs/codex_support_spec_implementation_order.md` in dependency order, with no UI path that can select Codex before session ownership, process lifecycle, watcher, and pending delivery are backend-aware.

**Architecture:** The implementation introduces a backend-aware adapter layer around Claude Code CLI and Codex CLI. Session identity becomes `(session_id, backend)` below the Telegram layer; `current_backend_registry` controls only new sessions; existing sessions always run through the backend that created them.

**Tech Stack:** Python 3.13, pytest, pytest-asyncio, python-telegram-bot, Claude Code CLI stream-json, Codex CLI JSON output and JSONL rollout files.

---

## Superpowers-Only Rule

- Use `superpowers:using-superpowers` as the controlling workflow rule for this cycle.
- Use `superpowers:using-git-worktrees` before implementation work starts.
- Use `superpowers:writing-plans` for this plan and any later plan amendments.
- Use `superpowers:test-driven-development` for every production-code behavior change.
- Use `superpowers:subagent-driven-development` for execution when subagents are available; use `superpowers:executing-plans` only if subagent execution is not available.
- Use `superpowers:verification-before-completion` before marking any task, phase, or spec as complete.
- Use `superpowers:finishing-a-development-branch` after all implementation tasks and verification gates are complete.
- Do not use the project `.agents/skills/feature-pipeline` or `.agents/skills/implement-module` wrappers in this cycle unless the user explicitly changes this instruction.
- Still obey repository architecture rules from `/Users/ivan/.claude/CLAUDE.md`, root `CLAUDE.md`, and root `AGENTS.md` where they do not conflict with the Superpowers-only execution rule.

## File Structure

- Create: `docs/superpowers/plans/2026-05-07-codex-support-spec-implementation-cycle.md`
  This plan and execution checklist.
- Modify after each completed task: `dev/docs/specs/codex_support_spec_implementation_order.md`
  Add a completion marker under the completed subsection with date, verification command, and result.
- Modify in Task 1: `dev/docs/brd/brd-user-journeys.md`
  Add the `/agent` customer journey as `CJM-16`.
- Modify in Task 1: `dev/docs/specs/module-dependency-graph.md`
  Replace `/agent` traceability from `CJM-14` to `CJM-16`.
- Modify in Task 1: backend-aware specs under `dev/docs/specs/*.md`
  Replace `CJM-NEW` and incorrect `CJM-14` `/agent` references with `CJM-16`.
- Create in Task 2: `src/claude_manager/coding_agent_backend.py`
  Common backend contract, DTOs, exceptions, and lazy backend factory.
- Create in Task 2: `tests/test_coding_agent_backend.py`
  Unit tests for enum, DTOs, abstract interface, factory error behavior, and ownership-contract DTOs.
- Create in Task 3: `src/claude_manager/claude_code_backend.py`
  Claude Code CLI implementation of `CodingAgentBackend`.
- Create in Task 3: `tests/test_claude_code_backend.py`
  Claude adapter unit tests.
- Create or modify in Task 3: `tests/integration/test_claude_cli_contract.py`
  Real CLI contract tests or explicit skip when Claude binary is unavailable.
- Create in Task 4: `src/claude_manager/codex_backend.py`
  Codex CLI implementation of `CodingAgentBackend`.
- Create in Task 4: `tests/test_codex_backend.py`
  Codex adapter unit tests.
- Create in Task 4: `tests/integration/test_codex_cli_contract.py`
  Real Codex CLI contract tests or explicit skip when Codex binary is unavailable.
- Create in Task 5: `src/claude_manager/current_backend_registry.py`
  Persistent global backend choice for new sessions.
- Modify in Task 5: `src/claude_manager/config.py`
  Add `CURRENT_BACKEND_FILE`.
- Create in Task 5: `tests/test_current_backend_registry.py`
  Registry migration, load, save, and failed-load tests.
- Modify in Task 6: `src/claude_manager/claude_runner.py`
  Add thin backend-aware subprocess wrapper.
- Modify in Task 6: `tests/test_claude_runner.py`
  Backend-aware runner tests.
- Modify in Task 7: `src/claude_manager/daily_session_registry.py`
  Store daily entries as `DailySessionEntry(session_id, backend)`.
- Modify in Task 7: `tests/test_daily_session_registry.py`
  Migration, lookup, registration, and orphan cleanup tests.
- Modify in Task 8: `src/claude_manager/session_manager.py`
  Store active sessions as `ActiveSession(session_id, backend)`.
- Modify in Task 8: `tests/test_session_manager.py`
  Migration, `/N` switching, active session, and owner lookup tests.
- Modify in Task 9: `src/claude_manager/unread_buffer.py`
  Key unread cursor state by `(session_id, backend)`.
- Modify in Task 9: `tests/test_unread_buffer.py`
  Backend-isolation, TTL, snapshot, and clear tests.
- Modify in Task 10: `src/claude_manager/process_manager.py`
  Key process state by `(session_id, backend)` and apply backend stop strategies.
- Modify in Task 10: `tests/test_process_manager.py`
  Backend-aware send, retry, stop, busy flag, and temp-id remap tests.
- Modify in Task 11: `src/claude_manager/session_watcher.py`
  Run one watcher instance per backend and read session snapshots through backend adapters.
- Modify in Task 11: `tests/test_session_watcher.py`
  Backend-aware pause/resume, update, terminal-record, and delivery tests.
- Modify in Task 12: `src/claude_manager/project_manager.py`
  Preserve backend state across project switching and carry pending delivery backend.
- Modify in Task 12: `tests/test_project_manager.py`
  Backend-aware snapshot, pending delivery, and current-backend preservation tests.
- Modify in Task 13: `src/claude_manager/claude_interaction.py`, `src/claude_manager/bot.py`, `src/claude_manager/main.py`
  Wire backend-aware lower layers into Telegram-facing flows, excluding `/agent` UI.
- Modify in Task 13: `tests/test_claude_interaction.py`, `tests/test_bot.py`, `tests/test_main.py`
  `/new`, normal messages, `/sessions`, `/N`, `/stop`, watcher callback, pending delivery, and startup tests.
- Modify in Task 14: `src/claude_manager/bot.py`
  Add `/agent` command and callback handler after all lower layers are backend-aware.
- Modify in Task 14: `tests/test_bot.py`
  `/agent` unit tests from `agent_backend_selection_user_journey_spec.md`.
- Create in Task 15: `tests/e2e/test_agent_backend_selection.py`
  Add end-to-end coverage for `/agent`, Codex `/new`, old Claude `/N`, `/stop`, and `/all`.
- Modify in Task 15: `tests/e2e/test_project_switching.py`
  Add backend-aware pending-delivery assertions for `/projects` and `/pN`.
- Move after each module gate: `dev/docs/specs/{module}_spec.md` to `dev/docs/specs/realised/{module}_spec.md`
  Move only after fresh verification proves that module's gate passed.

---

### Task 0: Superpowers Workspace And Baseline

**Files:**
- Modify: none
- Test: existing test suite

- [x] **Step 1: Invoke the required workspace skill**

Use `superpowers:using-git-worktrees` before any production-code edit.

- [x] **Step 2: Detect whether this checkout is already isolated**

Run:

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" 2>/dev/null && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P)
git rev-parse --show-superproject-working-tree 2>/dev/null || true
git branch --show-current
git status --short --branch
printf 'GIT_DIR=%s\nGIT_COMMON=%s\n' "$GIT_DIR" "$GIT_COMMON"
```

Expected:
- If `GIT_DIR != GIT_COMMON` and the repo is not a submodule, continue in the existing isolated workspace.
- If `GIT_DIR == GIT_COMMON`, ask the user before creating a Superpowers worktree. Do not start implementation on `main` without explicit consent.

- [x] **Step 3: Run baseline tests**

Run:

```bash
python -m pytest tests/ -q
```

Expected: all tests pass. If tests fail, record the failing tests and ask whether to fix baseline first or continue with known failures.

- [x] **Step 4: Mark Task 0 complete**

Use `superpowers:verification-before-completion`. Mark this task complete only with fresh command output from Step 2 and Step 3.

**Статус:** выполнено 2026-05-07.
**Проверка workspace:** `GIT_DIR=$(cd "$(git rev-parse --git-dir)" 2>/dev/null && pwd -P); GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P); git rev-parse --show-superproject-working-tree 2>/dev/null || true; git branch --show-current; git status --short --branch; printf 'GIT_DIR=%s\nGIT_COMMON=%s\n' "$GIT_DIR" "$GIT_COMMON"` — ветка `codex-support-spec-implementation-cycle`, `GIT_DIR == GIT_COMMON`, submodule path отсутствует.
**Проверка baseline:** `python -m pytest tests/ -q` — `python` отсутствует в shell (`command not found`); `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 921 passed, 3 warnings.

---

### Task 1: Documentation Numbering Gate

**Files:**
- Modify: `dev/docs/brd/brd-user-journeys.md`
- Modify: `dev/docs/specs/module-dependency-graph.md`
- Modify: `dev/docs/specs/agent_backend_selection_user_journey_spec.md`
- Modify: `dev/docs/specs/coding_agent_backend_spec.md`
- Modify: `dev/docs/specs/current_backend_registry_spec.md`
- Modify: `dev/docs/specs/claude_code_backend_spec.md`
- Modify: `dev/docs/specs/codex_backend_spec.md`
- Modify: `dev/docs/specs/process_manager_spec.md`
- Modify: `dev/docs/specs/telegram_agent_backend_integration_spec.md`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`

- [x] **Step 1: Add `/agent` to the BRD as CJM-16**

Append this section after `CJM-15` in `dev/docs/brd/brd-user-journeys.md`:

```markdown
---

## CJM-16: Переключение CLI-бэкенда (/agent)

Пользователь хочет выбрать, какой CLI будет использоваться для новых сессий: Claude Code CLI или Codex CLI.

### Что делает пользователь

Отправляет команду `/agent`, видит список доступных CLI-бэкендов и нажимает кнопку нужного варианта.

### Что происходит внутри

1. Бот читает текущий глобальный backend из `current_backend_registry`.
2. Бот показывает inline-кнопки всех backend-ов из `get_all_backends()`.
3. При выборе нового backend-а бот атомарно сохраняет его в `~/.claude-manager-current-backend`.
4. Активная сессия не меняется.
5. Следующая команда `/new` создаёт новую сессию уже через выбранный backend.

### Что видит пользователь

Пользователь видит текущего агента, кнопки `🤖 Claude` и `⚡ Codex`, а после переключения получает подтверждение, что новые сессии будут создаваться через выбранный backend.

Если активная сессия уже есть, подтверждение отдельно говорит, что текущая сессия остаётся на своём backend-е.

### Что может пойти не так

- **Ошибка записи настройки** — бот показывает понятную ошибку, а in-memory backend не меняется.
- **Повреждённая callback data** — бот отвечает «Неизвестный агент» и пишет warning в лог.
- **CLI выбран, но binary не установлен** — выбор сохраняется, а ошибка появится при первом запуске новой сессии через этот backend.
```

- [x] **Step 2: Replace incorrect `/agent` CJM references**

Replace:

```text
CJM-14: Переключение CLI-бэкенда (`/agent`)
CJM-14: `/agent`
CJM-NEW: Переключение бэкенда (/agent)
```

With:

```text
CJM-16: Переключение CLI-бэкенда (`/agent`)
CJM-16: `/agent`
CJM-16: Переключение бэкенда (/agent)
```

Apply this to active specs under `dev/docs/specs/`, excluding historical files under `dev/docs/specs/realised/`.

- [x] **Step 3: Sync dependency graph ranges**

In `dev/docs/specs/module-dependency-graph.md`, update Telegram transport traceability so `/agent` is `CJM-16` and the `bot` module covers `CJM: 01–16`.

- [x] **Step 4: Verify no stale `/agent` numbering remains**

Run:

```bash
rg -n "CJM-14.*agent|CJM-14.*бэкенд|CJM-14.*backend|CJM-NEW" dev/docs/specs dev/docs/brd -g '*.md' -g '!realised/**' -g '!codex_support_spec_implementation_order.md'
```

Expected: no output.

- [x] **Step 5: Mark documentation phase complete**

Add a completion marker under sections `0.1` and `0.2` in `dev/docs/specs/codex_support_spec_implementation_order.md`:

```markdown
**Статус:** выполнено 2026-05-07.
**Проверка:** `rg -n "CJM-14.*agent|CJM-14.*бэкенд|CJM-14.*backend|CJM-NEW" dev/docs/specs dev/docs/brd -g '*.md' -g '!realised/**' -g '!codex_support_spec_implementation_order.md'` — нет совпадений.
```

Use `superpowers:verification-before-completion` before adding the marker.

**Статус:** выполнено 2026-05-07.
**Проверка:** `rg -n "CJM-14.*agent|CJM-14.*бэкенд|CJM-14.*backend|CJM-NEW" dev/docs/specs dev/docs/brd -g '*.md' -g '!realised/**' -g '!codex_support_spec_implementation_order.md'` — нет совпадений.
**Артефакты:** `dev/docs/brd/brd-user-journeys.md`, `dev/docs/specs/module-dependency-graph.md`, active backend-aware specs, `dev/docs/specs/codex_support_spec_implementation_order.md`.

---

### Task 2: Common Backend Contract

**Files:**
- Create: `src/claude_manager/coding_agent_backend.py`
- Create: `tests/test_coding_agent_backend.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/coding_agent_backend_spec.md` to `dev/docs/specs/realised/coding_agent_backend_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing contract tests**

Create tests for:
- `BackendName.CLAUDE.value == "claude"` and `BackendName.CODEX.value == "codex"`.
- Frozen DTOs: `SessionFileInfo`, `SessionMessage`, `SessionFileSnapshot`, `SessionUnreadState`, `StopSignalStep`, `StopStrategy`.
- `TerminalStatus.SUCCESS.value == "success"` and `TerminalStatus.FAILED.value == "failed"`.
- `CodingAgentBackend` cannot be instantiated directly.
- A subclass missing abstract methods raises `TypeError`.
- `UnknownBackendError` includes the invalid backend value and available backend names.
- `get_backend("not_a_backend")` raises `UnknownBackendError`.

Run:

```bash
python -m pytest tests/test_coding_agent_backend.py -q
```

Expected: fail because `claude_manager.coding_agent_backend` does not exist.

- [x] **Step 3: Implement the minimal contract module**

Implement exactly the API from `dev/docs/specs/coding_agent_backend_spec.md`:
- `BackendName`
- `UnifiedEvent`
- DTO dataclasses
- `TerminalStatus`
- `CodingAgentBackend`
- `BackendError`
- `BackendBinaryNotFoundError`
- `BackendProtocolError`
- `UnknownBackendError`
- `_INSTANCES_CACHE`
- `_create_backend_instance`
- `get_backend`
- `get_all_backends`

Keep concrete backend imports lazy inside `_create_backend_instance`.

- [x] **Step 4: Verify module tests pass**

Run:

```bash
python -m pytest tests/test_coding_agent_backend.py -q
```

Expected: pass.

- [x] **Step 5: Verify no upper-layer imports leaked into the contract**

Run:

```bash
python - <<'PY'
from pathlib import Path
text = Path("src/claude_manager/coding_agent_backend.py").read_text()
for forbidden in ("bot", "process_manager", "session_manager"):
    assert f"claude_manager.{forbidden}" not in text
    assert f"from . import {forbidden}" not in text
print("contract import check passed")
PY
```

Expected: `contract import check passed`.

- [x] **Step 6: Run regression tests**

Run:

```bash
python -m pytest tests/ -q
```

Expected: pass.

- [x] **Step 7: Mark spec complete**

Use `superpowers:verification-before-completion`. Then:
- Move `dev/docs/specs/coding_agent_backend_spec.md` to `dev/docs/specs/realised/coding_agent_backend_spec.md`.
- Add a completion marker under section `1.1` in `dev/docs/specs/codex_support_spec_implementation_order.md` with exact verification commands and results.
- Mark this task checkbox complete in this plan.

**Статус:** выполнено 2026-05-07.
**RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_coding_agent_backend.py -q` — failed with `ModuleNotFoundError: No module named 'claude_manager.coding_agent_backend'`.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_coding_agent_backend.py -q` — 12 passed.
**Проверка:** `~/.venvs/claude-manager/bin/python - <<'PY' ... PY` — `contract import check passed`.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 933 passed, 3 warnings.
**Артефакты:** `src/claude_manager/coding_agent_backend.py`, `tests/test_coding_agent_backend.py`, moved spec to `dev/docs/specs/realised/coding_agent_backend_spec.md`.

---

### Task 3: Claude Code Backend Adapter

**Files:**
- Create: `src/claude_manager/claude_code_backend.py`
- Create: `tests/test_claude_code_backend.py`
- Modify: `tests/integration/test_claude_cli_contract.py`
- Modify: `tests/test_coding_agent_backend.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/claude_code_backend_spec.md` to `dev/docs/specs/realised/claude_code_backend_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing Claude adapter tests**

Cover the test plan from `dev/docs/specs/claude_code_backend_spec.md`:
- command args for new and resumed Claude sessions;
- stdin JSONL encoding;
- stdout stream-json parsing;
- assistant text, progress text, session id, terminal status, and error event extraction;
- Claude session directory resolution;
- JSONL session-file message parsing;
- `read_session_file_snapshot`;
- `get_stop_strategy` returns SIGTERM then SIGKILL;
- factory positive path: `get_backend(BackendName.CLAUDE)` returns a singleton `ClaudeCodeBackend`.

Run:

```bash
python -m pytest tests/test_claude_code_backend.py tests/test_coding_agent_backend.py -q
```

Expected: fail because `claude_manager.claude_code_backend` does not exist.

- [x] **Step 3: Implement Claude adapter**

Move Claude-specific command composition, stdin encoding, stdout parsing, session-file reading, and stop strategy into `ClaudeCodeBackend`. Preserve current Claude behavior exactly unless the spec explicitly changes it.

- [x] **Step 4: Add or update real CLI contract test**

The contract test must:
- use the real Claude binary when available;
- skip with an explicit reason when the binary is unavailable;
- verify stream-json stdout and session-file parsing.

Run:

```bash
python -m pytest tests/test_claude_code_backend.py tests/test_coding_agent_backend.py tests/integration/test_claude_cli_contract.py -q
```

Expected: pass or explicit CLI skip.

- [x] **Step 5: Run regression tests**

Run:

```bash
python -m pytest tests/ -q
```

Expected: pass.

- [x] **Step 6: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the Claude backend spec to `realised/`, mark section `2.1` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_claude_code_backend.py tests/test_coding_agent_backend.py -q` — failed with `ModuleNotFoundError: No module named 'claude_manager.claude_code_backend'`.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_claude_code_backend.py tests/test_coding_agent_backend.py tests/integration/test_claude_cli_contract.py -q` — 31 passed.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 951 passed, 3 warnings.
**Артефакты:** `src/claude_manager/claude_code_backend.py`, `src/claude_manager/claude_code_session_file_reader.py`, `src/claude_manager/claude_code_session_path.py`, `tests/test_claude_code_backend.py`, `tests/integration/test_claude_cli_contract.py`, moved spec to `dev/docs/specs/realised/claude_code_backend_spec.md`.

---

### Task 4: Codex Backend Adapter

**Files:**
- Create: `src/claude_manager/codex_backend.py`
- Create: `tests/test_codex_backend.py`
- Create: `tests/integration/test_codex_cli_contract.py`
- Modify: `tests/test_coding_agent_backend.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/codex_backend_spec.md` to `dev/docs/specs/realised/codex_backend_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing Codex adapter tests**

Cover the test plan from `dev/docs/specs/codex_backend_spec.md`:
- `codex exec` command args for new sessions;
- `codex exec resume` command args for existing sessions;
- `--json`;
- `--dangerously-bypass-approvals-and-sandbox`;
- `--skip-git-repo-check`;
- empty stdin;
- stdout JSON event parsing;
- `response_item` assistant text extraction;
- `turn.failed` error handling;
- JSONL rollout file discovery under `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`;
- `read_session_file_snapshot`;
- terminal record detection;
- SIGINT, SIGTERM, SIGKILL stop strategy;
- factory positive path: `get_backend(BackendName.CODEX)` returns a singleton `CodexBackend`.

Run:

```bash
python -m pytest tests/test_codex_backend.py tests/test_coding_agent_backend.py -q
```

Expected: fail because `claude_manager.codex_backend` does not exist.

- [x] **Step 3: Implement Codex adapter**

Implement the adapter strictly behind `CodingAgentBackend`. Do not add Codex-specific branches to `process_manager`, `session_watcher`, `bot.py`, or `claude_interaction.py` in this task.

- [x] **Step 4: Add real Codex CLI contract tests**

The contract test must:
- use Codex CLI v0.128.0 when available;
- skip with an explicit reason when the binary is unavailable or the version is not the required one;
- verify `codex exec`, `codex exec resume`, stdout `--json`, JSONL session files, prompt-text image path behavior through `view_image`, and SIGINT stop behavior.

Run:

```bash
python -m pytest tests/test_codex_backend.py tests/test_coding_agent_backend.py tests/integration/test_codex_cli_contract.py -q
```

Expected: pass or explicit CLI skip.

- [x] **Step 5: Run regression tests**

Run:

```bash
python -m pytest tests/ -q
```

Expected: pass.

- [x] **Step 6: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the Codex backend spec to `realised/`, mark section `2.2` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_codex_backend.py tests/test_coding_agent_backend.py -q` — падение на `ModuleNotFoundError: No module named 'claude_manager.codex_backend'`.
**Проверка unit:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_codex_backend.py tests/test_coding_agent_backend.py -q` — 33 passed.
**Проверка contract:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_codex_backend.py tests/test_coding_agent_backend.py tests/integration/test_codex_cli_contract.py -q` — 36 passed, 1 skipped.
**Проверка regression:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 975 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/codex_backend.py`, `src/claude_manager/codex_session_file_reader.py`, `src/claude_manager/codex_session_file_listing.py`, `tests/test_codex_backend.py`, `tests/integration/test_codex_cli_contract.py`, `dev/docs/specs/realised/codex_backend_spec.md`.

---

### Task 5: Current Backend Registry

**Files:**
- Create: `src/claude_manager/current_backend_registry.py`
- Create: `tests/test_current_backend_registry.py`
- Modify: `src/claude_manager/config.py`
- Modify: `tests/test_config.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/current_backend_registry_spec.md` to `dev/docs/specs/realised/current_backend_registry_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing registry tests**

Cover:
- default state loads as `BackendName.CLAUDE` when file is absent;
- JSON format `{"backend": "claude"}` and `{"backend": "codex"}`;
- legacy plain-text migration from `claude` and `codex`;
- invalid JSON fallback behavior from the spec;
- `set_current` updates memory only after successful atomic write;
- failed load blocks later `set_current` with `RuntimeError`;
- `config.CURRENT_BACKEND_FILE` points to the expected state file path.

Run:

```bash
python -m pytest tests/test_current_backend_registry.py tests/test_config.py -q
```

Expected: fail because `current_backend_registry` and `CURRENT_BACKEND_FILE` do not exist.

- [x] **Step 3: Implement registry and config constant**

Implement exactly the persistence and atomic-write behavior from `current_backend_registry_spec.md`.

- [x] **Step 4: Verify targeted and regression tests**

Run:

```bash
python -m pytest tests/test_current_backend_registry.py tests/test_config.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

- [x] **Step 5: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the registry spec to `realised/`, mark section `2.3` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_current_backend_registry.py tests/test_config.py -q` — падение на `ImportError: cannot import name 'current_backend_registry'`.
**Проверка targeted:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_current_backend_registry.py tests/test_config.py -q` — 53 passed.
**Проверка regression:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 989 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/current_backend_registry.py`, `src/claude_manager/config.py`, `tests/test_current_backend_registry.py`, `tests/test_config.py`, `dev/docs/specs/realised/current_backend_registry_spec.md`.

---

### Task 6: Backend-Aware Claude Runner Wrapper

**Files:**
- Modify: `src/claude_manager/claude_runner.py`
- Modify: `tests/test_claude_runner.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing runner tests**

Cover:
- `start_subprocess_for_backend(...)` calls `backend.compose_subprocess_command_args(...)`;
- stdin bytes come only from `backend.encode_user_message_for_cli_stdin(...)`;
- runner passes cwd to subprocess;
- legacy Claude runner behavior remains available only as compatibility wrapper until consumers migrate.

Run:

```bash
python -m pytest tests/test_claude_runner.py -q
```

Expected: fail because `start_subprocess_for_backend` does not exist.

- [x] **Step 3: Implement the wrapper**

Add the thin subprocess wrapper without moving consumer logic yet. Do not add Codex-specific parsing to `claude_runner.py`.

- [x] **Step 4: Verify tests**

Run:

```bash
python -m pytest tests/test_claude_runner.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

- [x] **Step 5: Mark section complete**

Use `superpowers:verification-before-completion`. Mark section `2.4` in the order document complete and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_claude_runner.py -q` — падение на `ImportError: cannot import name 'BackendSubprocess'`.
**Проверка targeted:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_claude_runner.py -q` — 33 passed.
**Проверка regression:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 992 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/claude_runner.py`, `tests/test_claude_runner.py`.

---

### Task 7: Backend-Aware Daily Session Registry

**Files:**
- Modify: `src/claude_manager/daily_session_registry.py`
- Modify: `tests/test_daily_session_registry.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/daily_session_registry_spec.md` to `dev/docs/specs/realised/daily_session_registry_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing daily registry tests**

Cover:
- old format `number -> "uuid"` migrates to `DailySessionEntry(session_id, BackendName.CLAUDE)`;
- new format stores `{"session_id": "...", "backend": "claude" | "codex"}`;
- `register_session(session_id, backend)` is idempotent by pair;
- same `session_id` under different backends can have distinct ownership;
- `lookup_by_number` returns `DailySessionEntry`;
- orphan cleanup calls `backend.session_file_exists_for_project(...)`.

Run:

```bash
python -m pytest tests/test_daily_session_registry.py -q
```

Expected: fail because the registry still returns bare session IDs.

- [x] **Step 3: Implement backend-aware registry**

Implement `DailySessionEntry` and migrate read/write paths while preserving existing safety guards around failed disk loads.

- [x] **Step 4: Verify tests**

Run:

```bash
python -m pytest tests/test_daily_session_registry.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

- [x] **Step 5: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the daily registry spec to `realised/`, mark section `3.1` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_daily_session_registry.py -q` — падение на `ImportError: cannot import name 'DailySessionEntry'`.
**Проверка targeted:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_daily_session_registry.py -q` — 43 passed.
**Проверка integration:** `~/.venvs/claude-manager/bin/python -m pytest tests/integration/test_concurrent_access.py::TestConcurrentRegistration::test_concurrent_register_file_not_corrupted tests/integration/test_session_lifecycle.py::TestFilePersistence::test_registry_survives_reload -q` — 2 passed.
**Проверка regression:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 999 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/daily_session_registry.py`, `tests/test_daily_session_registry.py`, `tests/integration/test_concurrent_access.py`, `tests/integration/test_session_lifecycle.py`, `dev/docs/specs/realised/daily_session_registry_spec.md`.

---

### Task 8: Backend-Aware Session Manager

**Files:**
- Modify: `src/claude_manager/session_manager.py`
- Modify: `tests/test_session_manager.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/session_manager_spec.md` to `dev/docs/specs/realised/session_manager_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing session manager tests**

Cover:
- `sessions.json` migrates from `{"chat_id": "uuid"}` to `{"chat_id": {"session_id": "uuid", "backend": "claude"}}`;
- active binding uses `ActiveSession(session_id, backend)`;
- `create_new_session(chat_id, backend)` registers temp-id ownership with explicit backend;
- `switch_to_session` uses backend from `DailySessionEntry`;
- `find_chat_by_session_id(session_id, backend)` searches by pair;
- `/N` path does not read `current_backend_registry`.

Run:

```bash
python -m pytest tests/test_session_manager.py -q
```

Expected: fail because active sessions are still bare session IDs.

- [x] **Step 3: Implement backend-aware session manager**

Preserve existing load-protection behavior and update public return types according to `session_manager_spec.md`.

- [x] **Step 4: Verify tests**

Run:

```bash
python -m pytest tests/test_session_manager.py tests/test_daily_session_registry.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

- [x] **Step 5: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the session manager spec to `realised/`, mark section `3.2` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_session_manager.py -q` — падение на `ImportError: cannot import name 'ActiveSession'`.
**Проверка targeted:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_session_manager.py -q` — 49 passed.
**Проверка dependency:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_session_manager.py tests/test_daily_session_registry.py -q` — 92 passed.
**Проверка failed-regression-fix:** `~/.venvs/claude-manager/bin/python -m pytest tests/integration/test_e2e_user_isolation.py::TestE2eUserIsolation::test_e2e_user_gets_own_session_notifications tests/integration/test_session_lifecycle.py::TestFilePersistence::test_bindings_survive_reload -q` — 2 passed.
**Проверка regression:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 1008 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/session_manager.py`, `tests/test_session_manager.py`, `tests/integration/test_session_lifecycle.py`, `dev/docs/specs/realised/session_manager_spec.md`.

---

### Task 9: Backend-Aware Unread Buffer

**Files:**
- Modify: `src/claude_manager/unread_buffer.py`
- Modify: `tests/test_unread_buffer.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/unread_buffer_spec.md` to `dev/docs/specs/realised/unread_buffer_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing unread-buffer tests**

Cover:
- identical `session_id` values under Claude and Codex store two independent entries;
- snapshot state uses `SessionUnreadState`;
- TTL expiry clears only the matching `(session_id, backend)` entry;
- explicit clear works by pair;
- module does not import `session_reader` or read JSONL files directly.

Run:

```bash
python -m pytest tests/test_unread_buffer.py -q
```

Expected: fail because keys are still session-id only.

- [x] **Step 3: Implement backend-aware unread buffer**

Keep the module thin: no session-file parsing and no dependency on watcher internals.

- [x] **Step 4: Verify tests**

Run:

```bash
python -m pytest tests/test_unread_buffer.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

- [x] **Step 5: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the unread buffer spec to `realised/`, mark section `3.3` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_unread_buffer.py -q` — падение на `ImportError: cannot import name 'SessionUnreadSnapshot'`.
**Проверка targeted:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_unread_buffer.py -q` — 14 passed.
**Проверка compatibility:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_project_manager.py tests/integration/test_project_switching.py tests/test_unread_buffer.py -q` — 47 passed.
**Проверка regression:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 990 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/unread_buffer.py`, `tests/test_unread_buffer.py`, `dev/docs/specs/realised/unread_buffer_spec.md`.

---

### Task 10: Backend-Aware Process Lifecycle

**Files:**
- Modify: `src/claude_manager/process_manager.py`
- Modify: `tests/test_process_manager.py`
- Modify: `tests/test_stop_triggers_retry_blackbox.py`
- Modify: `tests/test_stop_triggers_retry_whitebox.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/process_manager_spec.md` to `dev/docs/specs/realised/process_manager_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing process-manager tests**

Cover:
- `_processes`, `_busy_flags`, and `_stop_events` are keyed by `(session_id, BackendName)`;
- `send_message(..., backend=...)` captures backend at turn start;
- retry loop reuses the captured backend;
- temp-to-real remap preserves backend;
- `stop_process(session_id, backend)` applies `backend.get_stop_strategy()`;
- Codex failed turns retry from `TerminalStatus.FAILED`, not from empty text.

Run:

```bash
python -m pytest tests/test_process_manager.py tests/test_stop_triggers_retry_blackbox.py tests/test_stop_triggers_retry_whitebox.py -q
```

Expected: fail because process state is still session-id only and stop strategy is not backend-specific.

**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_process_manager.py -q -k "codex_failed_turn_retries_from_terminal_status or send_message_with_codex_backend_uses_backend_contract or send_message_rejects_missing_backend"` — 3 failed, 68 deselected: `send_message()` ещё не принимал `backend`.

- [x] **Step 3: Implement backend-aware process lifecycle**

Route all CLI-specific behavior through `CodingAgentBackend` and `claude_runner.start_subprocess_for_backend(...)`.

- [x] **Step 4: Verify tests**

Run:

```bash
python -m pytest tests/test_process_manager.py tests/test_stop_triggers_retry_blackbox.py tests/test_stop_triggers_retry_whitebox.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

- [x] **Step 5: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the process manager spec to `realised/`, mark section `4.1` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_process_manager.py -q -k "codex_failed_turn_retries_from_terminal_status or send_message_with_codex_backend_uses_backend_contract or send_message_rejects_missing_backend"` — 3 passed, 68 deselected.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_process_manager.py tests/test_stop_triggers_retry_blackbox.py tests/test_stop_triggers_retry_whitebox.py -q` — 87 passed.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/integration/test_claude_cli_contract.py tests/integration/test_codex_cli_contract.py -q` — 5 passed, 1 skipped.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 997 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/process_manager.py`, `tests/test_process_manager.py`, `tests/integration/test_codex_cli_contract.py`, `dev/docs/specs/realised/process_manager_spec.md`, `dev/docs/specs/realised/process_manager_claude_only_spec.md`.

---

### Task 11: Backend-Aware Session Watcher

**Files:**
- Modify: `src/claude_manager/session_watcher.py`
- Modify: `tests/test_session_watcher.py`
- Modify: `tests/integration/test_watcher_handler_coordination.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/session_watcher_spec.md` to `dev/docs/specs/realised/session_watcher_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing watcher tests**

Cover:
- two independent watcher instances, one per backend;
- `pause_session`, `resume_session`, and `update_session_id` accept backend;
- watcher reads files through `backend.list_all_session_files_for_project` and `backend.read_session_file_snapshot`;
- owner lookup uses `session_manager.find_chat_by_session_id(session_id, backend)`;
- buffer-and-hold does not deliver the last assistant text as final before terminal record.

Run:

```bash
python -m pytest tests/test_session_watcher.py tests/integration/test_watcher_handler_coordination.py -q
```

Expected: fail because watcher is still Claude-only.

**RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_session_watcher.py tests/integration/test_watcher_handler_coordination.py -q` — 10 failed, 4 passed. Основные причины: в модуле ещё не было `SessionWatcher` и backend factory.

- [x] **Step 3: Implement backend-aware watcher**

Keep the existing facade names where possible, but make the internal state and callbacks backend-aware.

**Implemented:** `src/claude_manager/session_watcher.py` now has one `SessionWatcher` per `BackendName`, reads via `CodingAgentBackend`, stores cursors per backend, routes pause/resume/update by backend, and uses buffer-and-hold for the last assistant message while a turn is active.

- [x] **Step 4: Verify tests**

Run:

```bash
python -m pytest tests/test_session_watcher.py tests/integration/test_watcher_handler_coordination.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

**GREEN:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_session_watcher.py tests/integration/test_watcher_handler_coordination.py -q` — 14 passed.
**Dependency gate:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_session_watcher.py tests/integration/test_watcher_handler_coordination.py tests/integration/test_e2e_user_isolation.py tests/integration/test_project_switching.py tests/test_project_manager.py tests/test_claude_interaction.py -q` — 98 passed.
**Post-init isolation gate:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_bot.py tests/test_session_manager.py -q` — 119 passed.
**Full gate:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 934 passed, 1 skipped, 3 warnings.

- [x] **Step 5: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the watcher spec to `realised/`, mark section `5.1` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Артефакты:** `src/claude_manager/session_watcher.py`, `tests/test_session_watcher.py`, `tests/integration/test_watcher_handler_coordination.py`, `tests/integration/test_e2e_user_isolation.py`, `tests/integration/test_project_switching.py`, `tests/test_bot.py`, `dev/docs/specs/realised/session_watcher_spec.md`, `dev/docs/specs/realised/session_watcher_claude_only_spec.md`.

---

### Task 12: Backend-Aware Project Switching

**Files:**
- Modify: `src/claude_manager/project_manager.py`
- Modify: `tests/test_project_manager.py`
- Modify: `tests/integration/test_project_switching.py`
- Modify: `tests/e2e/test_project_switching.py` if E2E coverage needs backend assertions
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/project_manager_spec.md` to `dev/docs/specs/realised/project_manager_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing project-switching tests**

Cover:
- `current_backend_registry` is not reset during project switching;
- watcher snapshots are saved for both backends;
- pending delivery includes backend;
- `pause_all` and `resume_all` cover all watcher instances;
- switching projects does not kill running processes.

Run:

```bash
python -m pytest tests/test_project_manager.py tests/integration/test_project_switching.py -q
```

Expected: fail because pending and watcher snapshots are not fully backend-aware.

- [x] **Step 3: Implement project switching changes**

Preserve the existing pause/reset/resume safety pattern and extend it to all backend-aware state.

- [x] **Step 4: Verify tests**

Run:

```bash
python -m pytest tests/test_project_manager.py tests/integration/test_project_switching.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

- [x] **Step 5: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the project manager spec to `realised/`, mark section `5.2` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_project_manager.py tests/integration/test_project_switching.py -q` — 6 failed, 34 passed: старый `project_manager` ещё сохранял legacy snapshot без backend-а, не собирал backend-aware pending delivery и не имел `resolve_neighbor_project`.
**Проверка GREEN:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_project_manager.py tests/integration/test_project_switching.py -q` — 40 passed.
**Проверка full suite:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 940 passed, 2 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/project_manager.py`, `tests/test_project_manager.py`, `tests/integration/test_project_switching.py`, `dev/docs/specs/realised/project_manager_spec.md`.

---

### Task 13: Telegram-Facing Backend Integration

**Files:**
- Modify: `src/claude_manager/bot.py`
- Modify: `src/claude_manager/claude_interaction.py`
- Modify: `src/claude_manager/claude_runner.py`
- Modify: `src/claude_manager/main.py`
- Modify: `tests/test_bot.py`
- Modify: `tests/test_claude_interaction.py`
- Modify: `tests/test_claude_runner.py`
- Modify: `tests/test_main.py`
- Modify: `tests/integration/test_message_path.py`
- Modify: `tests/integration/test_session_lifecycle.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/telegram_agent_backend_integration_spec.md` to `dev/docs/specs/realised/telegram_agent_backend_integration_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing Telegram integration tests**

Cover:
- `/new` reads `current_backend_registry.get_current()` and passes backend to `session_manager.create_new_session`;
- normal messages read backend from `session_manager.get_active_session`;
- `/sessions` merges all backend sessions and limits to 15 after merge;
- `/N` uses backend from `SwitchResult`;
- `/stop` calls `process_manager.stop_process(session_id, backend)`;
- watcher callback and pending delivery carry backend through to `send_response`;
- `main.post_init` loads `current_backend_registry`.

Run:

```bash
python -m pytest tests/test_bot.py tests/test_claude_interaction.py tests/test_main.py tests/integration/test_message_path.py tests/integration/test_session_lifecycle.py -q
```

Expected: fail because Telegram-facing code still assumes Claude-only session state.

- [x] **Step 3: Implement backend integration without `/agent` UI**

Wire the lower layers together. Do not add the `/agent` command in this task; the lower stack must be backend-aware before the UI switch appears.

- [x] **Step 4: Verify tests**

Run:

```bash
python -m pytest tests/test_bot.py tests/test_claude_interaction.py tests/test_main.py tests/integration/test_message_path.py tests/integration/test_session_lifecycle.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

- [x] **Step 5: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the Telegram integration spec to `realised/`, mark section `6.1` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Примечание RED:** при продолжении этой задачи Telegram-facing код и тесты уже были внесены в рабочее дерево, поэтому RED-фаза заново не воспроизводилась; текущая targeted-проверка сразу прошла.
**Проверка targeted:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_bot.py tests/test_claude_interaction.py tests/test_main.py tests/integration/test_message_path.py tests/integration/test_session_lifecycle.py -q` — 163 passed.
**Диагностика full suite:** первый полный прогон показал один устаревший whitebox-expectation в `tests/test_stop_triggers_retry_whitebox.py`; текущая версия теста уже ожидает backend-aware вызов, точечный повтор `~/.venvs/claude-manager/bin/python -m pytest tests/test_stop_triggers_retry_whitebox.py::TestDev3HandleStopDuringRetry::test_stop_process_called_when_busy_but_no_process -q` — 1 passed.
**Проверка full suite:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 947 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/bot.py`, `src/claude_manager/claude_interaction.py`, `src/claude_manager/claude_runner.py`, `src/claude_manager/main.py`, `tests/test_bot.py`, `tests/test_claude_interaction.py`, `tests/test_main.py`, `tests/integration/test_message_path.py`, `tests/integration/test_session_lifecycle.py`, moved `dev/docs/specs/telegram_agent_backend_integration_spec.md` to `dev/docs/specs/realised/telegram_agent_backend_integration_spec.md`.

---

### Task 14: `/agent` User Journey

**Files:**
- Modify: `src/claude_manager/bot.py`
- Modify: `tests/test_bot.py`
- Modify: `tests/integration/test_session_lifecycle.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Move after gate: `dev/docs/specs/agent_backend_selection_user_journey_spec.md` to `dev/docs/specs/realised/agent_backend_selection_user_journey_spec.md`

- [x] **Step 1: Invoke TDD**

Use `superpowers:test-driven-development`.

- [x] **Step 2: Write failing `/agent` tests**

Cover:
- `/agent` shows current backend;
- inline keyboard marks the current backend with `✓`;
- callback data is `agent:claude` and `agent:codex`;
- selecting Codex calls `current_backend_registry.set_current(BackendName.CODEX)`;
- selecting the already-current backend does not write the file;
- active session remains unchanged;
- confirmation mentions the active session backend when one exists;
- confirmation omits the active session line when none exists;
- `RuntimeError` and `OSError` from the registry are shown to the user;
- unknown callback value returns `Неизвестный агент`;
- unauthorized users are ignored.

Run:

```bash
python -m pytest tests/test_bot.py tests/integration/test_session_lifecycle.py -q
```

Expected: fail because `/agent` handlers do not exist.

- [x] **Step 3: Implement `/agent` and callback handler**

Add the command only after Tasks 1-13 are complete. The handler must not mutate active sessions.

- [x] **Step 4: Verify tests**

Run:

```bash
python -m pytest tests/test_bot.py tests/integration/test_session_lifecycle.py -q
python -m pytest tests/ -q
```

Expected: both commands pass.

- [x] **Step 5: Mark spec complete**

Use `superpowers:verification-before-completion`. Then move the `/agent` user journey spec to `realised/`, mark section `6.2` in the order document complete, and mark this task checkbox complete.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_bot.py tests/integration/test_session_lifecycle.py -q` — 9 failed, 86 passed: `handle_agent` и `handle_agent_callback` ещё отсутствовали.
**Проверка GREEN:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_bot.py tests/integration/test_session_lifecycle.py -q` — 99 passed.
**Проверка full suite:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 960 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/bot.py`, `tests/test_bot.py`, `tests/integration/test_session_lifecycle.py`, moved `dev/docs/specs/agent_backend_selection_user_journey_spec.md` to `dev/docs/specs/realised/agent_backend_selection_user_journey_spec.md`.

---

### Task 15: Cross-Cutting Verification And Final Status

**Files:**
- Create: `tests/e2e/test_agent_backend_selection.py`
- Modify: `tests/e2e/test_project_switching.py`
- Modify: `dev/docs/specs/codex_support_spec_implementation_order.md`
- Modify: `dev/docs/docs-index.md` if moved specs or new docs need index updates

- [x] **Step 1: Invoke verification skill**

Use `superpowers:verification-before-completion`.

- [x] **Step 2: Run unit and integration suite**

Run:

```bash
python -m pytest tests/ -q
```

Expected: pass.

- [x] **Step 3: Run CLI contract tests**

Run:

```bash
python -m pytest tests/integration/test_claude_cli_contract.py tests/integration/test_codex_cli_contract.py -q
```

Expected: pass, or explicit skips for unavailable binaries with clear reasons.

- [x] **Step 4: Run E2E scenarios when credentials are available**

Run:

```bash
python tests/e2e/run_e2e_tests.py
```

If the environment lacks Telegram E2E credentials, record the exact missing prerequisite instead of claiming E2E pass.

Expected covered scenarios:
- `/agent` shows current backend and switches to Codex;
- `/new` after Codex selection creates a Codex session;
- `/N` on an old Claude session still uses Claude after Codex selection;
- `/stop` uses the active backend stop strategy;
- `/all` delivers responses from both backends;
- `/projects` and `/pN` preserve pending messages with backend-aware delivery.

- [x] **Step 5: Mark order document complete**

After fresh verification, add final completion markers under:
- `7.1. Unit tests`
- `7.2. CLI contract tests`
- `7.3. E2E tests через Telegram`

Do not mark E2E complete if it was skipped for missing credentials; mark it as skipped with the exact reason.

- [ ] **Step 6: Finish the branch**

Use `superpowers:finishing-a-development-branch`. Present the required finish options after tests are verified.

**Статус:** частично выполнено, E2E blocked 2026-05-07.
**Проверка unit/integration:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 960 passed, 1 skipped, 3 warnings.
**Проверка CLI contract:** `~/.venvs/claude-manager/bin/python -m pytest tests/integration/test_claude_cli_contract.py tests/integration/test_codex_cli_contract.py -q` — 5 passed, 1 skipped.
**Проверка E2E prerequisites:** `.env` содержит `TELETHON_API_ID`, `TELETHON_API_HASH`, `TELETHON_PHONE`, `TELETHON_BOT_USERNAME`; файл `tests/e2e/telethon_test.session` есть.
**Проверка E2E `/agent`:** `~/.venvs/claude-manager/bin/python -m pytest tests/e2e/test_agent_backend_selection.py -q` — 4 failed: живой Telegram-бот не ответил на `/agent` текстом `Текущий агент` за 20 секунд. Вероятная причина: запущенный бот ещё не перезапущен на текущем рабочем дереве и не содержит новый `/agent` handler. E2E не отмечен complete до перезапуска бота на текущем коде.
**Артефакты E2E:** `tests/e2e/test_agent_backend_selection.py`, `tests/e2e/test_client.py`, `tests/e2e/test_project_switching.py`.

**Документаторская фиксация 2026-05-08:** основная реализация и targeted-проверки после E2E-стабилизации выполнены, но ветку нельзя закрывать до восстановления Git и повторной финальной проверки.
**Targeted-проверки:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_bot.py::TestHandleSwitchProject::test_delivered_pending_message_clears_unread_snapshot tests/test_bot.py::TestHandleSwitchProject::test_delivers_pending_messages_after_switch -q --tb=short` — 2 passed; `~/.venvs/claude-manager/bin/python -m pytest tests/test_project_manager.py::TestSwitchProject::test_pending_delivery_items_include_backend -q --tb=short` — 1 passed; `~/.venvs/claude-manager/bin/python -m pytest tests/e2e/test_file_delivery.py -q --tb=short` — 4 passed; combined targeted command for bot/project/file/session/project-switching checks — 9 passed, 1 skipped; final targeted E2E checks for FLOW-13 and FLOW-17 — 2 passed.
**Full old E2E:** inconclusive. Full command over `tests/e2e` excluding `tests/e2e/test_agent_backend_selection.py` reached the visible 100% progress point without failure lines, then hung while pytest wrote its cache; retry with `-p no:cacheprovider` skipped 24 tests because `tests/e2e/telethon_test.session` was locked by another Python process.
**Git state:** repository object database is broken: `.git/objects` is empty and `git log` reports that branch `codex-support-spec-implementation-cycle` has no commits. `git fetch origin` requested a GitHub username and was interrupted.
**Documentation gaps on disk:** `dev/docs/specs/codex_support_spec_implementation_order.md`, `dev/docs/session-reports/`, and `dev/docs/session-reports/08-05/` are absent, although `dev/docs/docs-index.md` still references the specs and session-report structure.
**Required next step:** repair Git state before commits, feature work, spec moves, or final branch completion. After Git repair, recreate or restore `dev/docs/specs/codex_support_spec_implementation_order.md`, create a session report under `dev/docs/session-reports/08-05/`, then rerun the final verification gates.

---

## Completion Marker Format

Use this format under the relevant subsection in `dev/docs/specs/codex_support_spec_implementation_order.md` only after fresh verification:

```markdown
**Статус:** выполнено 2026-05-07.
**Проверка:** `<command>` — `<observed result>`.
**Артефакты:** `<changed files or moved spec>`.
```

If a gate is skipped because a real CLI or Telegram credential is unavailable:

```markdown
**Статус:** код реализован, gate пропущен 2026-05-07.
**Причина пропуска:** `<exact missing binary, version mismatch, or missing credential>`.
**Проверка:** `<command>` — `<observed skip reason>`.
```

## Self-Review Checklist

- [ ] The `/agent` UI appears only after backend ownership, process lifecycle, watcher, unread buffer, and Telegram integration are backend-aware.
- [ ] Existing sessions never read `current_backend_registry` to choose their backend.
- [ ] All subprocess, stop, busy, owner lookup, watcher, and pending-delivery keys include backend.
- [ ] Codex terminal status is based on terminal events, not empty text.
- [ ] Specs move to `dev/docs/specs/realised/` only after fresh verification.
- [ ] The order document is updated only with evidence from commands run in the same completion step.
