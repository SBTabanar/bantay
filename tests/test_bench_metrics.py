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
