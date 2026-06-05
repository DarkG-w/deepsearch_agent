---
name: code-review
description: Review source code, diffs, pull requests, agent changes, backend APIs, frontend code, database logic, memory systems, and tool integrations. Use when the user asks for code review, review, audit, check risks, find bugs, inspect implementation quality, evaluate changes, or assess tests and regressions.
allowed-tools: read_file grep glob ls
---

# Code Review

Use this skill when the user asks to review code or evaluate implementation quality.

## Review Priorities

Focus on findings before praise or summaries.

Prioritize:

1. Correctness bugs and runtime failures
2. Security, permission, path traversal, and data leakage risks
3. Behavioral regressions against existing API/UI contracts
4. Concurrency, async, locking, and lifecycle issues
5. Persistence, migration, and cleanup problems
6. Missing or weak tests for changed behavior
7. Maintainability issues only when they create real risk

Avoid cosmetic feedback unless it affects behavior, readability of critical code, or future maintenance cost.

## Workflow

1. Identify the review target: changed files, specific module, PR-sized diff, or entire feature.
2. Inspect surrounding code before judging the change.
3. Trace data flow through entry points, side effects, persistence, and external tools.
4. Check whether user-visible behavior, API responses, file paths, or database schemas changed.
5. Look for edge cases:
   - missing files
   - empty inputs
   - concurrent requests
   - service restart
   - unavailable network/vector/database dependencies
   - permission failures
   - encoding issues
6. Verify whether tests or validation cover the risky paths.

## Output Format

Lead with findings, ordered by severity.

Use this shape:

```text
Findings
- High: file:line - issue, impact, and concrete fix direction.
- Medium: file:line - issue, impact, and concrete fix direction.
- Low: file:line - issue, impact, and concrete fix direction.

Open Questions
- Any assumptions that affect the review.

Summary
- Brief change-quality summary and residual risk.
```

If there are no findings, say that explicitly and mention remaining test gaps or unverified areas.

## Evidence Rules

- Cite file paths and line numbers when possible.
- Do not claim a bug unless the code path supports it.
- Separate confirmed issues from hypotheses.
- If a dependency behavior matters, inspect the local installed package or official docs before relying on memory.
- For generated files or logs, distinguish source risk from generated-output noise.

## Project-Specific Notes

- Backend runs FastAPI and async task lifecycle code; check cancellation, locks, and context cleanup.
- Agent execution uses DeepAgents, LangGraph checkpointing, tools, subagents, skills, and memory context.
- Long-term memory uses SQLite and optional Milvus; verify graceful degradation when Milvus or embeddings fail.
- Session file paths must stay inside `output/session_*` or `updated/session_*`.
- Frontend is Vue/Vite; check API contract drift with `/api/task`, `/api/sessions`, `/api/memories`, WebSocket events, and file listing.

