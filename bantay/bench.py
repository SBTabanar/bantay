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


def percentile(values, q):
    import math
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def evaluate_rows(rows, predict):
    import time
    latencies, predictions, sources = [], [], {}
    for row in rows:
        from .types import ToolCall
        call = ToolCall(row['tool_name'], row['tool_input'], cwd=row.get('cwd', ''), branch=row.get('branch', ''))
        start = time.perf_counter()
        label, source = predict(call)
        latencies.append((time.perf_counter() - start) * 1000)
        predictions.append(label)
        sources[source] = sources.get(source, 0) + 1
    result = metrics([r['label'] for r in rows], predictions)
    result.update(latency_ms={'p50': percentile(latencies, .50), 'p95': percentile(latencies, .95)},
                  decisions_per_second=1000 * len(rows) / sum(latencies) if sum(latencies) else 0,
                  sources=sources, predictions=predictions)
    return result


def worker(mode, heldout, model, adapter):
    """Run one system in a fresh process so peak RSS is attributable to that system."""
    import json, resource, sys
    from pathlib import Path
    from . import rules
    rows = [json.loads(line) for line in Path(heldout).read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError('heldout set is empty')
    if mode == 'rules':
        def predict(call):
            decision = rules.evaluate(call)
            return (decision.verdict, 'rule') if decision else ('allow', 'unmatched')
    else:
        from .classifier import Classifier
        classifier = Classifier(model, adapter if mode in ('tuned', 'full') else None)
        if mode == 'full':
            from .engine import Engine
            engine = Engine(classifier)
            def predict(call):
                decision = engine.decide(call)
                return decision.verdict, decision.source
        else:
            def predict(call):
                return classifier.classify(call)[0], 'model'
    result = evaluate_rows(rows, predict)
    result['peak_rss_mb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1e6 if sys.platform == 'darwin' else 1e3)
    if mode == 'rules':
        result['coverage'] = result['sources'].get('rule', 0) / len(rows)
    return result


def render_report(results, metadata, cloud=None):
    lines = ['# Bantay benchmarks', '', f"Measured: {metadata['measured_at']}", '',
             f"Machine: {metadata['machine']}. Dataset: {metadata['n']} held-out examples.",
             f"Dataset SHA-256: `{metadata['dataset_sha256']}`.", '',
             '| System | n | Accuracy | Block recall | False blocks / true allow | p50 ms | p95 ms | Decisions/s | Peak RSS MB |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    names = {'rules': 'Rules only', 'base': 'Base model', 'tuned': 'LoRA model', 'full': 'Rules + allowlist + LoRA'}
    all_results = [(names[k], v) for k, v in results.items()]
    if cloud:
        all_results.append((cloud['model'] + ' (CLI)', cloud))
    for name, r in all_results:
        lat = r['latency_ms']
        lines.append(f"| {name} | {r['n']} | {r['accuracy']:.1%} | {r['block_recall']:.1%} | {r['false_block_rate']:.1%} | {lat['p50']:.1f} | {lat['p95']:.1f} | {r.get('decisions_per_second', 0):.2f} | {r.get('peak_rss_mb', 0):.1f} |")
    lines += ['', 'Methodology:', '',
              '- Local model rows measure warm classification only; initialization and downloads are excluded.',
              '- Full engine latency includes synchronous reason generation for model-decided ask/block.',
              '- Each local system runs in a fresh process. Peak RSS includes initialization; it is process memory, not total unified GPU memory.',
              '- Rules-only treats unmatched calls as allow. Coverage is the proportion matched by a rule: '
              + f"{results.get('rules', {}).get('coverage', 0):.1%}.",
              '- False-block rate is true allow rows predicted block. Precision, recall, F1, confusion matrices and predictions are in the companion JSON.',
              '- The held-out set is synthetic and has correlated examples from the same generation categories. These results do not establish security guarantees.',
              '- Local inference has $0 API cost; hardware and electricity are excluded.']
    if cloud:
        lines += ['- Cloud latency includes CLI startup, provider network time and generation. It uses the first '
                  + f"{cloud['n']} held-out rows; compare with the matching local subset in the cloud artifact.",
                  f"- Cloud planning estimate per 10,000 decisions: ${cloud['estimated_cost_per_10k_usd']:.3f}, assuming "
                  f"{cloud['assumed_input_tokens']} input and {cloud['assumed_output_tokens']} output tokens per decision. "
                  'This excludes CLI context, caching and thinking tokens; it is not observed subscription billing.',
                  f"- Pricing checked {cloud['pricing_checked']}: [official model pricing]({cloud['pricing_source']})."]
    else:
        lines += ['- Cloud benchmark has not been recorded; no cloud performance claim is made.']
    missing = [names[k] for k in ('base', 'tuned', 'full') if k not in results]
    if missing:
        lines += ['', 'Pending measurements: ' + ', '.join(missing) + '.']
    return '\n'.join(lines) + '\n'


def run(args):
    import datetime, hashlib, json, platform, subprocess, sys
    from pathlib import Path
    from .classifier import DEFAULT_MODEL
    data = Path(args.heldout).resolve()
    rows = [json.loads(line) for line in data.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError('heldout set is empty')
    adapter = args.adapter if args.adapter and args.adapter != 'none' else None
    modes = ['rules'] if args.no_model else ['rules', 'base']
    if not args.no_model and not args.base_only:
        if not adapter or not (Path(adapter) / 'adapters.safetensors').exists():
            raise ValueError('LoRA adapter not ready; use --base-only or --no-model for partial measurements')
        modes += ['tuned', 'full']
    machine = platform.platform()
    if sys.platform == 'darwin':
        info = subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string', 'hw.memsize'], text=True).splitlines()
        machine += f"; {info[0]}, {int(info[1]) / 2**30:.0f} GiB"
    metadata = dict(measured_at=datetime.datetime.now().astimezone().isoformat(), machine=machine, n=len(rows),
                    dataset_sha256=hashlib.sha256(data.read_bytes()).hexdigest(), model=args.model or DEFAULT_MODEL,
                    adapter=adapter, git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip())
    results = {}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for mode in modes:
        print(f'Benchmarking {mode} ({len(rows)} rows)...', flush=True)
        proc = subprocess.run([sys.executable, '-m', 'bantay.bench', mode, str(data), metadata['model'], adapter or ''],
                              capture_output=True, text=True, check=True)
        results[mode] = json.loads(proc.stdout.split('BANTAY_RESULT=')[-1])
        out.with_suffix('.json').write_text(json.dumps({'metadata': metadata, 'results': results}, indent=2) + '\n')
    cloud_path = Path('bench/cloud_results.json')
    cloud = json.loads(cloud_path.read_text()) if cloud_path.exists() else None
    if cloud and cloud.get('dataset_sha256') != metadata['dataset_sha256']:
        raise ValueError('cloud results use a different heldout set; rerun cloud benchmark')
    report = render_report(results, metadata, cloud)
    out.write_text(report)
    print(report)


if __name__ == '__main__':
    import json, sys
    print('BANTAY_RESULT=' + json.dumps(worker(*sys.argv[1:])))
