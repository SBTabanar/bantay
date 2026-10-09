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
