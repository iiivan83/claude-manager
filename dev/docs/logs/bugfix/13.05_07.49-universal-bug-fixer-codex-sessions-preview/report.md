# Codex Sessions Preview Bugfix

## Summary

The Telegram `/sessions` command showed Codex bootstrap instructions as session previews for the `reviews-analyzer` project.

The visible symptom was repeated text like `# AGENTS.md instructions for /Users/ivan/Desktop/claude-sandbox/reviews-analyzer <INSTRUCTIONS>...` instead of the first real user message.

## Evidence

- The screenshot in `/Users/ivan/Desktop/claude-sandbox/claude_manager/received_files/file_20260513_074738_llweom.jpg` showed repeated Codex session entries with `AGENTS.md instructions`.
- A live Codex rollout file for `reviews-analyzer` contained this order:
  - `session_meta`
  - `response_item` with role `developer`
  - `response_item` with role `user` and text starting with `# AGENTS.md instructions for ...`
  - `response_item` with role `user` and the real session text
- `src/claude_manager/codex_session_file_listing.py` selected the first `response_item` with role `user` without filtering the Codex bootstrap block.

## Root Cause

Codex stores the injected project instructions as a `user` message in rollout JSONL history. The session preview reader treated every `user` message as user-authored content, so the bootstrap block became the preview.

## Fix

`src/claude_manager/codex_session_file_listing.py` now skips Codex bootstrap user text when it:

- starts with `# AGENTS.md instructions for `
- contains `<INSTRUCTIONS>`

After skipping that block, preview selection continues to the first real user-authored message.

## Regression Test

Added `test_list_session_files_skips_codex_bootstrap_user_message` in `tests/test_codex_backend.py`.

The test creates a rollout file where the bootstrap `AGENTS.md instructions` user message appears before the real user message and asserts that the preview uses the real message.

## Verification

- `.venv/bin/python -m pytest tests/test_codex_backend.py::test_list_session_files_skips_codex_bootstrap_user_message -q` failed before the fix with the old `AGENTS.md instructions` preview.
- `.venv/bin/python -m pytest tests/test_codex_backend.py::test_list_session_files_skips_codex_bootstrap_user_message -q` passed after the fix.
- `.venv/bin/python -m pytest tests/test_codex_backend.py -q` passed: 21 tests.
- `.venv/bin/python -m pytest tests/test_bot.py::TestHandleSessions -q` passed: 3 tests.
- `.venv/bin/python -m pytest tests/ -q` passed: 966 tests, 1 skipped, 3 warnings from the Telegram library.
- A live check against `/Users/ivan/Desktop/claude-sandbox/reviews-analyzer` no longer returned `AGENTS.md instructions` in the first Codex previews.

## Notes

The strict `universal-bug-fixer` pre-flight asked for `architecture.md` in the `claude_manager` root, but that file is absent. The investigation used the architecture section in `CLAUDE.md` instead.
