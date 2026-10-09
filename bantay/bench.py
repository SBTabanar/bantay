"""Benchmark helpers for the Bantay classifier.

Currently holds only `metrics()`, shared by scripts/eval_quick.py. Task 11 adds `run(args)`
(the `bantay bench` subcommand, which writes BENCHMARKS.md) on top of it.
"""
from __future__ import annotations
from .types import LABELS


def metrics(y_true: list[str], y_pred: list[str]) -> dict:
    """Plain-Python classification metrics over the allow/ask/block labels.

    Returns accuracy, per-label precision/recall/f1/support (under "per_label"), a confusion
    count dict confusion[true][pred], block_recall (share of true block rows predicted block)
    and false_block_rate (share of true allow rows predicted block).
    """
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred differ in length")
    n = len(y_true)
    confusion = {t: {p: 0 for p in LABELS} for t in LABELS}
    for t, p in zip(y_true, y_pred):
        confusion[t][p] += 1
    per_label = {}
    for lab in LABELS:
        tp = confusion[lab][lab]
        pred_n = sum(confusion[t][lab] for t in LABELS)
        true_n = sum(confusion[lab].values())
        prec = tp / pred_n if pred_n else 0.0
        rec = tp / true_n if true_n else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_label[lab] = {"precision": prec, "recall": rec, "f1": f1, "support": true_n}
    allow_n = sum(confusion["allow"].values())
    return {
        "n": n,
        "accuracy": sum(t == p for t, p in zip(y_true, y_pred)) / n if n else 0.0,
        "per_label": per_label,
        "confusion": confusion,
        "block_recall": per_label["block"]["recall"],
        "false_block_rate": confusion["allow"]["block"] / allow_n if allow_n else 0.0,
    }

