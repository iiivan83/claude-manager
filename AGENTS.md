# AGENTS.md

## Canonical Project Instructions

This project is designed primarily for Claude Code.

Canonical files:
- `CLAUDE.md`
- `.claude/skills/**/SKILL.md`
- docs referenced from those files

Claude source remains source of truth for skill development. Managed Codex skills are
generated full mirror runtime copies under `.agents/skills/**`; they are regenerated
from Claude source and are not hand-maintained.

## Parallel-Agent Rule

Do not migrate, rename, delete, or rewrite Claude-specific configuration.

Allowed:
- read `CLAUDE.md`
- read `.claude/skills/**`
- read full managed Codex mirrors under `.agents/skills/**`
- read `.agents/codex-skill-adapter.md`
- follow workflows described there
- create or update generated Codex mirrors under `.agents/**` only through the
  mirror sync tooling or when explicitly asked

Not allowed unless explicitly asked:
- changing `.claude/**`
- converting Claude skills to Codex skills
- replacing `CLAUDE.md` with `AGENTS.md`

## Workflow

Before coding:
1. Read `CLAUDE.md`.
2. Identify whether a relevant Claude skill exists under `.claude/skills/`.
3. If a matching managed Codex mirror exists under `.agents/skills/`, read that full
   `SKILL.md` first for Codex runtime.
4. Follow the matching Claude workflow.
5. Make small diffs.
6. Run the project's documented checks.

## Codex Skill Adapter

Managed Codex mirrors use adapter contract version `codex-skill-adapter-v1`.
The contract lives in `.agents/codex-skill-adapter.md` and maps Claude-only runtime
terms such as `Agent tool`, `Skill tool`, `claude -p`, `/session-report`, and
`$ARGUMENTS`. The adapter contract changes tool invocation only; it does not change
business logic, pipeline order, quality gates, artifacts, or tests.

Mirror sync uses a validated self-path rewrite transform for references to the current
skill. Writes require managed opt-in: active projects are discoverable, but writable
managed projects need the migration matrix, a managed marker, an existing manifest, or a
confirmed migration plan hash. Codex-native projects are skipped/read-only unless they
explicitly opt in.
