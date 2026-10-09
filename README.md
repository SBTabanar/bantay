# Bantay

A local safety judge and privacy proxy for cloud coding agents. Built by Sergei Benjamin Tabanar for AppBuilders PH, October 9–10, 2026.

Bantay checks proposed tool calls before execution, returning **allow**, **ask**, or **block**. Deterministic rules catch known dangerous patterns; a local Qwen classifier judges other actions. A separate proxy replaces detected secrets and personal data in outbound message content with placeholders, then restores them in replies.

## Why local

The safety judge sees the commands and sensitive content it protects. Running that judge locally avoids sending those inputs to a second cloud service, keeps decisions available offline, and has no per-decision API charge. Rules and routine command checks are fast; model latency is measured separately in [BENCHMARKS.md](BENCHMARKS.md).

## How it works

```text
Claude Code ── PreToolUse hook ──> bantayd on 127.0.0.1:4477
OpenCode ───── local plugin ─────>   rules → routine allowlist → Qwen + LoRA
                                    ↓ allow / ask / block

Claude Code ── Anthropic proxy ──> regex + GLiNER → placeholders → Anthropic
                                    ↑ restored reply           ← response

Browser ──> local dashboard: decisions, reasons, timings, redaction previews
```

The judge makes no cloud calls. The proxy forwards the coding agent's cloud traffic. Those are separate paths: offline decisions work after downloading the models, while Claude Code still needs its provider to generate responses.

## Quick start

Requires Apple Silicon, Python 3.12, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/sbtabanar/bantay.git
cd bantay
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -e '.[dev,pii]'
source .venv/bin/activate
bantay install --project /absolute/path/to/your/project
bantay serve
```

Open <http://127.0.0.1:4477>. In another terminal:

```bash
cd /absolute/path/to/your/project
ANTHROPIC_BASE_URL=http://127.0.0.1:4477/anthropic claude
```

The first run downloads model weights. The default adapter is `adapters/bantay-lora/adapters.safetensors` when present. Training and full model measurements are still in progress in this checkout; until the weights exist, the base model over-blocks. `bantay serve --no-model` uses rules and the routine allowlist, and asks for unmatched calls. `--adapter none` explicitly selects the base model.

`--no-gliner` keeps regex redaction while skipping name/address detection. `--no-redact` disables the proxy and redaction endpoint. A first Claude Code request can contain hundreds of kilobytes of context: observed GLiNER redaction took 9–26 seconds, with repeated blocks served from cache. See [verification notes](docs/NOTES.md).

## Commands and adapters

```bash
bantay check 'rm -rf ~/Documents'       # block; exit status 2
bantay check 'npm test'                # routine allowlist
bantay redact 'Contact demo@example.test' --no-gliner
bantay install --project /path/to/repo
bantay install --opencode --project /path/to/repo
bantay bench --no-model                # rules-only measurement
bantay bench                          # all local systems; requires trained adapter
```

Claude Code hooks cover Bash, Read, Write, Edit, MultiEdit, NotebookEdit, WebFetch, Grep, and Glob. If the daemon is unavailable, the hook returns **ask**. Do not rely on interactive asks in a mode that bypasses the agent's own permissions; use normal permissions for ordinary work.

The [OpenCode plugin](adapters/opencode/bantay.js) applies the same local judge to supported tools. OpenCode's before-tool callback cannot display a human approval dialog, so **ask** refuses the action with an explanation. It fails closed when the daemon cannot be reached. This adapter checks tools; OpenCode provider traffic is not automatically routed through the Anthropic privacy proxy.

## HTTP API

All endpoints bind to loopback only. No authentication is implemented; use on a trusted local machine.

```bash
curl -s http://127.0.0.1:4477/decide \
  -H 'Content-Type: application/json' \
  -d '{"tool_name":"Bash","tool_input":{"command":"npm test"},"cwd":"/path/to/repo"}'

curl -s http://127.0.0.1:4477/redact \
  -H 'Content-Type: application/json' \
  -d '{"text":"Contact demo@example.test"}'
