# Bantay

A local AI guardrail for cloud coding agents: it judges every tool call on your laptop before it runs, and strips secrets and personal data from prompts before they leave.

*Bantay* is Filipino for "guard" or "watch". Built by Sergei Benjamin Tabanar for AppBuilders PH, October 9–10, 2026.

Bantay answers each proposed tool call with **allow**, **ask**, or **block**. Deterministic rules catch known dangerous patterns, a routine allowlist lets everyday commands through, and a fine-tuned local Qwen2.5-Coder model judges the rest. A separate proxy replaces secrets and personal data in outbound messages with placeholders, then restores them in the reply.

## Why local

1. **The judge must not be the defendant.** If the safety check is another cloud API call, the agent's provider grades its own homework, and the check disappears when the network or the API does.
2. **Every tool call, every time.** A busy agent session issues hundreds of tool calls. A measured cloud judgment took 2.7 s per call through the Claude Code CLI, 1.0 s of it API time alone; over hundreds of calls that adds up to minutes of waiting. A local verdict takes about 100 ms; a rule block takes about 1 ms.
3. **It must see the secrets to protect them.** To block a `.env` leak or redact an API key from an outbound prompt, the checker reads the raw secret. That data cannot be sent anywhere by definition.
4. **Offline and free.** It works on a plane or during a brownout, and costs nothing per decision. Ten thousand decisions through a cloud classifier is real money; through Bantay it is electricity.

## How it works

```text
┌─ your laptop ──────────────────────────────────────────────────────────────┐
│                                                                            │
│  Claude Code ── PreToolUse hook ──┐                                        │
│  OpenCode ───── local plugin ─────┼──> bantayd  127.0.0.1:4477             │
│                                   │      1. rules (~1 ms, the floor)       │
│                                   │      2. routine allowlist (npm test)   │
│                                   │      3. Qwen2.5-Coder-1.5B + LoRA      │
│                                   │         via MLX (~100 ms verdict)      │
│                                   └──<── allow / ask / block + reason      │
│                                                                            │
│      what leaves the laptop for decisions: nothing                         │
│                                                                            │
│  Claude Code ── ANTHROPIC_BASE_URL ──> /anthropic proxy                    │
│       ▲                                  regex: keys, email, PH phone,     │
│       │                                    TIN, SSS, PhilHealth, cards     │
│       │                                  GLiNER: names, addresses          │
│       │                                  "Maria" -> <PERSON_1>             │
│       │                                        │                           │
│       └──── reply, placeholders restored ◄─────┼──────────┐                │
│                                                │          │                │
│  Browser ──> http://127.0.0.1:4477 dashboard   │          │                │
│              decisions, reasons, timings,      │          │                │
│              last redaction                    │          │                │
└────────────────────────────────────────────────┼──────────┼────────────────┘
                                                 ▼          │
          what leaves the laptop:     Anthropic API (cloud) ┘
          the conversation, with secrets and personal data
          replaced by placeholders (the map stays in memory)
```

The judge makes no network calls. The proxy forwards the coding agent's own cloud traffic. The two paths are independent: decisions keep working with WiFi off, while the coding agent still needs its provider to think.

## Quick start

Requires Apple Silicon, Python 3.12, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/sbtabanar/bantay.git
cd bantay
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -e '.[dev,pii]'
source .venv/bin/activate

bantay serve                      # daemon + dashboard on http://127.0.0.1:4477
```

In another terminal, from the project you want to guard:

```bash
bantay install --project .        # adds the PreToolUse hook to ./.claude/settings.json
ANTHROPIC_BASE_URL=http://127.0.0.1:4477/anthropic claude
```

For OpenCode:

```bash
bantay install --opencode --project .   # copies the plugin to ./.opencode/plugin/bantay.js
opencode
```

The first run downloads the model weights once (Qwen 4-bit about 0.9 GB, GLiNER about 1.1 GB). The bundled LoRA adapter at `adapters/bantay-lora` is used by default. Useful flags:

| Flag | Effect |
|---|---|
| `--no-model` | rules and allowlist only; unmatched calls become **ask** |
| `--adapter none` | base model without the Bantay adapter (it over-blocks) |
| `--no-gliner` | regex-only redaction; skips name and address detection |
| `--no-redact` | disables the proxy and the redaction endpoint |

## Commands

```bash
bantay check 'rm -rf ~/Documents'       # block, exit status 2
bantay check 'npm test'                 # allow (routine allowlist)
bantay check --tool Read .env           # block (read_secret_file)
bantay redact 'Contact demo@example.test' --no-gliner
bantay install --project /path/to/repo
bantay install --opencode --project /path/to/repo
bantay bench                            # local benchmark rows into BENCHMARKS.md
```

`bantay check` asks the running daemon, and judges in-process if no daemon is running.

The Claude Code hook covers Bash, Read, Write, Edit, MultiEdit, NotebookEdit, WebFetch, Grep, and Glob. If the daemon is unreachable, the hook returns **ask**.

The [OpenCode plugin](adapters/opencode/) applies the same judge to OpenCode's tools, including each file in an `apply_patch`. It fails closed when the daemon cannot be reached. See its [README](adapters/opencode/README.md).

## What runs locally, what needs internet

| Runs on the laptop | Needs internet |
|---|---|
| Decision rules and routine allowlist | The coding agent's own provider (Claude Code calling Anthropic) |
| Qwen2.5-Coder-1.5B-Instruct 4-bit + Bantay LoRA, via MLX | One-time download of model weights from Hugging Face |
| Reason generation for model-decided ask/block | The optional cloud benchmark (`scripts/bench_cloud.py`) |
| Redaction: regex patterns + GLiNER multi-PII | |
| The daemon, proxy, dashboard, hook, and OpenCode plugin | |

After the first download, the judge, the redactor, and the dashboard run with no network at all.

## HTTP API

Everything binds to loopback. There is no authentication; use it on a machine you trust.

```bash
curl -s http://127.0.0.1:4477/decide \
  -H 'Content-Type: application/json' \
  -d '{"tool_name":"Bash","tool_input":{"command":"npm test"},"cwd":"/path/to/repo"}'

