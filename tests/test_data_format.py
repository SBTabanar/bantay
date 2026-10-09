import json
from pathlib import Path
from bantay.types import LABELS

ROOT = Path(__file__).resolve().parent.parent

def _lines(p):
    return [json.loads(l) for l in (ROOT / p).read_text().splitlines() if l.strip()]

def test_seed_examples_valid():
    rows = _lines("data/seed.jsonl")
    assert len(rows) >= 30
    for r in rows:
        assert r["label"] in LABELS and r["tool_name"] and isinstance(r["tool_input"], dict) and r["reason"]

def test_split_outputs_exist_and_disjoint():
    if not (ROOT / "data/train.jsonl").exists(): return
    tr, va, ho = (_lines(f"data/{n}.jsonl") for n in ("train", "valid", "heldout"))
    key = lambda r: json.dumps(r["tool_input"], sort_keys=True)
    assert not ({key(r) for r in tr} & {key(r) for r in ho}) and len(ho) >= 200
    assert not ({key(r) for r in va} & ({key(r) for r in tr} | {key(r) for r in ho}))
    for lab in LABELS:
        assert sum(r["label"] == lab for r in ho) == 100
    m = _lines("data/mlx/train.jsonl")[0]["messages"]
    assert m[0]["role"] == "system" and m[-1]["role"] == "assistant" and m[-1]["content"].split("\n")[0] in LABELS

def test_mlx_rows_match_plain_rows():
    if not (ROOT / "data/train.jsonl").exists(): return
    for name in ("train", "valid"):
        plain, mlx = _lines(f"data/{name}.jsonl"), _lines(f"data/mlx/{name}.jsonl")
        assert len(plain) == len(mlx)
        assert [r["label"] for r in plain] == [m["messages"][-1]["content"].split("\n")[0] for m in mlx]
