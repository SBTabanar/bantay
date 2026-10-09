# HANDOFF — Bantay (AppBuilders PH hackathon, deadline 2026-10-10 10:00 PHT)

Read this first if you are a new AI session (Claude, Codex, anything) continuing this work.

**Controller ownership transferred to Codex / GPT-6 Astra high.** Previous Claude sessions must finish only
their already-running T8/T10/review work and report it; do not resume dispatching or start overlapping tasks
after a usage-limit reset. Codex owns Task 14, benchmarks, final review, documentation, and release.

## What this is
Bantay = local AI guardrail for cloud coding agents. A Python daemon on 127.0.0.1:4477 judges every tool call
(allow / ask / block) with deterministic rules + a fine-tuned local Qwen2.5-Coder-1.5B (MLX, one forward pass,
~90 ms), redacts PII/secrets from prompts leaving the laptop (GLiNER + regex, placeholder restore), and shows a
live dashboard. Claude Code is guarded via a PreToolUse hook and `ANTHROPIC_BASE_URL` pointing at the daemon's
`/anthropic` proxy. OpenCode is guarded via a plugin. Solo project by Sergei Benjamin Tabanar.

## Authoritative documents (read in this order)
1. `docs/superpowers/specs/2026-10-09-bantay-design.md` — the spec (binding).
2. `docs/superpowers/plans/2026-10-09-bantay.md` — 13-task implementation plan with code per task.
3. `.superpowers/sdd/2026-10-09-bantay/progress.md` — the execution ledger: every task's state, every review
   finding, every controller ruling (`Ruling:` lines). Trust it over memory. Task briefs/reports live beside it.
4. `docs/NOTES.md` — training/eval numbers and live-verification notes (created by Task 10).

## Process being followed
Superpowers "subagent-driven development": one implementer agent per task (batched 1+2, 3+4, 5+6, 7+8), a
reviewer per batch, fix rounds (max 5) with scoped re-reviews, then a whole-branch final review. The original agents ran
on Opus and the original planner was Fable 5.1. Sergei resumed work with GPT-6 Astra (high reasoning)
on 2026-10-09; this preference supersedes the earlier Opus instruction. Continue sequentially; no further
delegation unless explicitly requested. Implementers stage files by path only (never `git add -A`),
plain commit messages, no AI attribution trailers. Work is on branch `build`; merge to `main` at the end.

## How to run
```
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e ".[dev,pii]"
.venv/bin/pytest -q                       # full suite (slow tests load the model, ~1 min)
.venv/bin/bantay serve                    # daemon + dashboard http://127.0.0.1:4477
.venv/bin/bantay check "rm -rf ~"         # one-off decision
.venv/bin/bantay install --project DIR    # Claude Code hook into DIR/.claude/settings.json
bash demo/run.sh                          # demo setup; see demo/DEMO.md for the 5-minute script
```
Flags: `--no-model`, `--no-gliner`, `--no-redact`, `--adapter PATH` (default adapters/bantay-lora once it exists).

## Status snapshot (update this section at every milestone)
- Resource cleanup — terminated redundant smoke trainer PID 51929 with SIGTERM after verifying its
  separate scratch output and 10-iteration command; it competed with the real 800-iteration run on 16 GB.
  Real trainer PID 50433 preserved. Initial validation only is logged; benchmark model runs remain deferred.
- Resumption milestone 2 — Proxy review corrections: count_tokens now redacts user/tool messages and
  fails closed on redaction errors; Redactor.warm fixed; upstream HTTP client closes on daemon shutdown.
  Focused proxy/server/redactor tests passed (30, one slow deselected). T8 implementation report now exists.
  Official Haiku 5.5 pricing checked: $0.10 input / $0.50 output per MTok for prompts <=100k tokens.
- Resumption milestone 1 — Task 14 implemented and independently reviewed: narrow routine git mutation
  allowlist, force-with-lease main/master block, OpenCode plugin tamper guards, Grep/Glob hook coverage.
  Fast suite: 252 passed, 4 slow deselected. Proxy landed c8f92ad; independent review found count_tokens
  redaction bypass and broken Redactor.warm(), fixes next. Real training still active; benchmark waits for it.
