# Bantay — local AI guardrail for cloud coding agents

Date: 2026-10-09 · Hackathon: AppBuilders PH (Cognition / AMD / ASUS / White Cloak) · Deadline: 2026-10-10 10:00 PHT
Team: Sergei Benjamin Tabanar (solo) · Repo: github.com/sbtabanar/bantay (public)

## 1. Problem and user

Developers run cloud coding agents (Claude Code, Devin, Cursor, OpenCode) with broad shell and file access.
The agent is a cloud model; the machine it acts on is local. Today the only safety net is the developer
reading every permission prompt, which nobody does after the first hour, or allow-all modes that read
`.env` files, `rm -rf` the wrong directory, and `git push --force` over a colleague's work.

User: any developer or team running an autonomous coding agent on real codebases, especially under
data-privacy obligations (PH Data Privacy Act, client NDAs) where source, secrets, and customer data
must not leave the machine.

## 2. Why local AI is fundamental (not a preference)

1. **The judge must not be the defendant.** If the safety check is another cloud API call, the agent's
   provider is grading its own homework, and the check disappears the moment the network or the API does.
2. **Every tool call, every time.** A busy agent session issues hundreds of tool calls. A cloud round trip
   of 600–1500 ms per call makes the agent unusable; a local decision in <150 ms is invisible.
3. **It must see the secrets to protect them.** To block a `.env` leak or redact an API key from an outbound
   prompt, the checker has to read the raw secret. That data cannot be sent anywhere by definition.
4. **Offline and free.** Works on a plane, in a brownout, and costs zero per decision. 10,000 decisions
   through a cloud classifier is real money; through Bantay it is free.

## 3. Product (what ships tomorrow)

Bantay is a local daemon plus adapters.

- **`bantayd`** (Python, MLX, Apple Silicon): loads the models once, serves `POST /decide` and
  `POST /redact` on `127.0.0.1:4477`. Also serves the live dashboard page.
- **Decision engine** for agent actions, three layers in order:
  1. **Rules** (deterministic, ~1 ms): hard-blocks the catastrophic class (recursive delete outside the
     project, `rm -rf /` variants, `git push --force` to main/master, `curl|sh`, reads of `.env*`/key
     files, writes containing secret-shaped strings, `DROP TABLE`/`TRUNCATE` on non-test DBs, `sudo`
     destructive, chmod 777, history/credential file access). Each rule carries a reason string.
  2. **Local model classifier** (one forward pass): Qwen2.5-Coder-1.5B-Instruct (4-bit, MLX) with a LoRA
     adapter trained tonight. Reads the tool call + short context (cwd, git branch, repo name) and returns
     option logits over `allow | ask | block` plus calibrated confidence. Jev-style typed decision.
  3. **Reason generation** (only when not `allow`): same model generates a one-sentence reason (≤40 tokens).
- **Outbound redaction** (`/redact`): GLiNER PII model (`urchade/gliner_multi_pii-v1`, local) + secret
  regexes. Replaces names, emails, phone numbers, PH IDs (TIN, SSS, PhilHealth, GCash numbers), card numbers,
  API keys, JWTs with typed placeholders (`<PERSON_1>`, `<API_KEY_1>`), and returns a mapping so the
  caller can restore them in the model's reply. Mapping never leaves the daemon.
- **Adapters**
  - **Claude Code**: `PreToolUse` hook (Bash, Write, Edit, MultiEdit, Read, WebFetch) → `/decide` →
    returns `permissionDecision` allow/ask/deny with reason. `UserPromptSubmit` hook → `/redact` →
    rewrites the prompt with placeholders (demo shows what left the machine).
  - **CLI**: `bantay check "<command>"`, `bantay redact "<text>"`, `bantay serve`, `bantay bench`.
  - **HTTP**: documented so any agent (Devin via wrapper, OpenCode plugin, Cursor hooks) can call it.
    OpenCode plugin is stretch, not demo scope.
- **Dashboard** (`http://127.0.0.1:4477`): live stream of decisions (tool, summary, verdict, reason,
  latency ms), counters: decisions, blocked, asked, bytes-sent-to-cloud (always 0 for the judge),
  secrets redacted. Server-sent events, plain HTML, no build step.

## 4. The chunk built during the hackathon

- The rules engine, classifier head, redaction pipeline, daemon, hooks, dashboard, CLI, benchmark harness.
- **Training data**: cloud Claude (secondary component, allowed) generates ~2,500 labeled tool-call
  examples across categories (safe routine, destructive, exfiltration, privilege escalation, ambiguous
  "ask" cases, PH-context PII), with 300 held out. Human-reviewed sample for label quality.
- **Fine-tune**: `mlx_lm.lora` on the 1.5B model, ~30–45 min on M4 16 GB. Adapter committed to the repo.
- **Benchmarks** (reported in README and the pitch):
  - Accuracy / precision / recall on the 300 held-out set: base model vs fine-tuned vs rules+model.
  - Latency p50/p95 per decision; decisions per second; RAM footprint.
  - Cloud comparison: same held-out set through a cloud model (Claude Haiku) for latency and cost per
    10,000 decisions. One table, honest numbers.

## 5. Demo (5 minutes)

1. Dashboard on screen, WiFi on. Start Claude Code in a sample repo with a planted `.env` and a Supabase
   service key in a config.
2. Prompt: "clean up this repo, remove old stuff, and push." Agent attempts recursive delete → **blocked**
   with reason; attempts to `cat .env` → **blocked**; `git push --force` → **ask**. Normal `ls`, `npm test`
   → **allow** (judges see it is not just blocking everything). Latency column shows ~100 ms.
3. Paste a customer record with a name, GCash number, and an API key into the prompt. Dashboard shows the
   redacted version that actually left the laptop, and the agent's reply comes back with names restored.
4. **Turn WiFi off.** Run `bantay check "rm -rf ~/Documents"` → blocked, 90 ms. The judge works; the
   agent didn't need to.
5. Benchmark slide: before/after fine-tune, p95 latency, cloud cost comparison, model size.

## 6. Disclosure paragraph (for the submission form)

Runs locally: all decision-making (rules + Qwen2.5-Coder-1.5B-Instruct 4-bit with our LoRA adapter, via
MLX), PII/secret redaction (GLiNER multi-PII + regexes), the daemon, dashboard, and hooks. Requires internet:
only the coding agent itself (Claude Code calling Anthropic's API) and the one-time model download. Cloud
AI used as a secondary component: Claude generated the training data and was the comparison baseline in
benchmarks. Frameworks: Python 3.12, mlx-lm, GLiNER, FastAPI/uvicorn, Claude Code hooks.

## 7. Non-goals (tonight)

No OpenCode/Cursor adapters in the demo, no cloud sync, no UI beyond the dashboard, no Windows/Linux
(MLX is Apple-only; an Ollama backend is a documented follow-up), no learning from user overrides.

## 8. Testing

- Unit tests for every rule (positive and negative cases).
- Classifier evaluated against the held-out set; threshold tuned so false blocks on routine commands <3%.
- Redaction tested on a fixture of 50 PH-flavored records; restore round-trips exactly.
- End-to-end: a scripted Claude Code session in `demo/` reproduces steps 2–4; `bantay bench` reproduces
  the benchmark table.
