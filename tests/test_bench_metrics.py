from bantay.bench import metrics

def test_metrics_basic():
    m = metrics(["allow", "block", "ask", "block"], ["allow", "block", "block", "allow"])
    assert m["accuracy"] == 0.5
    assert m["block_recall"] == 0.5
    assert m["false_block_rate"] == 0.0
    assert m["per_label"]["allow"]["precision"] == 0.5 and m["per_label"]["allow"]["recall"] == 1.0
    assert m["per_label"]["ask"]["recall"] == 0.0 and m["confusion"]["ask"]["block"] == 1

def test_false_block_rate_counts_allow_rows_predicted_block():
    m = metrics(["allow", "allow", "allow", "allow"], ["block", "allow", "ask", "allow"])
    assert m["false_block_rate"] == 0.25


def test_percentile_nearest_rank():
    from bantay.bench import percentile
    assert percentile(list(range(1, 101)), .95) == 95
    assert percentile([7], .5) == 7


def test_evaluate_rows_counts_sources_and_errors():
    from bantay.bench import evaluate_rows
    rows = [{'tool_name': 'Bash', 'tool_input': {'command': cmd}, 'label': label}
            for cmd, label in [('ls', 'allow'), ('rm', 'block')]]
    result = evaluate_rows(rows, lambda call: ('allow', 'unmatched'))
    assert result['accuracy'] == .5 and result['block_recall'] == 0
    assert result['sources'] == {'unmatched': 2}
    assert result['predictions'] == ['allow', 'allow']


def test_cloud_verdict_parse_rejects_errors():
    import pytest
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("bench_cloud", Path(__file__).resolve().parents[1] / "scripts/bench_cloud.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parse_label = module.parse_label
    assert parse_label('block\nReason: unsafe') == 'block'
    with pytest.raises(ValueError):
        parse_label('API Error: usage limit')


def _fake(acc, n=300, **extra):
    r = {'n': n, 'accuracy': acc, 'block_recall': acc, 'false_block_rate': .01,
         'latency_ms': {'p50': 1.0, 'p95': 2.0}, 'decisions_per_second': 10.0, 'peak_rss_mb': 100.0,
         'sources': {'rule': 10}}
    r.update(extra)
    return r


def test_render_report_has_brief_rows_and_reading_paragraph():
    from bantay.bench import render_report
    meta = {'measured_at': 'now', 'git_commit': 'abcdef1234', 'machine': 'M4', 'n': 300,
            'dataset_sha256': 'x', 'model': 'm', 'adapter': 'a'}
    full = _fake(.9, sources={'rule': 100, 'allowlist': 50, 'model': 150},
                 latency_by_source_ms={'rule': {'n': 100, 'p50': .2, 'p95': .5},
                                       'model_ask_block': {'n': 90, 'p50': 400.0, 'p95': 900.0}})
    cloud = _fake(.8, n=100, model='claude-haiku-5-5', estimated_cost_per_10k_usd=.19, mean_input_tokens=600,
                  mean_output_tokens=40, pricing_checked='d', pricing_source='u')
    cloud.pop('peak_rss_mb')
    md = render_report({'rules': _fake(.5, coverage=.3), 'base': _fake(.3), 'tuned': _fake(.9), 'full': full}, meta, cloud)
    for row in ('| Rules only |', '| Base model', '| Fine-tuned model (LoRA) |',
                '| Full engine (rules + allowlist + LoRA) |', '| Cloud: claude-haiku-5-5'):
        assert row in md
    assert '## Reading the table' in md and 'from 30.0% to 90.0%' in md and '$0.19 per 10,000' in md
    assert 'n/a (remote)' in md and 'Pending' not in md
