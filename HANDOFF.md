# HANDOFF — Bantay (AppBuilders PH hackathon, deadline 2026-10-10 10:00 PHT)

Read this first if you are a new AI session (Claude, Codex, anything) continuing this work.

**Usage checkpoint: Codex / GPT-6 Astra high has saved state for the next session.**
Sergei requested a handoff update because usage is nearly exhausted. The Astra executor was interrupted;
no agent work should be assumed active. The existing LoRA training process is deliberately still running.
The next controller should take ownership here, inspect processes/files first, and avoid duplicate training
or overlapping work from an older Claude session.

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

## Status snapshot — saved 2026-10-09 16:00 PHT

Branch `build`; code HEAD before this handoff commit: `ce864b2`. Previous controller timestamps in the
ledger may disagree with machine time; trust actual commits, files, and live processes.

- T3–4 and T5–6 were previously reviewed complete. T9 data is committed (`99fd65c`).
- Task 14 core is committed (`c47502a`): conservative routine git add/commit/pull/push allowlist,
  force-with-lease to main/master blocked, Grep/Glob hook matcher, OpenCode plugin tamper coverage.
  At that milestone: 252 fast tests passed, 4 slow deselected.
- T8 proxy is committed (`c8f92ad`) and independently reviewed/fixed (`972f35f`): count_tokens
  message redaction, fail-closed regression coverage, warm() NameError, HTTP client shutdown.
  30 focused tests passed. Prior live OAuth verification is in `docs/NOTES.md`.
- Latest review fix wave is **uncommitted**, preserved in the working tree: commit-message literal
  handling and safe formatter output redirection in rules; normalized OpenCode guard paths;
  OpenCode whitespace-free patch headers and raw forwarding of unknown/custom tools; demo preflight
  bounds. Astra reported 212 targeted rules/engine tests and 4 OpenCode JS tests passed. These results
  are scoped; full suite/final review has not run after this wave. Do not mark T1–2/OpenCode closed yet.
- T10 trainer PID **50433** (parent shell 50429) is deliberately still running. Last observed iteration
  **320/800**, train loss 1.212. Iteration-300 validation loss 1.311 (initial 3.989).
  `adapters/bantay-lora/0000300_adapters.safetensors` and `adapters.safetensors` exist (~10 MB each).
  They are interim checkpoints, not validated final release weights. Logs: `adapters/bantay-lora/train.log`.
  The redundant smoke trainer 51929 and stale pytest 29211 were terminated; old daemons are gone.
  Initial contention made training slow; recent output was 11–14 supervised tokens/s. Do not launch
  another trainer. CLI defaults and adapter slow-test assertions are uncommitted and need validation.
- T11 harness/cloud runner are **uncommitted**. Five metrics/parser tests passed. Rules-only run on
  300 rows: 58.3% accuracy, 54% block recall, 1% false-block rate, p95 0.9 ms. `BENCHMARKS.md/json`
  contain partial results; base/tuned/full-engine measurements remain pending. These rules-only
  figures predate the latest uncommitted rules fixes and should be regenerated for final release.
- Cloud benchmark blocked before row 1 by Claude subscription usage limit, resetting **20:10 PHT**.
  Do not retry before reset. No API key was available in the environment. Pricing source/estimates are
  in `bench/pricing.json`; estimated cost is not a measured cloud result. No cloud performance invented.
- T13: README draft exists; `SUBMISSION.md`, `demo/VIDEO.md`, and `LICENSE` not yet created.
  GitHub auth works; public `sbtabanar/bantay` repo was not yet created. No merge or push performed.

## Preserved uncommitted work

Tracked edits: `adapters/opencode/README.md`, `adapters/opencode/bantay.js`, `bantay/cli.py`,
`bantay/rules.py`, `demo/DEMO.md`, `demo/run.sh`, `tests/test_classifier.py`, `tests/test_cli.py`,
`tests/test_rules.py`. Inspect `git diff` before changing or committing them.

Untracked: `BENCHMARKS.json`, `BENCHMARKS.md`, `README.md`, `adapters/bantay-lora/`,
`bantay/bench.py`, `bench/`, `scripts/bench_cloud.py`, `scripts/eval_quick.py`, `scripts/train.sh`,
`tests/test_bench_metrics.py`, `tests/test_opencode.mjs`. Do not delete these as scratch files.
The ledger under `.superpowers/` is ignored by git but is maintained locally.

## Resume commands

```bash
# Inspect saved work and live training before starting anything.
git status --short
git log -8 --oneline
ps -p 50433 -o pid,etime,%cpu,command
tail -12 adapters/bantay-lora/train.log
# CPU-only verification/review can proceed while training runs.
.venv/bin/pytest -q -m 'not slow'
node --test tests/test_opencode.mjs
# After the existing trainer completes, validate the adapter and run GPU work sequentially.
.venv/bin/pytest -q
.venv/bin/python scripts/eval_quick.py
.venv/bin/python scripts/eval_quick.py adapters/bantay-lora
.venv/bin/bantay bench --adapter adapters/bantay-lora
# Only after the 20:10 PHT subscription reset:
.venv/bin/python scripts/bench_cloud.py --limit 100
.venv/bin/bantay bench --adapter adapters/bantay-lora
```

## Remaining work, in order

1. Review the uncommitted rules/OpenCode/demo fix wave, run CPU tests, path-stage and commit it.
   Reconcile late reports in the ledger directory; T3–4 is already complete, T14 core and T8 fixes
   already landed. Avoid reimplementing those. Final branch review remains required.
2. Let existing T10 training finish; evaluate base vs tuned and run the restored adapter slow tests.
   Record actual results in NOTES. Review and commit training scripts, default adapter wiring, tests,
   config and chosen weights. If quality is poor, investigate data/training before claiming success.
3. Review/finish T11 harness and run local benchmark rows after training; rerun rules-only for current
   code. Run cloud after usage reset, or explicitly retain unavailable status. Commit reproducible results.
4. Finish T13 README with actual metrics, SUBMISSION.md (spec §6 disclosure), demo/VIDEO.md shot list
   and social-post draft, MIT LICENSE. User requested function first; no dashboard design polish.
5. Final whole-branch review and fix wave, full tests, create public `sbtabanar/bantay`, merge build→main
   and push per original plan. If fake test secrets trip protection, reshape fixtures rather than
   misrepresenting real secrets. Public release has not happened yet.
6. Sergei: live rehearsal, record the 60-second reel, publish LinkedIn/X post tagging Cognition and
   #appbuildersph, fill submission form. Video/post links remain pending; do not claim submitted.

## Submission checklist (hard requirements)
Project name, short description, team members, PUBLIC GitHub repo, 1-minute demo video, X or LinkedIn post
(tag Cognition, #appbuildersph), disclosure paragraph (what runs locally, what needs internet, models used).
Form closes 10:00 2026-10-10, no extensions.

## Known limits to state honestly (README "Limitations")
Apple Silicon only tonight (MLX); the allowlist is a latency shortcut for routine dev commands and not a sandbox
(rules run first on every command; repo-local git config like core.fsmonitor can still run code on `git status`);
model-decided ask/block adds ~0.3 s for the generated reason; the base model without the adapter blocks
almost everything.
