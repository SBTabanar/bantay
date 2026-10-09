#!/usr/bin/env python3
"""Cloud classifier baseline via Claude CLI; sends only synthetic held-out rows, with tools disabled."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bantay.bench import metrics, percentile
from bantay.prompts import SYSTEM, render_call
from bantay.types import ToolCall


def parse_label(text):
    match = re.match(r'^\s*(allow|ask|block)\b', text, flags=re.I)
    if not match:
        raise ValueError('classifier did not return a leading verdict')
    return match.group(1).lower()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--heldout', default=str(ROOT / 'data/heldout.jsonl'))
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--out', default=str(ROOT / 'bench/cloud_results.json'))
    parser.add_argument('--timeout', type=float, default=90)
    args = parser.parse_args()
    data = Path(args.heldout)
    rows = [json.loads(line) for line in data.read_text().splitlines() if line.strip()][:args.limit]
    if args.limit <= 0 or not rows:
        raise ValueError('positive limit and nonempty heldout data required')
    price = json.loads((ROOT / 'bench/pricing.json').read_text())
    digest = hashlib.sha256(data.read_bytes()).hexdigest()
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = out.with_suffix('.checkpoint.json')
    predictions, latencies = [], []
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text())
        if saved['dataset_sha256'] != digest or saved['model'] != price['model']:
            raise ValueError('checkpoint dataset/model mismatch')
        predictions, latencies = saved['predictions'], saved['latencies_ms']
    if len(predictions) > len(rows):
        raise ValueError('checkpoint exceeds requested limit')
    env = dict(os.environ)
    for key in ('CLAUDECODE', 'ANTHROPIC_BASE_URL'):
        env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix='bantay-cloud-bench-') as cwd:
        for i, row in enumerate(rows[len(predictions):], start=len(predictions)):
            call = ToolCall(row['tool_name'], row['tool_input'], cwd=row.get('cwd', ''), branch=row.get('branch', ''))
            command = ['claude', '-p', '--model', price['model'], '--output-format', 'text',
                       '--tools', '', '--safe-mode', '--no-session-persistence', '--effort', 'low',
                       '--system-prompt', SYSTEM, render_call(call)]
            start = time.perf_counter()
            proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=args.timeout)
            elapsed = (time.perf_counter() - start) * 1000
            if proc.returncode:
                raise RuntimeError(f'cloud CLI failed on row {i}; checkpoint retained (exit {proc.returncode})')
            predictions.append(parse_label(proc.stdout)); latencies.append(elapsed)
            checkpoint.write_text(json.dumps(dict(dataset_sha256=digest, model=price['model'], predictions=predictions,
                                                  latencies_ms=latencies), indent=2) + '\n')
            print(f'{i + 1}/{len(rows)} {predictions[-1]} {elapsed:.0f} ms', flush=True)
    result = metrics([row['label'] for row in rows], predictions)
    result.update(model=price['model'], dataset_sha256=digest, predictions=predictions,
                  latency_ms={'p50': percentile(latencies, .5), 'p95': percentile(latencies, .95)},
                  decisions_per_second=1000 * len(rows) / sum(latencies),
                  pricing_checked=price['checked'], pricing_source=price['source'],
                  assumed_input_tokens=price['assumed_input_tokens'], assumed_output_tokens=price['assumed_output_tokens'],
                  estimated_cost_per_10k_usd=(price['assumed_input_tokens'] * price['input_usd_per_million']
                                             + price['assumed_output_tokens'] * price['output_usd_per_million']) / 100,
                  method='Sequential no-tools Claude CLI, safe mode, low effort; process startup included')
    local_path = ROOT / 'BENCHMARKS.json'
    if local_path.exists():
        local = json.loads(local_path.read_text())
        if local['metadata']['dataset_sha256'] == digest:
            result['matching_local_subset'] = {name: metrics([r['label'] for r in rows], values['predictions'][:len(rows)])
                                                for name, values in local['results'].items()}
    out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('predictions', 'confusion')}, indent=2))


if __name__ == '__main__':
    main()
