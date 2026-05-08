# AGENTS.md

## Canonical Project Instructions

This project is designed primarily for Claude Code.

Canonical files:
- `CLAUDE.md`
- `.claude/skills/**/SKILL.md`
- docs referenced from those files

Codex must use these files as the source of truth.

## Parallel-Agent Rule

Do not migrate, rename, delete, or rewrite Claude-specific configuration.

Allowed:
- read `CLAUDE.md`
- read `.claude/skills/**`
- follow workflows described there
- create Codex compatibility wrappers under `.agents/**` only when explicitly asked

Not allowed unless explicitly asked:
- changing `.claude/**`
- converting Claude skills to Codex skills
- replacing `CLAUDE.md` with `AGENTS.md`

## Workflow

Before coding:
1. Read `CLAUDE.md`.
2. Identify whether a relevant Claude skill exists under `.claude/skills/`.
3. If a matching Codex wrapper exists under `.agents/skills/`, read it first.
4. Follow the matching Claude workflow.
5. Make small diffs.
6. Run the project's documented checks.

## Codex Skill Adapter

Managed Codex skills under `.agents/skills/**` are generated full mirror runtime copies. Read `.agents/codex-skill-adapter.md` before executing Claude-only terms such as `Agent tool`, `Skill tool`, `claude -p`, `/session-report`, or `$ARGUMENTS`.
