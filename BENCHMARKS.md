# Bantay benchmarks

Measured: 2026-10-09T15:42:31.308856+08:00

Machine: macOS-26.4.1-arm64-arm-64bit; Apple M4, 16 GiB. Dataset: 300 held-out examples.
Dataset SHA-256: `1689daa25bd96ea17f6447af1f287804436f7174e0da33801dbe9c2084c7f8d8`.

| System | n | Accuracy | Block recall | False blocks / true allow | p50 ms | p95 ms | Decisions/s | Peak RSS MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Rules only | 300 | 58.3% | 54.0% | 1.0% | 0.4 | 0.9 | 2640.79 | 19.1 |

Methodology:

- Local model rows measure warm classification only; initialization and downloads are excluded.
- Full engine latency includes synchronous reason generation for model-decided ask/block.
- Each local system runs in a fresh process. Peak RSS includes initialization; it is process memory, not total unified GPU memory.
- Rules-only treats unmatched calls as allow. Coverage is the proportion matched by a rule: 37.0%.
- False-block rate is true allow rows predicted block. Precision, recall, F1, confusion matrices and predictions are in the companion JSON.
- The held-out set is synthetic and has correlated examples from the same generation categories. These results do not establish security guarantees.
- Local inference has $0 API cost; hardware and electricity are excluded.
- Cloud benchmark has not been recorded; no cloud performance claim is made.

Pending measurements: Base model, LoRA model, Rules + allowlist + LoRA.
