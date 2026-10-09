#!/usr/bin/env python3
"""Quick eval of the classifier on data/heldout.jsonl.

Usage: eval_quick.py [ADAPTER_PATH] [--heldout PATH] [--json OUT]
No adapter = base model. Prints accuracy, per-label P/R/F1, block recall, false-block rate,
confusion matrix and p50/p95 classify latency.
"""
from __future__ import annotations
import argparse, json, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bantay.bench import metrics  # noqa: E402
from bantay.classifier import Classifier, DEFAULT_MODEL  # noqa: E402
from bantay.types import LABELS, ToolCall  # noqa: E402


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("adapter", nargs="?")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--heldout", default=str(ROOT / "data" / "heldout.jsonl"))
    ap.add_argument("--json", help="write metrics + latency as JSON here")
    a = ap.parse_args()

    rows = [json.loads(l) for l in Path(a.heldout).read_text().splitlines() if l.strip()]
    clf = Classifier(a.model, a.adapter)
    y_true, y_pred, lat = [], [], []
    for r in rows:
        call = ToolCall(r["tool_name"], r["tool_input"], cwd=r.get("cwd", ""), branch=r.get("branch", ""))
        t0 = time.perf_counter()
        label, _ = clf.classify(call)
        lat.append((time.perf_counter() - t0) * 1000)
        y_true.append(r["label"]); y_pred.append(label)
    m = metrics(y_true, y_pred)
    m["latency_ms"] = {"p50": statistics.median(lat), "p95": pct(lat, 0.95), "mean": statistics.fmean(lat)}
    m["model"] = clf.name

    print(f"model: {clf.name}   rows: {m['n']}")
    print(f"accuracy {m['accuracy']:.3f}   block_recall {m['block_recall']:.3f}   "
          f"false_block_rate {m['false_block_rate']:.3f}")
    print(f"{'label':6s} {'prec':>6s} {'recall':>6s} {'f1':>6s} {'n':>4s}")
    for lab in LABELS:
        p = m["per_label"][lab]
        print(f"{lab:6s} {p['precision']:6.3f} {p['recall']:6.3f} {p['f1']:6.3f} {p['support']:4d}")
    print("confusion (rows=true, cols=pred): " + "  ".join(
        f"{t}:{[m['confusion'][t][p] for p in LABELS]}" for t in LABELS))
    print(f"latency ms  p50 {m['latency_ms']['p50']:.1f}  p95 {m['latency_ms']['p95']:.1f}")
    if a.json:
        Path(a.json).write_text(json.dumps(m, indent=2) + "\n")


if __name__ == "__main__":
    main()
