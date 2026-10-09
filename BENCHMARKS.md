# Bantay benchmarks

Measured: 2026-10-09T20:17:36.214431+08:00 (git `ed04221`).

Machine: macOS-26.4.1-arm64-arm-64bit; Apple M4, 16 GiB. Dataset: `data/heldout.jsonl`, 300 held-out examples (100 allow / 100 ask / 100 block).
Dataset SHA-256: `1689daa25bd96ea17f6447af1f287804436f7174e0da33801dbe9c2084c7f8d8`. Local model: `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit`, adapter `adapters/bantay-lora`.

## Reading the table

A local model verdict (fine-tuned LoRA, label only) takes 110 ms at p50 and 1108 ms at p95 on a laptop. In the full engine, 125 of 300 calls are settled by rules or the allowlist at a p95 under 0.9 ms; model-decided ask/block calls, which also generate a one-line reason, take 699 ms p50 / 1290 ms p95 (n=106). Fine-tuning lifts accuracy from 33.3% to 91.3% and cuts the false-block rate from 100.0% to 2.0% (block recall 100.0% -> 93.0%); the full engine scores 86.7% with a 2.0% false-block rate. The cloud baseline (claude-haiku-5-5 via the Claude Code CLI, first 100 rows) takes 2.7 s p50 / 3.8 s p95 per decision including CLI startup (1.0 s / 2.1 s API time alone), scores 84.0% with 75.9% block recall (LoRA alone on the same rows: 91.0% / 93.1%), and would cost about $1.32 per 10,000 decisions at API list price; local inference has no per-decision API cost.

## Results

| System | n | Accuracy | Block recall | False-block rate | p50 ms | p95 ms | Decisions/s | RAM (peak RSS MB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Rules only | 300 | 58.3% | 54.0% | 1.0% | 0.1 | 0.3 | 8051.90 | 19 |
| Base model (Qwen2.5-Coder-1.5B 4-bit) | 300 | 33.3% | 100.0% | 100.0% | 92.0 | 210 | 7.86 | 1377 |
| Fine-tuned model (LoRA) | 300 | 91.3% | 93.0% | 2.0% | 110 | 1108 | 3.87 | 1407 |
| Full engine (rules + allowlist + LoRA) | 300 | 86.7% | 89.0% | 2.0% | 186 | 1148 | 3.08 | 1407 |
| Cloud: claude-haiku-5-5 via Claude Code CLI (incl. startup) | 100 | 84.0% | 75.9% | 0.0% | 2680 | 3831 | 0.34 | n/a (remote) |

Full-engine latency by deciding layer:

| Layer | n | p50 ms | p95 ms |
|---|---:|---:|---:|
| Model (all) | 175 | 568 | 1233 |
| Rules | 111 | 0.3 | 0.8 |
| Allowlist | 14 | 0.4 | 0.6 |
| Model ask/block (verdict + reason) | 106 | 699 | 1290 |

Full engine vs LoRA alone: they disagree on 29 rows; LoRA alone is right on 21 of them and the engine on 7. The deterministic rule and allowlist layers run first and override the model, trading some accuracy for verdicts that cannot be talked out of a block. Of the rows LoRA gets right and the engine misses, 10 are a conservative ask on a row labelled allow (e.g. `rm -rf` of build output), and 1 allowed a row labelled block (details in docs/NOTES.md).

Same first 100 rows, local systems (accuracy / block recall / false-block rate):

- Rules only: 66.0% / 65.5% / 0.0%
- Base model (Qwen2.5-Coder-1.5B 4-bit): 29.0% / 100.0% / 100.0%
- Fine-tuned model (LoRA): 91.0% / 93.1% / 0.0%
- Full engine (rules + allowlist + LoRA): 90.0% / 93.1% / 0.0%

## Methodology

- Accuracy is exact match over allow/ask/block. Block recall is the share of true block rows predicted block. False-block rate is the share of true allow rows predicted block.
- Local model rows measure warm classification only; initialization and downloads are excluded.
- Full engine latency includes synchronous reason generation for model-decided ask/block.
- Each local system runs in a fresh process. Peak RSS includes initialization; it is process memory, not total unified GPU memory.
- Rules-only treats unmatched calls as allow. Coverage is the proportion matched by a rule: 37.0%.
- Precision, recall, F1, confusion matrices and per-row predictions are in BENCHMARKS.json.
- The held-out set is synthetic and has correlated examples from the same generation categories. These results do not establish security guarantees.
- Local inference has $0 API cost; hardware and electricity are excluded.
- Cloud latency is wall time per sequential `claude -p` call: CLI process startup, provider network time and generation. It uses the first 100 held-out rows (35 allow, 36 ask, 29 block) with the same system prompt and call rendering, tools disabled, `--safe-mode`, `--effort low`; compare it with the matching local subset above, not only the 300-row local rows.
- Cloud API-only latency (the CLI's `duration_api_ms`, no process startup): 1012 ms p50 / 2075 ms p95.
- Cloud cost per 10,000 decisions: $1.32 = measured mean tokens per call (926 input including the CLI's own context, 79 output) x API list price. It is an estimate, not observed billing; the CLI's own cost report (which bills its cache writes) sums to $2.24 per 10,000.
- Pricing checked 2026-10-09: [official pricing](https://platform.claude.com/docs/en/about-claude/pricing).
