12-rule template

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

## Rule 1 — Think Before Coding
State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what's unclear.

## Rule 2 — Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked. No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

## Rule 3 — Surgical Changes
Touch only what you must. Clean up only your own mess.
Don't "improve" adjacent code, comments, or formatting.
Don't refactor what isn't broken. Match existing style.

## Rule 4 — Goal-Driven Execution
Define success criteria. Loop until verified.
Don't follow steps. Define success and iterate.
Strong success criteria let you loop independently.

## Rule 5 — Use the model only for judgment calls
Use me for: classification, drafting, summarization, extraction.
Do NOT use me for: routing, retries, deterministic transforms.
If code can answer, code answers.

## Rule 6 — Token budgets are not advisory
Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh.
Surface the breach. Do not silently overrun.

## Rule 7 — Surface conflicts, don't average them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.
Don't blend conflicting patterns.

## Rule 8 — Read before you write
Before adding code, read exports, immediate callers, shared utilities.
"Looks orthogonal" is dangerous. If unsure why code is structured a way, ask.

## Rule 9 — Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

## Rule 10 — Checkpoint after every significant step
Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.
If you lose track, stop and restate.

## Rule 11 — Match the codebase's conventions, even if you disagree
Conformance > taste inside the codebase.
If you genuinely think a convention is harmful, surface it. Don't fork silently.

## Rule 12 — Fail loud
"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.

## Git Workflow
- Full convention lives in docs/git-conventions.md — branch strategy, commit/PR title, merge policy. That is the SSOT.
- Hard rule: never commit or PR directly to `main`. Branch ladder is `feature/*`·`fix/*` → `dev` → `main`. Open PRs against `dev` (except a `dev` → `main` release PR).
- Jira issue key: put it in the branch name (`feature/<KEY>-slug`) and reference it in the commit/PR footer as `Refs: <KEY>`. Keep the title pure Conventional Commits — the key lives only in the footer. For squash merges put `Refs:` at the bottom of the PR description (the PR template does this). Details in README.
- Issue-first: feat/fix work requires a Jira issue (key required on the branch). Trivial docs/chore with no issue may be keyless (omit the `Refs:` footer). Branch types are only `feature/*`·`fix/*` — the prefix marks work nature, the commit `type` marks each change. Tracker is Jira; do not add GitHub issue templates.
- Parallel work: concurrent sessions/branches must each use their own `git worktree` (never share one checkout — branches and working trees entangle) and take non-overlapping tickets/work units. Details in README "Git 컨벤션 › 병렬 작업".

## Instruction File Convention
- AGENTS.md is the single source of truth (SSOT) for agent instructions.
- Add a per-folder AGENTS.md (folder-specific content) plus CLAUDE.md (a single
  `@AGENTS.md` line) only when a folder genuinely needs its own instructions.
  Do not add them to every folder.
- Nested files contain folder-specific content only. Do not copy root rules into them.
- A per-module README (e.g. `src/apps/foo/README.md`) is added only when that package has non-obvious local concerns (its own build/run steps, env, quirks). Module roles live in the root README — do not duplicate.
- `.claude/skills` and `.claude/rules` hold Claude-specific executable units, not prose that restates AGENTS.md. Do not put competing instructions in `.claude/rules`; AGENTS.md stays the SSOT.
- Harness change history (what changed under `.claude/skills` and why) lives in `.claude/harness-changelog.md`. Not in CLAUDE.md — that file is always loaded into context, so keep it thin (ADR-0002). Not in `docs/` — that is the design-knowledge SSOT, and tooling history is neither design knowledge nor an operating rule.
