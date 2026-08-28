---
name: openmanus-safe-implementation
description: Use this skill for any OpenManus, Manus AI, agent runtime, tool-calling, browser automation, MCP, planning, refactor, architecture, or Claude Code implementation task. It prevents broad unsafe rewrites and forces investigation, tests, minimal diffs, and verification before changes.
---

# OpenManus Safe Implementation

You are working on an OpenManus-style AI agent runtime.

The goal is not to make random "improvements".
The goal is to preserve working behavior while safely improving the system.

## Mandatory workflow

Before editing code:

1. Understand the user's goal.
2. Inspect the existing codebase.
3. Identify the real root cause or architectural bottleneck.
4. Choose the correct existing skill.
5. Produce a small implementation plan.
6. Make the minimum necessary change.
7. Run verification.
8. Explain what changed and what remains risky.

## Skill routing

Use these installed skills when relevant:

- Use `triage` for bugs, errors, broken behavior, failed tools, broken agent loops, broken browser automation, or confusing logs.
- Use `tdd` for bug fixes and new behavior.
- Use `improve-codebase-architecture` for architecture, agent runtime, tool calling, MCP, browser automation, model routing, planning, memory, or orchestration.
- Use `zoom-out` before large changes, broad rewrites, or unclear refactors.
- Use `grill-me` when the task is vague, dangerous, too broad, or could be solved in multiple ways.
- Use `to-prd` when turning a big idea into product requirements.
- Use `to-issues` when splitting a big plan into implementation tasks.
- Use `write-a-skill` when creating new project-specific skills.

## Forbidden behavior

Do not:

- rewrite the whole project;
- replace working architecture without a clear plan;
- change public interfaces without checking usages;
- hide errors with broad try/except blocks;
- silently return fake success;
- change model/provider logic unless the task requires it;
- change browser automation logic unless the task requires it;
- change MCP/tool schemas without updating validation;
- make large multi-file refactors in one step.

## High-risk zones

Treat these as protected:

- agent loop
- planning / flow logic
- tool calling
- MCP
- browser automation
- shell execution
- file editing tools
- memory/context handling
- model/provider routing
- sandbox/workspace logic
- config schemas
- auth/secrets
- CI/CD
- Git operations

If a task touches these zones, stop and produce a plan before editing.

## Verification

After code changes, run the smallest useful verification available.

Prefer:

```bash
ruff check .
pytest -q
mypy .
