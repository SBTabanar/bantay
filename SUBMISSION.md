# Submission answers — AppBuilders PH

Form closes **2026-10-10 10:00 PHT**. No extensions. Each answer below is ready to paste.

## Project name

Bantay

## Short description

Bantay is a local AI guardrail for cloud coding agents: a fine-tuned Qwen2.5-Coder model and deterministic rules running on the laptop judge every tool call (allow, ask, or block) before it executes, in about 90 ms. A local proxy also replaces secrets and personal data in outbound prompts with placeholders, so the cloud model does the work without ever seeing the raw values.

## Team name

Endevo

## Team members

Sergei Benjamin Tabanar (solo)

## GitHub repository (public)

https://github.com/sbtabanar/bantay

<!-- Confirm the repo is public and the build branch is merged to main before submitting. -->

## Demo video (1 minute)

`VIDEO_LINK_PENDING`

## X or LinkedIn post

`POST_LINK_PENDING`

Requirements for the post: tag **Cognition** (use the platform's mention picker so the tag resolves to the company page) and include **#appbuildersph**. Draft text is in [demo/VIDEO.md](demo/VIDEO.md).

## Disclosure: what runs locally, what needs internet, models used

Runs locally: all decision-making (deterministic rules, a routine allowlist, and Qwen2.5-Coder-1.5B-Instruct 4-bit with a Bantay LoRA adapter, via MLX), secret and personal-data redaction (GLiNER gliner_multi_pii-v1 for names and addresses, plus regexes for API keys, emails, Philippine mobile numbers, TIN, SSS, PhilHealth, and card numbers), the daemon, the proxy, the dashboard, the Claude Code hook, and the OpenCode plugin. Requires internet: only the coding agent itself (Claude Code calling Anthropic's API) and the one-time model download. The model is about 0.9 GB on disk and the LoRA adapter about 10 MB; the daemon with the model uses under 2 GB of RAM. Cloud AI as a secondary component: Claude generated the 2,300-row synthetic training set (31 categories, 300 rows held out) and is the cloud comparison baseline in the benchmarks. On the held-out set the fine-tuned adapter reaches 91.3% accuracy against 33.3% for the base model, with 93% block recall and a 2% false-block rate. Frameworks: Python 3.12, mlx-lm, GLiNER, FastAPI/uvicorn, httpx, Claude Code hooks, and the OpenCode plugin API. Development was assisted by Claude and Codex.

## Why does this product benefit from running AI locally?

A safety judge for a coding agent has to read exactly what it protects: every command, file path, and secret the agent touches. Sending that to a cloud judge leaks the thing being guarded, and lets the agent's own provider grade its own homework. The judge also runs on every tool call, hundreds per session, so a 600–1500 ms cloud round trip would make the agent unusable; Bantay's local verdict takes about 90 ms and a rule block about 1 ms. Running locally means it keeps blocking with WiFi off, and it costs nothing per decision. Local AI is the reason the product can exist, not a preference.
