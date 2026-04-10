---
name: pipeline-designer
description: "Design pipeline specifications from scratch — from idea to production-ready spec. Use this skill whenever the user wants to create a pipeline, design an automation workflow, build a multi-step process, orchestrate agents, plan a data processing chain, design a CI/CD flow, create a migration plan, or describe any sequence of steps that should run automatically. Also trigger when the user says 'pipeline', 'workflow', 'automation', 'orchestration', 'multi-step process', 'design a spec', 'plan the steps', or asks to break a complex task into ordered stages with agents and scripts."
---

# Pipeline Designer

An orchestrator skill that takes a pipeline idea from the user, conducts an interview, writes a full draft specification, enhances it, optimizes it, verifies it with four testing methods, and brings it to production-ready quality through a fix cycle.

## Глубина мышления

Думай как опытный тех-лид, который лично отвечает за стабильность продакшена. Каждое решение
проходит через внутренний чек-лист:

- **Побочные эффекты** — что может сломаться в соседних модулях из-за этого изменения?
- **Граничные случаи** — как это поведёт себя на пустых данных, при конкурентном доступе,
  при неожиданном порядке вызовов?
- **Откат** — если изменение окажется неудачным, как быстро можно вернуть всё назад?
- **Альтернативы** — есть ли более простой способ добиться того же результата?
- **Достаточность** — это изменение действительно решает корневую причину, а не маскирует
  симптом?

Все агенты этого скилла работают с такой же глубиной — разбирают задачу по частям,
рассматривают альтернативы и перепроверяют выводы перед тем, как действовать.

**Проброс в создаваемые скиллы:** при проектировании спецификаций пайплайнов — всегда
включай секцию «Глубина мышления» с полным чек-листом (побочные эффекты, граничные случаи,
откат, альтернативы, достаточность) в спецификацию создаваемого скилла. Эталон —
в `.claude/skills/apply-root-cause-fixes/SKILL.md`.

## Жёсткие правила пайплайн-скиллов

Эти четыре правила обязательны для любого пайплайн-скилла (три этапа и больше) — и при создании нового, и при обновлении существующего. Нарушение хотя бы одного — это брак, переделывай в том же проходе.

Правила появились после root-cause-анализа `google-sheets-etl` — скилл тихо пропускал LLM-этап и отдавал базу с пустыми метаданными. Причина каждый раз была одна: скилл оставлял выбор режима «на усмотрение агента», а агент ошибался. Эти правила закрывают саму возможность такой ошибки.

### 1. Один режим на этап

У каждого этапа должен быть ровно один режим работы. Если этап делает «либо A, либо B» в зависимости от входных данных — это два разных этапа, а выбор между ними идёт через отдельный этап триажа с детерминированными условиями в SKILL.md.

Запрещено:
- «Этап нормализации работает либо через LLM-агента, либо через эвристику»
- «Скрипт сам решит, какой режим выбрать»
- «По умолчанию LLM, но можно переключиться на эвристику»

Разрешено:
- «Этап 3: LLM-анализ — единственный режим»
- «Этап 3a — триаж (детерминированные условия в SKILL.md). Этап 3b — LLM или эвристика, выбор определён триажом»

### 2. Никаких опциональных дорогих этапов

Если этап влияет на корректность результата — он обязательный. Слова «опциональный», «по желанию», «можно пропустить для скорости», «на выбор» в описании этапа запрещены.

Запрещено:
- `**Тип:** script + **опциональный** LLM-агент`
- «Можно пропустить для ускорения»
- «По желанию запустить этап N»

Разрешено:
- `**Тип:** script + LLM-агент (оба обязательны)`
- Если этап реально не влияет на корректность — удали его или вынеси в отдельный вспомогательный скилл

### 3. Никаких тихих fallback

Если ожидаемый аргумент, файл или ответ агента отсутствует — скрипт обязан упасть с понятной ошибкой. Запрещено молча возвращать `None`, пустую структуру или переключаться на «режим попроще».

Запрещено в коде:
```python
if analysis_path is None:
    return None  # тихий fallback на эвристики

try:
    result = call_llm(...)
except Exception:
    result = heuristic_fallback(...)  # замаскированная ошибка
```

Разрешено:
```python
if analysis_path is None:
    raise SystemExit("--analysis-file обязателен. LLM-анализ — единственный поддерживаемый режим")

try:
    result = call_llm(...)
except LLMError as e:
    raise SystemExit(f"LLM-агент упал на листе {sheet_name}: {e}")
```

### 4. Запрет мягких формулировок

В описании режимов и обязательности этапов запрещены формулировки, которые оставляют решение «на усмотрение Клода»:

