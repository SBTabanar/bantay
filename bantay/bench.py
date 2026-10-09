"""Benchmark harness for Bantay: `metrics()` (shared with scripts/eval_quick.py) and `run(args)`,
the `bantay bench` subcommand that measures each local system in a fresh process, merges the cloud
row from bench/cloud_results.json when present, and writes BENCHMARKS.md plus BENCHMARKS.json.
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
    latencies, predictions, sources, row_sources = [], [], {}, []
    for row in rows:
        from .types import ToolCall
        call = ToolCall(row['tool_name'], row['tool_input'], cwd=row.get('cwd', ''), branch=row.get('branch', ''))
        start = time.perf_counter()
        label, source = predict(call)
        latencies.append((time.perf_counter() - start) * 1000)
        predictions.append(label)
        row_sources.append(source)
        sources[source] = sources.get(source, 0) + 1
    result = metrics([r['label'] for r in rows], predictions)
    by_source = {}
    groups = {s: [ms for ms, src in zip(latencies, row_sources) if src == s] for s in sources}
    # Model-decided ask/block also generate a reason in the full engine; report them separately.
    groups['model_ask_block'] = [ms for ms, src, p in zip(latencies, row_sources, predictions)
                                 if src == 'model' and p != 'allow']
    for name, values in groups.items():
        if values:
            by_source[name] = {'n': len(values), 'p50': percentile(values, .50), 'p95': percentile(values, .95)}
    result.update(latency_ms={'p50': percentile(latencies, .50), 'p95': percentile(latencies, .95)},
                  latency_by_source_ms=by_source,
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


NAMES = {'rules': 'Rules only', 'base': 'Base model (Qwen2.5-Coder-1.5B 4-bit)', 'tuned': 'Fine-tuned model (LoRA)',
         'full': 'Full engine (rules + allowlist + LoRA)'}


def _fmt_ms(v):
    return f'{v:.1f}' if v < 100 else f'{v:.0f}'


def reading_paragraph(results, cloud):
    """One judge-readable paragraph; every number comes from the measured results."""
    parts = []
    if 'tuned' in results:
        t = results['tuned']['latency_ms']
        parts.append(f"A local model verdict (fine-tuned LoRA, label only) takes {_fmt_ms(t['p50'])} ms at p50 "
                     f"and {_fmt_ms(t['p95'])} ms at p95 on a laptop.")
    if 'full' in results:
        f = results['full']
        srcs = f.get('latency_by_source_ms', {})
        rule_n = f['sources'].get('rule', 0) + f['sources'].get('allowlist', 0)
        line = (f"In the full engine, {rule_n} of {f['n']} calls are settled by rules or the allowlist at a p95 under "
                f"{max(srcs.get('rule', {}).get('p95', 0), srcs.get('allowlist', {}).get('p95', 0)) + 0.05:.1f} ms; ")
        if 'model_ask_block' in srcs:
            m = srcs['model_ask_block']
            line += (f"model-decided ask/block calls, which also generate a one-line reason, take "
                     f"{_fmt_ms(m['p50'])} ms p50 / {_fmt_ms(m['p95'])} ms p95 (n={m['n']}).")
        else:
            line += 'no model-decided ask/block call occurred.'
        parts.append(line)
    if 'base' in results and 'tuned' in results:
        b, t = results['base'], results['tuned']
        parts.append(f"Fine-tuning lifts accuracy from {b['accuracy']:.1%} to {t['accuracy']:.1%} and cuts the false-block "
                     f"rate from {b['false_block_rate']:.1%} to {t['false_block_rate']:.1%} (block recall "
                     f"{b['block_recall']:.1%} -> {t['block_recall']:.1%})"
                     + (f"; the full engine scores {results['full']['accuracy']:.1%} with a "
                        f"{results['full']['false_block_rate']:.1%} false-block rate." if 'full' in results else '.'))
    if cloud:
        api = cloud.get('api_latency_ms')
        same = cloud.get('matching_local_subset', {})
        parts.append(f"The cloud baseline ({cloud['model']} via the Claude Code CLI, first {cloud['n']} rows) takes "
                     f"{cloud['latency_ms']['p50'] / 1000:.1f} s p50 / {cloud['latency_ms']['p95'] / 1000:.1f} s p95 per "
                     'decision including CLI startup'
                     + (f" ({api['p50'] / 1000:.1f} s / {api['p95'] / 1000:.1f} s API time alone)" if api else '')
                     + f", scores {cloud['accuracy']:.1%} with {cloud['block_recall']:.1%} block recall"
                     + (f" (LoRA alone on the same rows: {same['tuned']['accuracy']:.1%} / {same['tuned']['block_recall']:.1%})"
                        if 'tuned' in same else '')
                     + f", and would cost about ${cloud['estimated_cost_per_10k_usd']:.2f} per 10,000 decisions at API "
                     'list price; local inference has no per-decision API cost.')
    else:
        parts.append('No cloud measurement is recorded, so no cloud comparison is claimed.')
    return ' '.join(parts)


def _label_counts(labels):
    counts = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    return counts


def render_report(results, metadata, cloud=None, labels=None):
    lines = ['# Bantay benchmarks', '', f"Measured: {metadata['measured_at']} (git `{metadata['git_commit'][:7]}`).", '',
             f"Machine: {metadata['machine']}. Dataset: `data/heldout.jsonl`, {metadata['n']} held-out examples "
             '(100 allow / 100 ask / 100 block).',
             f"Dataset SHA-256: `{metadata['dataset_sha256']}`. Local model: `{metadata['model']}`"
             + (f", adapter `{metadata['adapter']}`." if metadata.get('adapter') else '.'), '',
             '## Reading the table', '', reading_paragraph(results, cloud), '',
             '## Results', '',
             '| System | n | Accuracy | Block recall | False-block rate | p50 ms | p95 ms | Decisions/s | RAM (peak RSS MB) |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    rows = [(NAMES[k], v) for k, v in results.items()]
    if cloud:
        rows.append((f"Cloud: {cloud['model']} via Claude Code CLI (incl. startup)", cloud))
    for name, r in rows:
        lat = r['latency_ms']
        rss = f"{r['peak_rss_mb']:.0f}" if 'peak_rss_mb' in r else 'n/a (remote)'
        lines.append(f"| {name} | {r['n']} | {r['accuracy']:.1%} | {r['block_recall']:.1%} | {r['false_block_rate']:.1%} "
                     f"| {_fmt_ms(lat['p50'])} | {_fmt_ms(lat['p95'])} | {r.get('decisions_per_second', 0):.2f} | {rss} |")
    if 'full' in results and results['full'].get('latency_by_source_ms'):
        lines += ['', 'Full-engine latency by deciding layer:', '', '| Layer | n | p50 ms | p95 ms |', '|---|---:|---:|---:|']
        layer_names = {'rule': 'Rules', 'allowlist': 'Allowlist', 'model': 'Model (all)',
                  'model_ask_block': 'Model ask/block (verdict + reason)', 'error': 'Error fallback'}
        for key, v in results['full']['latency_by_source_ms'].items():
            lines.append(f"| {layer_names.get(key, key)} | {v['n']} | {_fmt_ms(v['p50'])} | {_fmt_ms(v['p95'])} |")
    if labels and 'full' in results and 'tuned' in results:
        pairs = list(zip(labels, results['tuned']['predictions'], results['full']['predictions']))
        lora_wins = sum(t == y != f for y, t, f in pairs)
        engine_wins = sum(f == y != t for y, t, f in pairs)
        lines += ['', f"Full engine vs LoRA alone: they disagree on {sum(t != f for _, t, f in pairs)} rows; LoRA alone is right on "
                  f"{lora_wins} of them and the engine on {engine_wins}. The deterministic rule and allowlist layers run first and "
                  'override the model, trading some accuracy for verdicts that cannot be talked out of a block. Of the rows LoRA '
                  f"gets right and the engine misses, {sum(y == 'allow' and f == 'ask' and t == y for y, t, f in pairs)} are a "
                  'conservative ask on a row labelled allow (e.g. `rm -rf` of build output), and '
                  f"{sum(y == 'block' and f == 'allow' and t == y for y, t, f in pairs)} allowed a row labelled block (details in docs/NOTES.md)."]
    if cloud and cloud.get('matching_local_subset'):
        lines += ['', f"Same first {cloud['n']} rows, local systems (accuracy / block recall / false-block rate):", '']
        for k, m in cloud['matching_local_subset'].items():
            if k in NAMES:
                lines.append(f"- {NAMES[k]}: {m['accuracy']:.1%} / {m['block_recall']:.1%} / {m['false_block_rate']:.1%}")
    lines += ['', '## Methodology', '',
              '- Accuracy is exact match over allow/ask/block. Block recall is the share of true block rows predicted block. '
              'False-block rate is the share of true allow rows predicted block.',
              '- Local model rows measure warm classification only; initialization and downloads are excluded.',
              '- Full engine latency includes synchronous reason generation for model-decided ask/block.',
              '- Each local system runs in a fresh process. Peak RSS includes initialization; it is process memory, not total unified GPU memory.',
              '- Rules-only treats unmatched calls as allow. Coverage is the proportion matched by a rule: '
              + f"{results.get('rules', {}).get('coverage', 0):.1%}.",
              '- Precision, recall, F1, confusion matrices and per-row predictions are in BENCHMARKS.json.',
              '- The held-out set is synthetic and has correlated examples from the same generation categories. These results do not establish security guarantees.',
              '- Local inference has $0 API cost; hardware and electricity are excluded.']
    if cloud:
        lines += ['- Cloud latency is wall time per sequential `claude -p` call: CLI process startup, provider network time and generation. '
                  f"It uses the first {cloud['n']} held-out rows"
                  + (f" ({', '.join(f'{v} {k}' for k, v in sorted(_label_counts(labels[:cloud['n']]).items()))})" if labels else '')
                  + ' with the same system prompt and call rendering, tools disabled, `--safe-mode`, `--effort low`; compare it with the '
                  'matching local subset above, not only the 300-row local rows.'
                  + (f" {cloud['requested_rows'] - cloud['n']} requested rows did not complete." if cloud.get('requested_rows', cloud['n']) > cloud['n'] else ''),
                  f"- Cloud API-only latency (the CLI's `duration_api_ms`, no process startup): {_fmt_ms(cloud['api_latency_ms']['p50'])} ms p50 / "
                  f"{_fmt_ms(cloud['api_latency_ms']['p95'])} ms p95." if cloud.get('api_latency_ms') else '- Cloud API-only latency was not recorded.',
                  f"- Cloud cost per 10,000 decisions: ${cloud['estimated_cost_per_10k_usd']:.2f} = measured mean tokens per call "
                  f"({cloud['mean_input_tokens']:.0f} input including the CLI's own context, {cloud['mean_output_tokens']:.0f} output) "
                  'x API list price. It is an estimate, not observed billing'
                  + (f"; the CLI's own cost report (which bills its cache writes) sums to ${cloud['cli_reported_cost_per_10k_usd']:.2f} per 10,000."
                     if cloud.get('cli_reported_cost_per_10k_usd') is not None else '.'),
                  f"- Pricing checked {cloud['pricing_checked']}: [official pricing]({cloud['pricing_source']})."]
        if cloud.get('note'):
            lines.append(f"- {cloud['note']}")
    else:
        lines += ['- Cloud benchmark has not been recorded; no cloud performance claim is made.']
    missing = [NAMES[k] for k in ('base', 'tuned', 'full') if k not in results]
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
    write_report(out, metadata, results, data)


def load_cloud(metadata, path='bench/cloud_results.json'):
    import json
    from pathlib import Path
    cloud_path = Path(path)
    cloud = json.loads(cloud_path.read_text()) if cloud_path.exists() else None
    if cloud and cloud.get('dataset_sha256') != metadata['dataset_sha256']:
        raise ValueError('cloud results use a different heldout set; rerun cloud benchmark')
    return cloud


def write_report(out, metadata, results, heldout='data/heldout.jsonl'):
    import hashlib, json
    from pathlib import Path
    data = Path(heldout)
    labels = None
    if data.exists() and hashlib.sha256(data.read_bytes()).hexdigest() == metadata['dataset_sha256']:
        labels = [json.loads(line)['label'] for line in data.read_text().splitlines() if line.strip()]
    report = render_report(results, metadata, load_cloud(metadata), labels)
    out.write_text(report)
    print(report)


def merge(out='BENCHMARKS.md'):
    """Re-render BENCHMARKS.md from BENCHMARKS.json plus the cloud row, without re-running local models."""
    import json
    from pathlib import Path
    out = Path(out)
    saved = json.loads(out.with_suffix('.json').read_text())
    write_report(out, saved['metadata'], saved['results'])


if __name__ == '__main__':
    import json, sys
    if sys.argv[1:2] == ['merge']:  # python -m bantay.bench merge [BENCHMARKS.md]
        merge(*sys.argv[2:3])
    else:
        print('BANTAY_RESULT=' + json.dumps(worker(*sys.argv[1:])))
