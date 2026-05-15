# All Projects Monitoring Design

## Goal

Add an `all` project mode that lets the user see assistant messages from every
available project while preventing message sending until the user enters a
specific project and session.

## User Requirements

- The `/projects` list includes an `all` mode.
- In `all` mode the user sees messages from all projects.
- Each all-mode message starts with a clickable command that contains both the
  project number and session number.
- The confirmed command format is `/<project_number>s<session_number>`, for
  example `/3s12 bloger`.
- Clicking that command switches the bot to project `3` and session `12`.
- A message delivered in `all` mode is not considered read for the target
  project. When the user switches into that project, the same message is
  delivered again through the normal pending-message path.
- In `all` mode the user cannot send text, photos, or documents to the agent.
  The bot must warn the user to enter a concrete project and session first.

## Recommended Approach

Use a separate all-project monitor instead of extending the existing
`session_watcher`.

The existing `session_watcher` owns normal project delivery state. Reusing it
for all-project mode would risk marking cross-project messages as read too
early. A separate monitor can keep its own delivery cursors, display messages
globally, and leave normal project unread state intact.

## Components

### `all_projects_monitor`

New module responsible for global monitoring.

Responsibilities:

- Track which chats are currently in all-project mode.
- Scan all projects returned by `project_manager.scan_available_projects()`.
- For each project, scan session files from all configured coding-agent
  backends.
- Keep independent all-mode cursors keyed by project path, session id, and
  backend.
- Build a link registry from `(project_number, session_number)` to the exact
  project path, session id, and backend.
- Deliver only new assistant messages to enabled all-mode chats.
- Preserve normal project unread delivery by writing an unread snapshot before
  displaying a message from all-mode.

### `bot.py`

Transport changes:

- `/projects` renders `/all all` as the first line.
- `/all` enables all-project mode and unbinds the current session.
- `handle_message`, `handle_photo`, `handle_document`, and `/new` block agent
  input while all-project mode is active.
- A new handler parses `/<project_number>s<session_number>` commands before the
  existing `/N` session handler.
- Clicking an all-mode link disables all-project mode, switches to the target
  project, binds the target session, and then delivers pending messages.
- If the all-mode link points to the already active project, pending messages
  are still collected and delivered because all-mode may have paused normal
  watcher state.

### `project_manager`

Expose a public wrapper for collecting pending messages for a project that is
already active.

This is needed when the user exits all-mode into the same project. The existing
`switch_project()` no-op path returns no pending messages for an already active
project, but all-mode can still have unread snapshots for that project.

## Message Format

All-mode messages use plain Telegram command text, not HTML-only links:

```text
/3s12 bloger
message text
```

Rules:

- `/3s12` is clickable in Telegram and routes to project `3`, session `12`.
- `bloger` is the project name.
- The backend label remains inside the delivered body only if existing message
  formatting already includes it elsewhere. The all-mode prefix stays compact
  and project-first.
- Long messages are still split through the existing message splitter.

## Unread Semantics

All-mode delivery must not advance normal project watcher cursors.

When all-mode detects a new assistant message:

1. Read the all-mode cursor for that project/session/backend.
2. If `unread_buffer` does not already contain a snapshot for that session and
   backend, save a snapshot using the previous all-mode cursor.
3. Send the message to all-mode chats.
4. Advance only the all-mode cursor.

When the user later switches into the project, existing pending-message logic
uses `unread_buffer` to replay the same message through normal project delivery.

## Error Handling

- If scanning one project or backend fails, log the error and continue scanning
  the rest.
- If enabling all-mode fails after pausing the normal watcher, resume the normal
  watcher before raising the error.
- If an all-mode link cannot be resolved from the in-memory link registry,
  fall back to switching by project number and session number through the
  current project registry after project switch.
- If the project number is invalid, show the existing invalid-project message.
- If the session number is invalid inside the target project, show a clear
  message naming the missing session and project.

## Testing

Use test-first implementation.

Required tests:

- Enabling all-mode pauses normal watcher state and marks the chat as all-mode.
- If all-mode enable fails, normal watcher state is resumed.
- All-mode polling delivers a new assistant message with project and session
  numbers.
- All-mode delivery creates an unread snapshot and does not overwrite an older
  unread snapshot.
- All-mode message formatting starts with `/<project_number>s<session_number>
  <project_name>`.
- `/projects` includes `/all all` and marks it when all-mode is active.
- Text input in all-mode is blocked with a warning that asks the user to enter a
  project and session.
- Photos, documents, and `/new` are also blocked in all-mode.
- `/<project_number>s<session_number>` switches project and binds the exact
  session.
- Exiting all-mode into the already active project still collects pending
  messages.

## Out of Scope

- Inline keyboards for all-mode messages.
- Persistent all-mode state across bot restarts.
- Changing existing `/N` session switching semantics inside a concrete project.
- Renaming or rewriting Claude-specific configuration.