```

`POST /decide` returns verdict, reason, source, rule, probabilities, and latency. `POST /redact` returns redacted text and counts; mappings stay in daemon memory. `GET /health`, `/stats`, and `/events` provide health, counters, and server-sent events. `/anthropic/v1/messages` and `/anthropic/v1/messages/count_tokens` redact supported message content before forwarding. API-key and Claude subscription OAuth forwarding were live-verified for messages.

## Data, training, and benchmarks

The synthetic corpus contains 2,300 teacher-generated examples plus hand-authored seeds, with 300 examples held out. [Data scripts](scripts/) preserve category labels and reproducible splits. The original generation report records a 40-row human-style review by the implementing agent; this is not an independently human-audited dataset. Secrets and identities in fixtures are synthetic.

```bash
bash scripts/train.sh
.venv/bin/python scripts/eval_quick.py adapters/bantay-lora
bantay bench
.venv/bin/python scripts/bench_cloud.py
bantay bench                         # incorporates recorded cloud results
.venv/bin/pytest -q                  # includes model-dependent slow checks
.venv/bin/pytest -q -m 'not slow'     # fast checks
node --test tests/test_opencode.mjs  # adapter checks, if this filename exists in your checkout
```

The reproducible [benchmark report](BENCHMARKS.md) and [raw metrics](BENCHMARKS.json) distinguish classification from full decisions with generated reasons. Currently recorded: rules-only, 300 rows, 58.3% accuracy, 54.0% block recall, 1.0% false blocks among true allow cases, 0.9 ms p95. Base, LoRA, full-engine, and cloud measurements are pending. The cloud runner currently encounters a subscription session limit; there is no measured cloud comparison yet.

## Limitations

- Apple Silicon/MLX is the supported inference backend. Linux, Windows, and Ollama are follow-up work.
- This is a heuristic guardrail, not an operating-system sandbox. Shell indirection, unrecognized tools, and model errors can bypass protection. Do not grant agents access you cannot afford to lose.
- Rules run before the routine allowlist. The allowlist deliberately permits project execution such as tests/builds; repository git hooks/config can also execute code. Broad staging (`git add .`, `git add -A`) retains review; explicit safe paths can skip the model.
- The untuned base model favors block. Classifier scores are relative label probabilities, not independently calibrated risk estimates. Low-confidence allow decisions with substantial block probability become ask.
- Model-generated reasons add latency. Rule/allowlist decisions, model classification, and complete model decisions have different timing profiles.
- Redaction detects supported regex patterns and GLiNER entities; it cannot guarantee finding all sensitive data. Names may be missed or falsely detected. If GLiNER cannot load, the service logs the failure and uses regex-only detection.
- Top-level system prompts, system-role messages, signed thinking, image/document payloads, and unrelated proxy endpoints are not scanned. Assistant history re-masks previously known originals. Keep secrets out of excluded channels.
- Placeholder mappings and original/redacted dashboard previews are held in process memory. They are shared by clients of that daemon; restart it between unrelated trust contexts. No disk audit log is written by Bantay.
- The daemon trusts local callers. Hook enforcement depends on the host agent honoring hook verdicts; interactive bypass-mode ask behavior still needs a live rehearsal.

## Disclosure

Runs locally: decision rules, the Qwen2.5-Coder-1.5B-Instruct 4-bit classifier via MLX (using the Bantay LoRA adapter when available), GLiNER multi-PII and regex redaction, the daemon, dashboard, and adapters. Requires internet: the coding agent's provider, initial model downloads, and the separate optional cloud benchmark. Cloud Claude generated synthetic training examples; the cloud comparison runner is implemented but results are pending. Built with Python 3.12, mlx-lm, GLiNER, FastAPI/uvicorn, httpx, Claude Code hooks, and an OpenCode plugin. Development was assisted by Claude and Codex.

See the [five-minute demo](demo/DEMO.md), [60-second recording plan](demo/VIDEO.md), and [submission answers](SUBMISSION.md). Code is [MIT licensed](LICENSE); model weights retain their upstream licenses.