- 2026-10-09 resumed — GPT-6 Astra high owns remaining implementation/review work on `build` (HEAD fc70ad5).
  Existing uncommitted work is preserved: cli/redact and their tests; proxy, bench metrics, train/eval scripts,
  NOTES, adapter outputs, proxy/metrics tests. Previous Claude processes remain active: 800-iteration training
  PID 50433 writes adapters/bantay-lora; smoke training PID 51929 writes a separate scratch directory.
  Do not duplicate training or overwrite their outputs. Prior Claude session may still write T8/T10/OpenCode
  files; verify quiescence before taking ownership. Work first on independent review/Task 14 files. Old
  no-redact daemon PIDs 37695/39369/40728 remain; check port ownership before any live verification.
- 2026-10-09 16:45 — CONTROLLER SESSION HIT ITS USAGE LIMIT. Subagents below were still running and write their
  reports into `.superpowers/sdd/2026-10-09-bantay/`; check those report files and `git log` before redoing anything.
  - T1-2 rules: 3 fix rounds done (head f2cfb26). Round-3 re-review IN FLIGHT. If it reports ≤ minor issues, mark
    T1-2 complete in the ledger; otherwise one more fix round on rules.py/test_rules.py (fresh implementer).
  - T3-4 classifier/engine: COMPLETE (head e0f09fd, round-3 re-review clean).
  - T5-6 server/hook/CLI: COMPLETE (review clean).
  - T7 redactor: committed (8ecb291). T8 proxy: implementer IN FLIGHT (agent writes task-7-8-report.md; expect
    bantay/proxy.py + tests/test_proxy.py + a live `claude -p` verification note in docs/NOTES.md). Needs a review after.
  - T9+T10 data split + LoRA fine-tune: agent IN FLIGHT (writes task-9-10-report.md; commits data/, scripts/,
    bantay/bench.py (metrics only), adapters/bantay-lora/, docs/NOTES.md, cli default adapter). Needs a review after.
  - T12 demo: fix round 1 committed (017ad19); re-review IN FLIGHT.
  - OpenCode adapter: committed (4d2a6c3), live-verified; review IN FLIGHT.
  - T11 benchmarks, T13 README/submission, Task 14 engine hardening, final whole-branch review: NOT STARTED.
- Task 14 scope (ledger ruling): allowlist routine `git add <paths>|commit|pull|push` (non-force/non-delete) in
  bantay/engine.py; `--force-with-lease origin main|master` → block in rules.py; add `Grep|Glob` to MATCHER in
  bantay/cli.py and regenerate demo/sample-project/.claude/settings.json; add `.opencode/plugin` paths to the
  guardrail_tamper rule. Then review.

## Remaining work, in order
1. Close T1-2 / T3-4 re-reviews; Task 14 engine hardening (see ledger ruling).
2. T8 proxy lands → live-verify `ANTHROPIC_BASE_URL=http://127.0.0.1:4477/anthropic claude -p "..."` redacts.
3. T10 adapter → `bantay serve` uses it by default; restore absolute slow-test check.
4. T11 `bantay bench` + `scripts/bench_cloud.py` → `BENCHMARKS.md` (local rows + cloud Haiku row + cost/10k).
5. T13 README (disclosure paragraph is spec §6), SUBMISSION.md, demo/VIDEO.md, MIT LICENSE, public repo
   `sbtabanar/bantay` (fake demo keys may trip push protection — allow as test data or reshape them).
6. Final whole-branch review → fix wave → merge `build` into `main` → push.
7. Sergei: live rehearsal with Claude Code in demo/sample-project, record the 60-second reel, post on LinkedIn/X
   tagging Cognition + #appbuildersph, fill the submission form (project name, description, members, repo,
   video, post link, disclosure).

## Submission checklist (hard requirements)
Project name, short description, team members, PUBLIC GitHub repo, 1-minute demo video, X or LinkedIn post
(tag Cognition, #appbuildersph), disclosure paragraph (what runs locally, what needs internet, models used).
Form closes 10:00 2026-10-10, no extensions.

## Known limits to state honestly (README "Limitations")
Apple Silicon only tonight (MLX); the allowlist is a latency shortcut for routine dev commands and not a sandbox
(rules run first on every command; repo-local git config like core.fsmonitor can still run code on `git status`);
model-decided ask/block adds ~0.3 s for the generated reason; the base model without the adapter blocks
almost everything.
