# Submission answers — AppBuilders PH

Form closes **2026-10-10 10:00 PHT**. No extensions. Each answer below is ready to paste.

## Project name

Bantay

## Short description

Bantay is a local AI guardrail for cloud coding agents: a fine-tuned Qwen2.5-Coder model and deterministic rules running on the laptop judge every tool call (allow, ask, or block) before it executes. Model-only verdicts take about 99 ms at the median; model ask/block decisions including a reason take about 542 ms. A local proxy also replaces secrets and personal data in outbound prompts with placeholders, so detected values are hidden from the cloud model. Redaction is best effort; system prompts, signed thinking, images, and document payloads are not scanned.

## Team name

Endevo

## Team members

Sergei Benjamin Tabanar (solo)

## GitHub repository (public)

https://github.com/sbtabanar/bantay

Public repository verified on 2026-10-09; tested build merged into default branch `main`.

## Demo video (1 minute)

`VIDEO_LINK_PENDING`

## X or LinkedIn post

`POST_LINK_PENDING`

Requirements for the post: tag **Cognition** (use the platform's mention picker so the tag resolves to the company page) and include **#appbuildersph**. Draft text is in [demo/VIDEO.md](demo/VIDEO.md).

## Disclosure: what runs locally, what needs internet, models used

Runs locally: all decision-making (deterministic rules, a routine allowlist, and Qwen2.5-Coder-1.5B-Instruct 4-bit with a Bantay LoRA adapter, via MLX), secret and personal-data redaction (GLiNER gliner_multi_pii-v1 for names and addresses, plus regexes for API keys, emails, Philippine mobile numbers, TIN, SSS, PhilHealth, and card numbers), the daemon, the proxy, the dashboard, the Claude Code hook, and the OpenCode plugin. Requires internet: only the coding agent itself (Claude Code calling Anthropic's API) and the one-time model download. The model is about 0.9 GB on disk and the LoRA adapter about 10 MB; the daemon with the model is about 1.4 GB resident, and GLiNER adds roughly 1.9 GB more when name and address redaction is on. Cloud AI as a secondary component: Claude generated the 2,300-row synthetic training set (31 categories, 300 rows held out) and is the cloud comparison baseline in the benchmarks. On the synthetic held-out set (excluded from LoRA training; rules were subsequently tuned using its errors), the fine-tuned adapter reaches 91.3% accuracy against 33.3% for the base model, with 93% block recall and a 2% false-block rate. Frameworks: Python 3.12, mlx-lm, GLiNER, FastAPI/uvicorn, httpx, Claude Code hooks, and the OpenCode plugin API. Development was assisted by Claude and Codex.

## Why does this product benefit from running AI locally?

A safety judge for a coding agent has to read exactly what it protects: every command, file path, and secret the agent touches. Sending that to a cloud judge leaks the thing being guarded, and lets the agent's own provider grade its own homework. The judge also runs on every tool call, hundreds per session, so the cloud round trip matters: Claude Haiku 5.5 took 2.7 s per decision through the Claude Code CLI (1.0 s of API time), while Bantay's local verdict takes about 100 ms and a rule block about 1 ms. On the same 100 held-out rows the fine-tuned local model was also more accurate (91.0% against 84.0%). Running locally means it keeps blocking with WiFi off, and it costs nothing per decision, against about $1.32 per 10,000 decisions through the API. Local AI is the reason the product can exist, not a preference.
