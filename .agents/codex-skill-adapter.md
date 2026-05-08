# Codex Skill Adapter

**Adapter version:** `codex-skill-adapter-v1`

This project is Claude-first, but managed Codex skills are generated full mirror runtime
copies. Claude source remains source of truth; generated mirrors are not hand-maintained.

## Runtime Terms

- **Agent tool** — use the available Codex agent delegation mechanism when the current
  runtime exposes it. If no delegation mechanism is available, stop and report
  `delegation_unavailable`; do not silently execute the agent step as ordinary prose.
- **Skill tool** — read and execute `.agents/skills/<skill-name>/SKILL.md` for available
  managed mirrors. Do not jump back to `.claude/skills/**` for runtime resources.
- **claude -p** — keep Claude CLI calls unchanged in generated mirrors. If the runtime
  cannot call Claude CLI, use a documented test fixture stub or report unsupported status.
- **/session-report** — map Claude slash-command behavior to the available session report
  mechanism, normally `session-change-documenter`, and state that Claude slash commands
  are not native Codex tools.
- **$ARGUMENTS** — replace deterministically from user input or explicit command arguments.
  If the value is missing, ask a question or fail input validation.

## Boundaries

- Adapter rules only map runtime tools and placeholders.
- Adapter rules do not change business logic, pipeline order, quality gates, artifacts, or tests.
- Project business/domain additions stay in `.claude/skill-extensions/`.
- Codex-specific operational rules live here or in root `AGENTS.md`, not inside generated mirrors.
