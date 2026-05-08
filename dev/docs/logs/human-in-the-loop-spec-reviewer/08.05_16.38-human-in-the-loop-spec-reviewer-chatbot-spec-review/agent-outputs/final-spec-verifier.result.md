# final-spec-verifier result

## Verdict

Final verification is complete, but I cannot confirm absence of blocker/high risks.

There are no blocker findings. There are three high findings in cross-spec consistency. The high risks are not in the explicitly requested project_manager/process_manager checks themselves; they are in parent and adjacent realised specs that still describe older contracts.

## Confirmed checks

- `project_manager_spec.md` does not require Telegram commands `/next` and `/prev` as current UI. It keeps `resolve_neighbor_project(direction)` as internal/future API only.
- `project_manager_spec.md` pending delivery registers each pending item with `daily_session_registry.register_session(item.session_id, item.backend)` and sends it with `send_response(chat_id, item.text, day_number, item.backend, is_final=item.is_final)`.
- `08.05_16.38-backend-aware-process_manager_spec.md` does not require fallback from `backend=None` to `current_backend_registry`. It states that backend-aware `send_message` requires an explicit backend and treats `backend=None` as a contract error.
- Storage specs document compatibility wrappers where code preserves them:
  - `daily_session_registry`: `get_session_id_by_number`
  - `session_manager`: `bind_session`, `unbind_session`, `get_bound_session`, `get_chat_id_for_session`
  - `unread_buffer`: old project-path helpers as no-op compatibility wrappers
- `telegram_agent_backend_integration_spec.md` explicitly allows legacy `ClaudeProcess` / `start_process` as compatibility debt and restricts the no-Claude-specific-parsing rule to the new backend-aware `start_subprocess_for_backend` path.
- Backend-aware specs were added under `dev/docs/specs/realised/` without deleting the older Claude-only realised specs; both old and new files are present.

## High findings

### finding-final-001

Parent/backend selection specs still describe an implicit process_manager fallback to `current_backend_registry` for `session_id=None` or missing backend. This contradicts the accepted explicit-backend process_manager contract and the current code/tests.

Recommended fix: update `coding_agent_backend_spec.md` and `current_backend_registry_spec.md` so the upper layer chooses the current backend for new sessions, then passes explicit `backend` and temp `session_id` into `process_manager`.

### finding-final-002

Operational flows are still documented with `list_session_files_for_project`, the recent UI listing API, while code and tests use `list_all_session_files_for_project` for watcher and pending delivery. The recent API is capped, so using it for operational flows can lose messages outside the UI window.

Recommended fix: document both APIs in parent and concrete backend specs. Keep `list_session_files_for_project` for `/sessions`; use `list_all_session_files_for_project` for watcher, pending delivery, reset, and other operational scans.

### finding-final-003

`process_manager_spec.md` still contains an obsolete `claude_runner` dependency section: it says backend-aware `start_process(args, cwd, stdin_bytes)` exists and `ClaudeProcess.terminate()` is removed. The code and integration spec use `start_subprocess_for_backend` for the new path and keep legacy `ClaudeProcess` / `start_process` / `terminate` as compatibility debt.

Recommended fix: align the process_manager spec dependency section with `telegram_agent_backend_integration_spec.md` and current `claude_runner.py`.

## Test Evidence

The orchestrator-provided test run was:

```text
~/.venvs/claude-manager/bin/python -m pytest tests/ -q
961 passed, 1 skipped, 3 warnings
```

I did not rerun the full test suite in this verifier pass. I verified the high findings by reading the listed specs, source files, and targeted tests.

## Conclusion

The moved specs are close, and the user-requested corrections for `/next`/`/prev`, pending backend delivery, explicit process_manager backend, storage wrappers, and legacy runner allowance are present. However, the three high cross-spec contradictions above must be fixed before these specs can be treated as fully realised without blocker/high implementation risk.
