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
    predictions, latencies, usage = [], [], []
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text())
        if saved['dataset_sha256'] != digest or saved['model'] != price['model']:
            raise ValueError('checkpoint dataset/model mismatch')
        predictions, latencies, usage = saved['predictions'], saved['latencies_ms'], saved['usage']
    stop_note, unparsed = None, []
    if len(predictions) > len(rows):
        raise ValueError('checkpoint exceeds requested limit')
    env = dict(os.environ)
    for key in ('CLAUDECODE', 'ANTHROPIC_BASE_URL'):
        env.pop(key, None)
    with tempfile.TemporaryDirectory(prefix='bantay-cloud-bench-') as cwd:
        for i, row in enumerate(rows[len(predictions):], start=len(predictions)):
            call = ToolCall(row['tool_name'], row['tool_input'], cwd=row.get('cwd', ''), branch=row.get('branch', ''))
            command = ['claude', '-p', '--model', price['model'], '--output-format', 'json',
                       '--tools', '', '--safe-mode', '--no-session-persistence', '--effort', 'low',
                       '--system-prompt', SYSTEM, render_call(call)]
            start = time.perf_counter()
            try:
                proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=args.timeout)
            except subprocess.TimeoutExpired:
                stop_note = f'Cloud run stopped at row {i} of {len(rows)}: CLI timed out after {args.timeout:.0f} s'
                print(stop_note, file=sys.stderr)
                break
            elapsed = (time.perf_counter() - start) * 1000
            try:
                reply = json.loads(proc.stdout)
            except ValueError:
                reply = {'is_error': True, 'result': (proc.stdout or proc.stderr)[-300:]}
            if proc.returncode or reply.get('is_error'):
                # Usage limits and API errors end the run; metrics cover only the completed rows.
                stop_note = (f'Cloud run stopped at row {i} of {len(rows)} (exit {proc.returncode}): '
                             + str(reply.get('result', ''))[:200].replace('\n', ' '))
                print(stop_note, file=sys.stderr)
                break
            u = reply.get('usage', {})
            try:
                label = parse_label(reply['result'])
            except ValueError:
                label = 'ask'  # an unparseable verdict falls back to ask, as the local engine does on errors
                unparsed.append(i)
            predictions.append(label); latencies.append(elapsed)
            usage.append(dict(input_tokens=u.get('input_tokens', 0) + u.get('cache_creation_input_tokens', 0)
                              + u.get('cache_read_input_tokens', 0),
                              output_tokens=u.get('output_tokens', 0), api_ms=reply.get('duration_api_ms', 0),
                              cli_cost_usd=reply.get('total_cost_usd', 0)))
            checkpoint.write_text(json.dumps(dict(dataset_sha256=digest, model=price['model'], predictions=predictions,
                                                  latencies_ms=latencies, usage=usage), indent=2) + '\n')
            print(f'{i + 1}/{len(rows)} {predictions[-1]} {elapsed:.0f} ms', flush=True)
    if not predictions:
        raise RuntimeError(stop_note or 'no cloud rows completed')
    rows = rows[:len(predictions)]  # never extrapolate past the rows that actually completed
    n = len(rows)
    mean_in = sum(u['input_tokens'] for u in usage) / n
    mean_out = sum(u['output_tokens'] for u in usage) / n
    api_ms = [u['api_ms'] for u in usage]
    result = metrics([row['label'] for row in rows], predictions)
    result.update(model=price['model'], dataset_sha256=digest, predictions=predictions, requested_rows=args.limit,
                  latency_ms={'p50': percentile(latencies, .5), 'p95': percentile(latencies, .95)},
                  api_latency_ms={'p50': percentile(api_ms, .5), 'p95': percentile(api_ms, .95)},
                  decisions_per_second=1000 * n / sum(latencies),
                  pricing_checked=price['checked'], pricing_source=price['source'],
                  mean_input_tokens=mean_in, mean_output_tokens=mean_out,
                  estimated_cost_per_10k_usd=(mean_in * price['input_usd_per_million']
                                             + mean_out * price['output_usd_per_million']) / 100,
                  cli_reported_cost_per_10k_usd=10_000 * sum(u['cli_cost_usd'] for u in usage) / n,
                  method='Sequential no-tools Claude CLI (-p, JSON output), safe mode, low effort; process startup included')
    result['unparsed_rows'] = unparsed  # this run only; rows resumed from a checkpoint are not re-checked
    if stop_note:
        result['note'] = stop_note
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
