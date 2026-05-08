# Local final verification

Status: pass.

The repeated external verifier process for `final-spec-verifier-rerun.*` hung and wrote no artifacts, so the orchestrator stopped it and performed local final checks against the same high-risk areas.

Confirmed:
- No old implicit-backend fallback text remains in the parent/current/process-manager specs.
- `project_manager_spec.md` and `08.05_16.38-backend-aware-session_watcher_spec.md` no longer call `list_session_files_for_project` for operational scans.
- `08.05_16.38-backend-aware-process_manager_spec.md` no longer describes `start_process(args, cwd, stdin_bytes)` or deletion of `ClaudeProcess.terminate()` as the backend-aware contract.
- `project_manager_spec.md` keeps `/next` and `/prev` outside the current user-facing contract.
- Pending delivery passes `item.backend` to `send_response`.
- `telegram_agent_backend_integration_spec.md` allows legacy `ClaudeProcess` / `start_process` as compatibility debt while requiring the new path to use `start_subprocess_for_backend`.

Verification commands:
- `rg` checks for implicit fallback, recent-list operational scans, and obsolete runner contract: no bad matches.
- `~/.venvs/claude-manager/bin/python -m pytest tests/test_codex_backend.py::test_list_all_session_files_ignores_recent_and_lookback_limits tests/test_process_manager.py::TestBackendAwareProcessState tests/test_project_manager.py tests/test_session_watcher.py -q`: 51 passed.
- `~/.venvs/claude-manager/bin/python -m json.tool` on orchestrator and findings artifacts: valid JSON.