Запрещено:
- «Лучше использовать LLM для нестандартных листов»
- «Обычно эвристика хороша для простых таблиц»
- «Рекомендуется выбирать LLM-агента, если...»
- «По возможности прогнать через LLM»

Разрешено только детерминированные условия и прямые утверждения:
- «Если листов > 10 ИЛИ merged_cells > 5 ИЛИ в первых 10 строках есть слова из набора {P&L, Бюджет, Факт, План} — LLM-режим обязателен»
- «LLM-анализ — единственный поддерживаемый режим этого этапа»

---

Это правила для скиллов, которые создаёт и обновляет эта фабрика. Проверяй каждое правило перед завершением работы. Нашёл нарушение — исправь в том же проходе, не откладывай.

## Core Principles

- **Always run through agents** — every stage executes as a separate agent (Agent tool), never in the main context
- **All intermediate documents are JSON** — agents write results to JSON files. MD format is used only for the final pipeline specification and for agent instruction files (in `agents/`)
- **Before launching any agent, read its instruction file** from `agents/*.md` — pass that content as the agent's prompt
- **Log every operation** — update `orchestrator-log.json` BEFORE starting each stage (not after). This way, if the pipeline crashes, the log shows which stage was in progress. Also log after each user consultation and fix cycle iteration

## Global Reference Rules

Перед началом работы прочитай:
- `~/.claude/references/document-naming-and-placement.md` — правила документооборота (секции 2, 3.2)
- `~/.claude/references/writing-style-guide.md` — единый стиль письма для всех документов и отчётов

## Directory Layout

All pipeline artifacts go into a dedicated folder created at Stage 0. The folder location and internal structure follow the rules from `~/.claude/references/document-naming-and-placement.md` (sections 2, 3.2).

**Standard structure** (from the reference):
- `orchestrator-log.json` — master log tracking all steps
- `agent-outputs/` — JSON output from each agent (`{NN}-{agent-name}.json`)
- `test-reports/` — testing phase reports
- `fix-cycle/iteration-{N}/` — fix cycle iterations (with `traces/` and `fixes/`)

**Skill-specific extras** (not in the reference, specific to pipeline-designer):
- `dry-run-test-results/artifacts/` — dry-run test output and files created during dry-run
- `eval-test-results/` — eval consistency results

**Skill-specific constants:**
- `LOG_SUBCATEGORY`: `skills-modifications` — this skill's subcategory within the logs directory
- `SPEC_SUFFIX`: `-spec.md` — this skill's suffix for spec files

### Spec File Location

The pipeline specification (final MD document) is saved according to the rules from `~/.claude/references/document-naming-and-placement.md` (section 3.1), using `-spec.md` as this skill's suffix.

The timestamp format is defined in section 2 of the reference. Generate this timestamp once at Stage 0 and reuse throughout all stages.

## Logging Format

### Orchestrator Log (orchestrator-log.json)

After each operation, append a step entry to the `steps` array:

```json
{
  "step_number": 1,
  "start_time": "ISO-8601",
  "end_time": "ISO-8601",
  "agent": "agent-name",
  "input": ["list of input file paths"],
  "success": true,
  "comment": "",
  "output": ["list of output file paths"],
  "next_agent": "next-agent-name or null"
}
```

### Agent Output Format

Every agent writes a JSON file with this base structure. The `result` field is agent-specific — see `references/schemas.json` for exact schemas.

```json
{
  "agent": "agent-name",
  "pipeline": "pipeline-name",
  "called_by": "orchestrator",
  "timestamp": "ISO-8601",
  "status": "success | partial | failed",
  "input": { "description": "...", "files": ["..."] },
  "created_files": [{ "path": "...", "description": "..." }],
  "result": {},
  "next_agent": null
}
```

The `next_agent` field is set by the orchestrator, not by the agent itself.

## Pipeline Stages

### Stage 0 — Reference Reading & Infrastructure Setup

#### 0.1 — Read Global References

Before anything else, read `~/.claude/references/document-naming-and-placement.md` and extract:
- **Specs base path and naming pattern** (section 3.1) — where to save spec files and how to name them
- **Logs base path pattern** (section 3.2) — where to create the pipeline log folder
- **Internal log folder structure** (section 3.2) — which subdirectories to create inside the log folder
- **Timestamp format** (section 2) — the format for timestamps used in file and folder names

Combine the extracted values with the skill-specific constants (defined in the Directory Layout section above): `LOG_SUBCATEGORY` = `skills-modifications`, `SPEC_SUFFIX` = `-spec.md`, and the extra subdirectories.

