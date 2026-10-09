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

## Status snapshot (update this section at every milestone)
- 2026-10-09 20:20 PHT — Fable controller resumed as SOLE controller. The Codex/Astra session's uncommitted wave was
  checkpointed as fb29e3f. Fast suite: 264 passed. Trainer finished: adapters/bantay-lora (800 iters), heldout acc
  91.3% vs base 33.3%, block recall 93%, false-block 2% (commits e809521, e82df0b).
  In flight: (1) combined review of c8f92ad..fb29e3f (Task 14 + T8 fixes + Codex fix wave) → fix wave if needed;
  (2) Task 11 completion: full slow suite, local bench rows, cloud Haiku row, final BENCHMARKS.md;
  (3) Task 13 docs: README, SUBMISSION.md, demo/VIDEO.md, LICENSE (no push yet).
  After those: paste final table into README, final whole-branch review + one fix wave, merge build→main,
  create public repo sbtabanar/bantay and push (push-protection: allow fake keys as test data), live rehearsal.

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
