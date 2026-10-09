#!/usr/bin/env python3
"""Build stratified train/valid/heldout splits from data/seed.jsonl + data/raw/*.jsonl.

- Dedupes by the canonical tool_input (sorted-key JSON), so no input can sit in two splits;
  inputs seen with conflicting labels are dropped entirely.
- Shuffles with seed 7, then stratifies by label: heldout 100 per label, valid 50 per label,
  the rest train. Within a label, rows are interleaved across categories so each split covers
  categories in proportion. Hand-written seed rows always go to train.
- Writes data/{train,valid,heldout}.jsonl (plain rows) and data/mlx/{train,valid}.jsonl
  (mlx-lm chat format, {"messages": prompts.messages_for(...)}).
"""
from __future__ import annotations
import json, random, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bantay.prompts import messages_for  # noqa: E402
from bantay.types import LABELS, ToolCall  # noqa: E402

DATA = ROOT / "data"
HELDOUT_PER_LABEL, VALID_PER_LABEL, SEED = 100, 50, 7
FIELDS = ("tool_name", "tool_input", "cwd", "branch", "label", "reason", "category")


def key(r: dict) -> str:
    return json.dumps(r["tool_input"], sort_keys=True, ensure_ascii=False)


def clean(r: dict) -> dict | None:
    if r.get("label") not in LABELS or not isinstance(r.get("tool_name"), str) or not r["tool_name"]:
        return None
    if not isinstance(r.get("tool_input"), dict) or not r["tool_input"]:
        return None
    out = {f: r.get(f) or "" for f in FIELDS}
    out["tool_input"] = r["tool_input"]
    out["reason"] = str(out["reason"]).strip() or f"{r['label']} by teacher"
    return out


def read(path: Path, source: str) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = clean(json.loads(line))
        except json.JSONDecodeError:
            continue
        if r:
            r["_src"] = source
            rows.append(r)
    return rows


def interleave(rows: list[dict], rng: random.Random) -> list[dict]:
    """Order rows so any prefix covers categories in proportion to their size."""
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    keyed = []
    for cat_rows in by_cat.values():
        rng.shuffle(cat_rows)
        n = len(cat_rows)
        keyed += [((i + rng.random()) / n, r) for i, r in enumerate(cat_rows)]
    keyed.sort(key=lambda t: t[0])
    return [r for _, r in keyed]


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def to_mlx(r: dict) -> dict:
    call = ToolCall(r["tool_name"], r["tool_input"], cwd=r["cwd"], branch=r["branch"])
    return {"messages": messages_for(call, r["label"], r["reason"])}


def main() -> None:
    rows = read(DATA / "seed.jsonl", "seed")
    for p in sorted((DATA / "raw").glob("*.jsonl")):
        rows += read(p, "raw")

    labels_by_key = defaultdict(set)
    for r in rows:
        labels_by_key[key(r)].add(r["label"])
    conflicts = {k for k, ls in labels_by_key.items() if len(ls) > 1}
    seen, uniq = set(), []
    for r in rows:  # seed first, so seed wins on duplicates
        k = key(r)
        if k in conflicts or k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    print(f"read {len(rows)} rows, {len(uniq)} unique, dropped {len(rows) - len(uniq)} "
          f"(duplicates + {len(conflicts)} label-conflicting inputs)")

    rng = random.Random(SEED)
    train, valid, heldout = [], [], []
    for lab in LABELS:
        seed_rows = [r for r in uniq if r["label"] == lab and r["_src"] == "seed"]
        gen_rows = interleave([r for r in uniq if r["label"] == lab and r["_src"] != "seed"], rng)
        heldout += gen_rows[:HELDOUT_PER_LABEL]
        valid += gen_rows[HELDOUT_PER_LABEL:HELDOUT_PER_LABEL + VALID_PER_LABEL]
        train += gen_rows[HELDOUT_PER_LABEL + VALID_PER_LABEL:] + seed_rows
    for split in (train, valid, heldout):
        rng.shuffle(split)
        for r in split:
            r.pop("_src", None)

    for name, split in (("train", train), ("valid", valid), ("heldout", heldout)):
        write(DATA / f"{name}.jsonl", split)
        c = Counter(r["label"] for r in split)
        print(f"{name:8s} {len(split):5d}  " + "  ".join(f"{l}={c[l]}" for l in LABELS))
    write(DATA / "mlx" / "train.jsonl", [to_mlx(r) for r in train])
    write(DATA / "mlx" / "valid.jsonl", [to_mlx(r) for r in valid])


if __name__ == "__main__":
    main()
