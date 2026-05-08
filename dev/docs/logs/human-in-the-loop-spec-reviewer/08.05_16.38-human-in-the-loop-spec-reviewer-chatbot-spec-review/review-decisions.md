# Review decisions

Task: chatbot spec review after restored specification files.

## finding-001 — project_manager_spec promises /next and /prev without Telegram wiring

Decision: do not implement `/next` and `/prev`.

Interpretation: user rejected the recommended implementation option. Current review path should remove or explicitly defer these commands from the current ready scope instead of adding Telegram handlers.

Decision source: user response "Это делать не надо".

Applied change: `dev/docs/specs/project_manager_spec.md` now states that `/next` and `/prev` are not part of the current user-facing contract. The internal `resolve_neighbor_project(direction)` API remains documented and tested.

## finding-002 — process_manager_spec backend fallback mismatch

Decision: align spec to implemented code.

Decision source: user response "давай" after the recommended option was presented.

Applied change: `dev/docs/specs/process_manager_spec.md` now states that backend-aware `send_message` requires an explicit `BackendName`. `current_backend_registry` is read by upper layers for new sessions and is not used as a fallback inside `process_manager`.

## finding-003 — project_manager_spec pending delivery backend argument

Decision: fix the example in `project_manager_spec.md`.

Decision source: user response "Давай первое".

Applied change: pending delivery now calls `send_response(chat_id, item.text, day_number, item.backend, is_final=item.is_final)`.

## finding-004 — storage specs compatibility APIs

Decision: document compatibility wrappers.

Decision source: user response "прими все рекомендованые тобой предложения".

Applied changes:
- `daily_session_registry_spec.md` documents `get_session_id_by_number` as a compatibility wrapper over `lookup_by_number`.
- `session_manager_spec.md` documents `bind_session`, `unbind_session`, `get_bound_session`, and `get_chat_id_for_session` as legacy compatibility wrappers.
- `unread_buffer_spec.md` documents `get_pending_messages`, `clear_snapshot`, and `has_pending` as compatibility no-op wrappers.

## finding-005 — telegram integration runner readiness criterion

Decision: narrow the readiness criterion.

Decision source: user response "прими все рекомендованые тобой предложения".

Applied change: `telegram_agent_backend_integration_spec.md` now requires only the new backend-aware runner path `start_subprocess_for_backend` to avoid Claude-specific parsing, while allowing legacy `ClaudeProcess` / `start_process` as explicit compatibility debt.

## finding-final-001 — parent specs still described process_manager fallback

Decision: accept the final verifier recommendation.

Decision source: user response "прими все рекомендованые тобой предложения".

Applied changes:
- `coding_agent_backend_spec.md` now states that the upper Telegram layer reads `current_backend_registry` for new sessions and passes explicit `backend` to `process_manager`.
- `current_backend_registry_spec.md` now states that `process_manager` does not import the registry and does not fallback from `backend=None`.

## finding-final-002 — operational scans used recent UI API in docs

Decision: accept the final verifier recommendation.

Decision source: user response "прими все рекомендованые тобой предложения".

Applied changes:
- `coding_agent_backend_spec.md` documents both APIs: `list_session_files_for_project` for `/sessions` UI and `list_all_session_files_for_project` for operational flows.
- `claude_code_backend_spec.md` and `codex_backend_spec.md` document `list_all_session_files_for_project`.
- `project_manager_spec.md` and `session_watcher_spec.md` now use `list_all_session_files_for_project` for pending delivery, watcher scans, reset state, and resume paths.

## finding-final-003 — process_manager_spec obsolete claude_runner contract

Decision: accept the final verifier recommendation.

Decision source: user response "прими все рекомендованые тобой предложения".

Applied change: `process_manager_spec.md` now documents `start_subprocess_for_backend` / `BackendSubprocess` as the new backend-aware path and treats `ClaudeProcess`, `start_process`, and `ClaudeProcess.terminate()` as allowed legacy compatibility debt.