**If the reference file is not found** — stop the pipeline with an error: "Cannot find reference file at ~/.claude/references/document-naming-and-placement.md — this file is required for the pipeline-designer to determine file placement rules."

#### 0.2 — Infrastructure Setup

Run two scripts sequentially to prepare the logging directory. Pass the paths extracted from the reference as arguments:

1. Run `scripts/ensure-log-dir.sh <project-root> <log-base-dir>` — creates the log base directory if missing. The `<log-base-dir>` is the logs base path from the reference (section 3.2) combined with this skill's `LOG_SUBCATEGORY` (e.g. `dev/docs/logs/skills-modifications`)
2. Run `scripts/create-pipeline-folder.sh <pipeline-name> <project-root> <skill-name> <pipelines-base-dir>` — creates the pipeline folder with all subdirectories and initializes `orchestrator-log.json`. The `<pipelines-base-dir>` is the same value passed to ensure-log-dir.sh

Save the returned pipeline folder path — all subsequent operations use it.

3. Generate the timestamp using the format from the reference (section 2). Ensure the specs directory (extracted from section 3.1 of the reference) exists (`mkdir -p`). This timestamp is used in the spec filename.

### Stage 1 — Interview

Read `agents/interviewer.md` and launch an agent with that instruction. The agent conducts an interactive interview with the user to fully understand the pipeline requirements.

**Input:** the user's initial pipeline description
**Output:** `agent-outputs/01-interviewer.json`
**Important:** the interviewer talks to the user directly — this is the only agent that does so

After the agent finishes, update orchestrator-log.json and proceed.

### Stage 2 — Drafting

Read `agents/drafter.md` and launch an agent. It writes a complete pipeline specification as an MD file with 7 required sections.

**Input:** `agent-outputs/01-interviewer.json`
**Output:** the spec MD file + `agent-outputs/02-drafter.json`

### Stage 3 — Enhancement

Read `agents/enhancer.md` and launch an agent. It analyzes the draft and proposes improvements across 7 categories (security, error handling, logging, idempotency, monitoring, scalability, testability).

**Input:** spec MD file + `agent-outputs/01-interviewer.json`
**Output:** `agent-outputs/03-enhancer.json` with all proposals having status `"pending"`

**User consultation:** after the agent finishes, present each improvement to the user one by one. For each one, ask: approve or reject? Update the JSON file with `"approved"` or `"rejected"` status. Update orchestrator-log.json with the consultation results.

### Stage 4 — Optimization

Read `agents/optimizer.md` and launch an agent. It looks for duplication, parallelization opportunities, and simplifications — considering both the original spec and the approved improvements.

**Input:** spec MD file + `agent-outputs/03-enhancer.json` (with statuses)
**Output:** `agent-outputs/04-optimizer.json` with all proposals having status `"pending"`

**User consultation:** same as stage 4 — present each optimization, collect approve/reject.

**After consultation:** create `agent-outputs/04a-approved-changes.json` — a consolidated list of all approved improvements (from stage 3) and approved optimizations (from stage 4). This file follows the `approved_changes` schema from `references/schemas.json`.

### Stage 5 — Change Implementation

Read `agents/change-implementer.md` and launch an agent. It applies all approved changes to the spec.

**Input:** spec MD file + `agent-outputs/04a-approved-changes.json`
**Output:** updated spec MD file + `agent-outputs/05-change-implementer.json`

If conflicts are detected between changes, present them to the user for resolution before proceeding.

### Stage 6 — Testing (4 phases)

Run all four testing phases sequentially. Each phase produces a timestamped JSON report in the format `DD-MM-HH-MM-test-name.json`.

#### 6.1 Structural Validation (automatic)

Run `scripts/structural-validator.sh <spec-file> [reports-dir]`. It checks 12 mandatory elements.

**Output:** `test-reports/DD-MM-HH-MM-structural-validation.json`

#### 6.2 Completeness Verification (agent)

Read `agents/completeness-verifier.md` and launch an agent.

**Input:** the user's original request + `agent-outputs/01-interviewer.json` + current spec MD file
**Output:** `test-reports/DD-MM-HH-MM-completeness-verification.json`

#### 6.3 Dry-Run Test (script + clean agent)

Run `scripts/dry-run-test.sh <spec-file> <results-dir>`. This launches a clean Claude session that knows nothing about the task and tries to implement the pipeline using only the spec.

**Output:** `dry-run-test-results/dry-run-result.json` + timestamped copy in `dry-run-test-results/`

Read `agents/dry-run-tester.md` before launching — it contains the instruction for the clean agent that the script uses.