curl -s http://127.0.0.1:4477/redact \
  -H 'Content-Type: application/json' \
  -d '{"text":"Contact demo@example.test"}'
```

`POST /decide` returns verdict, reason, source, rule, probabilities, and latency. `POST /redact` returns redacted text and counts; the mapping stays in daemon memory. `GET /health`, `/stats`, and `/events` serve health, counters, and server-sent events for the dashboard. `/anthropic/v1/messages` and `/anthropic/v1/messages/count_tokens` redact message content before forwarding. Authentication headers pass through untouched. Forwarding was verified live with a Claude subscription (OAuth) login, and API-key forwarding is covered by tests; see [docs/NOTES.md](docs/NOTES.md).

Any agent with a pre-tool hook can use `POST /decide`. Claude Code and OpenCode are the two adapters in this repository.

## Models and tools

Runs locally: all decision-making (deterministic rules, a routine allowlist, and Qwen2.5-Coder-1.5B-Instruct 4-bit with a Bantay LoRA adapter, via MLX), secret and personal-data redaction (GLiNER `gliner_multi_pii-v1` for names and addresses, plus regexes for API keys, emails, Philippine mobile numbers, TIN, SSS, PhilHealth, and card numbers), the daemon, the proxy, the dashboard, the Claude Code hook, and the OpenCode plugin. Requires internet: only the coding agent itself (Claude Code calling Anthropic's API) and the one-time model download. The model is about 0.9 GB on disk and the LoRA adapter about 10 MB; the daemon with the model is about 1.4 GB resident (peak RSS 1,412 MB for the full engine in the benchmark), and GLiNER adds roughly 1.9 GB more when name and address redaction is on (`--no-gliner` skips it). Cloud AI as a secondary component: Claude generated the 2,300-row synthetic training set (31 categories, 300 rows held out) and is the cloud comparison baseline in the benchmarks. On the held-out set the fine-tuned adapter reaches 91.3% accuracy against 33.3% for the base model, with 93% block recall and a 2% false-block rate. Frameworks: Python 3.12, mlx-lm, GLiNER, FastAPI/uvicorn, httpx, Claude Code hooks, and the OpenCode plugin API. Development was assisted by Claude and Codex.

## Benchmarks

Held-out set: 300 synthetic tool calls (100 allow, 100 ask, 100 block), excluded from LoRA training. Rules were tuned after inspecting these results, so this is a development evaluation. Apple M4, 16 GB.

<!-- BENCHMARKS:START -->
| System | n | Accuracy | Block recall | False-block rate | p50 ms | p95 ms | Decisions/s | RAM (peak RSS MB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Rules only | 300 | 61.3% | 62.0% | 1.0% | 0.2 | 0.6 | 4095.74 | 19 |
| Base model (Qwen2.5-Coder-1.5B 4-bit) | 300 | 33.3% | 100.0% | 100.0% | 91.6 | 214 | 8.46 | 881 |
| Fine-tuned model (LoRA) | 300 | 91.3% | 93.0% | 2.0% | 98.8 | 563 | 5.33 | 1407 |
| Full engine (rules + allowlist + LoRA) | 300 | 90.0% | 95.0% | 2.0% | 133 | 785 | 4.05 | 1412 |
| Cloud: claude-haiku-5-5 via Claude Code CLI (incl. startup) | 100 | 84.0% | 75.9% | 0.0% | 2680 | 3831 | 0.34 | n/a (remote) |

**Reading the table.** A local model verdict (fine-tuned LoRA, label only) takes 98.8 ms at p50 and 563 ms at p95 on a laptop. In the full engine, 125 of 300 calls are settled by rules or the allowlist at a p95 under 2.1 ms; model-decided ask/block calls, which also generate a one-line reason, take 542 ms p50 / 1080 ms p95 (n=105). Fine-tuning lifts accuracy from 33.3% to 91.3% and cuts the false-block rate from 100.0% to 2.0% (block recall 100.0% -> 93.0%); the full engine scores 90.0% with a 2.0% false-block rate. The cloud baseline (claude-haiku-5-5 via the Claude Code CLI, first 100 rows) takes 2.7 s p50 / 3.8 s p95 per decision including CLI startup (1.0 s / 2.1 s API time alone), scores 84.0% with 75.9% block recall (LoRA alone on the same rows: 91.0% / 93.1%), and would cost about $1.32 per 10,000 decisions at API list price; local inference has no per-decision API cost.

The [override analysis](bench/override_analysis.md) explains the 19 disagreements: nine cleanup-policy asks, two debatable labels, seven engine wins, and one row both miss. Full methodology and raw metrics: [BENCHMARKS.md](BENCHMARKS.md), [BENCHMARKS.json](BENCHMARKS.json).
<!-- BENCHMARKS:END -->

The base model predicts block for 299 of 300 rows, which is why its block recall is perfect and it is unusable. The adapter fixes that: tuned confusion (rows true, columns allow/ask/block) is allow [91, 7, 2], ask [2, 90, 8], block [0, 7, 93]. Training details are in [docs/NOTES.md](docs/NOTES.md).

Reproduce:

```bash
bash scripts/train.sh                                    # ~80 min on an M4
.venv/bin/python scripts/eval_quick.py adapters/bantay-lora
bantay bench                                             # all local rows; reuses bench/cloud_results.json
.venv/bin/python -m bantay.bench rerun tuned full        # re-measure only the model rows
.venv/bin/python scripts/bench_cloud.py                  # optional, needs Claude Code login
.venv/bin/pytest -q -m 'not slow'                        # fast checks
.venv/bin/pytest -q                                      # includes model-dependent checks
node --test tests/test_opencode.mjs                      # OpenCode adapter checks
```

## Limitations

- **Apple Silicon only tonight.** Inference uses MLX. Linux, Windows, and an Ollama backend are follow-up work.
- **The allowlist is a latency shortcut, not a sandbox.** It lets routine development commands skip the model, and some of them run project code by design (`npm test`, `make`, `pytest`). Rules run first on every command. Repository-local git config such as `core.fsmonitor` can still run code on `git status`.
- **Recursive deletes always ask.** Any `rm -r` inside the project, even of `node_modules` or `dist`, and any `git clean -f` returns **ask**, where the dataset labels routine cleanup allow. This is deliberate and accounts for 9 of the 11 held-out rows the full engine gets wrong and the model alone gets right; the [override analysis](bench/override_analysis.md) lists the rows.
- **The model can be wrong.** Accuracy on the held-out set is 91.3%, and that set is synthetic. Classifier scores are relative label probabilities, not calibrated risk. The model has an explicit ask label; the engine also exposes a configurable block-probability threshold.
- **Rules are the floor.** Secret-file reads, force-push to main, environment dumps, pipe-to-shell, and exfiltration uploads are caught by deterministic rules and never depend on the model. This is still a heuristic guardrail, not an operating-system sandbox: shell indirection and unrecognized tools can get past it. Do not give an agent access you cannot afford to lose.
- **The first proxied request is slow.** Claude Code's first request carries a few hundred kilobytes of context. GLiNER scanning it took 9–26 s on CPU; later requests hit a per-block cache. `--no-gliner` makes redaction regex-only and near-instant.
- **OpenCode cannot prompt on ask.** OpenCode's plugin API has no approval dialog, so an **ask** verdict is refused with an explanation. Run the action yourself, or use the Claude Code adapter, which can prompt in interactive permission modes. In the rehearsed headless `--dangerously-skip-permissions` mode, hook ask was refused; interactive bypass-mode prompting remains unverified.
- **Redaction is best effort.** It finds supported regex patterns and GLiNER entities; names can be missed or falsely detected. Top-level system prompts, system-role messages, signed thinking, and image or document payloads are forwarded unscanned. If GLiNER cannot load, Bantay falls back to regex-only and logs it.
- **Memory only.** Placeholder mappings and dashboard previews live in daemon memory and are shared by all its clients; restart it between unrelated trust contexts. Bantay writes no audit log to disk.
- **The daemon trusts local callers.** Enforcement depends on the host agent honoring hook verdicts.

## Built during the hackathon

Everything in this repository was written on 2026-10-09 and 2026-10-10 for AppBuilders PH: the rules, daemon, dashboard, hook, OpenCode plugin, redaction proxy, dataset scripts, training data, LoRA adapter, and benchmark harness. Pre-existing open weights: Qwen2.5-Coder-1.5B-Instruct (4-bit MLX conversion by mlx-community) and GLiNER `urchade/gliner_multi_pii-v1`. Claude generated the synthetic training data and serves as the cloud comparison baseline. Secrets, names, and identifiers in the dataset, tests, and demo project are fake.

See the [five-minute demo script](demo/DEMO.md), the [60-second video plan](demo/VIDEO.md), and the [submission answers](SUBMISSION.md).

## Hackathon

Built solo for AppBuilders PH (October 9–10, 2026) by **Sergei Benjamin Tabanar**.

Code is [MIT licensed](LICENSE). Model weights keep their upstream licenses.