#### 6.4 Eval Consistency (scripts)

Run 3 passes with the same input, extract metrics, compare:

1. For each run: `scripts/extract-metrics.sh <spec-file> <run-number>` — saves to `eval-test-results/metrics-{N}.json`
2. After all runs: `scripts/compare-runs.sh eval-test-results/metrics-*.json` — saves comparison to `eval-test-results/`

**Stability threshold:** 80% of metrics must be stable (identical across all 3 runs).

#### Collecting test results

After all 4 phases, check results:
- If ALL tests pass — proceed to Stage 8 (Documentation)
- If ANY test fails — proceed to Stage 7 (Fix Cycle)

### Stage 7 — Fix Cycle

Runs until all failed tests pass. Each iteration:

#### 7.1 Launch a separate session

Run `scripts/fix-cycle-session.sh` to start a clean Claude context with:
- Current spec MD file
- Failed test reports
- Agent instructions for tracing and fixing

#### 7.2 Trace problems

For each problem from failed tests, read `agents/problem-tracer.md` and launch an agent. It identifies which agent or stage caused the issue.

**Output:** `fix-cycle/iteration-{N}/traces/trace-problem-{M}.json`

Problems can be traced in parallel if they are independent.

#### 7.3 Fix problems

Read `agents/change-fixer.md` and launch an agent. It takes all trace reports and applies targeted fixes to the spec.

**Output:** `fix-cycle/iteration-{N}/fixes/fix-report.json` + updated spec MD file

#### 7.4 Re-test

Re-run ONLY the tests that failed (not all four phases). If all pass — the spec is ready. If new failures appear — start a new iteration (7.1 through 7.4).

Update orchestrator-log.json with each iteration: iteration number, which tests were rerun, result.

After the fix cycle completes and all tests pass, proceed to Stage 8 (Documentation).

### Stage 8 — Documentation

Read `agents/documentalist.md` and launch an agent. This is the final stage — it analyzes everything the pipeline did and creates or updates project documentation.

**Critical rule:** the documentalist agent MUST read two global reference files before doing anything:
- `~/.claude/references/agent-document-triggers.md` — defines what documents to create/update and when
- `~/.claude/references/document-naming-and-placement.md` — defines naming conventions and file placement

These reference files are the single source of truth. If they are updated, the documentalist's behavior changes automatically — no need to update this skill.

**Input:** `orchestrator-log.json` + all files in `agent-outputs/` + the final spec MD file
**Output:** documentation files (placed according to the reference rules) + `agent-outputs/08-documentalist.json`

The documentalist evaluates each document type's trigger conditions (from the reference file) against what happened in this pipeline, and creates or updates only the documents that are required.

After the agent finishes, update orchestrator-log.json and proceed to completion.

## Completion

When Stage 8 finishes, inform the user that the pipeline specification is ready. Provide:
- Path to the final spec MD file (the path determined at Stage 0 using rules from the reference + this skill's suffix)
- Summary of what was created (number of stages, agents, prompts, checklist items)
- List of documentation files created or updated by the documentalist
- Path to the orchestrator log for full traceability

### Session Report

Launch an agent for session report (mandatory for pipelines, per AGENTS.md).

Agent prompt:
```
Используй /session-report.
Контекст: пайплайн pipeline-designer завершён.
Pipeline name: {pipeline-name}
Log directory: {log-dir}
```

## Global Reference Rules

This skill uses two global reference files as the single source of truth:

- `~/.claude/references/document-naming-and-placement.md` — **read at Stage 0** by the orchestrator to determine ALL file placement: where to save specs, where to create log folders, what timestamp format to use, what internal folder structure to create. Also read at Stage 9 by the documentalist for document placement
- `~/.claude/references/agent-document-triggers.md` — read at Stage 9 by the documentalist to determine what documents to create/update and when

**Key principle:** file placement rules are NOT embedded in this skill. They are extracted from the reference at runtime. If the reference files change, this skill's behavior changes automatically — no need to update SKILL.md.

Every pipeline designed by this skill must also follow this principle. The drafter adds instructions to every spec it creates, telling the target pipeline to read the reference files for file placement (not just for documentation).

## Reference Files

- `agents/*.md` — Read the relevant agent file BEFORE launching each agent
- `scripts/*.sh` — Run directly via bash
- `references/schemas.json` — JSON schemas for all data structures used by agents
- `~/.claude/references/document-naming-and-placement.md` — Global file placement rules (read by orchestrator at Stage 0 AND by documentalist at Stage 9)
- `~/.claude/references/agent-document-triggers.md` — Global documentation trigger rules (read by documentalist at Stage 9)
