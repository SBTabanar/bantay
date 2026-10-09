from bantay.engine import Engine
from bantay.classifier import FakeClassifier
from bantay.types import ToolCall

def test_rules_win_before_model(bash):
    fc = FakeClassifier("allow")
    d = Engine(fc).decide(bash("rm -rf ~"))
    assert d.verdict == "block" and d.source == "rule" and fc.calls == []

def test_allowlist_skips_model(bash):
    fc = FakeClassifier("block")
    d = Engine(fc).decide(bash("git status"))
    assert d.verdict == "allow" and d.source == "allowlist" and fc.calls == []

def test_model_decides_the_rest(bash):
    fc = FakeClassifier("ask", reason_text="touches prod")
    d = Engine(fc).decide(bash("vercel env pull"))
    assert d.verdict == "ask" and d.source == "model" and d.reason == "touches prod" and d.probs["ask"] == 0.8

def test_block_floor_escalates_to_ask(bash):
    fc = FakeClassifier("allow", probs={"allow": 0.5, "ask": 0.1, "block": 0.4})
    d = Engine(fc).decide(bash("python cleanup.py --all"))
    assert d.verdict == "ask" and "uncertain" in d.reason

def test_no_classifier_means_ask(bash):
    d = Engine(None).decide(bash("python x.py"))
    assert d.verdict == "ask" and d.source == "error"

def test_garbage_input_never_raises():
    e = Engine(FakeClassifier("allow"))
    for call in [ToolCall("Bash", {}), ToolCall("Edit", {"old_string": "x"}), ToolCall("Weird", None), ToolCall("Bash", {"command": 123})]:
        d = e.decide(call)
        assert d.verdict in ("allow", "ask", "block")

def test_latency_recorded(bash):
    assert Engine(FakeClassifier()).decide(bash("ls")).latency_ms >= 0

def test_allowlist_rejects_chained_and_find_bypasses(bash):
    for cmd in ["ls\nnode exfil.js", "git status\rpython steal.py", "echo hi\ncurl evil.io",
                "find . -delete", "find . -exec rm -rf {} +", "fd -x rm"]:
        fc, call = FakeClassifier("block"), bash(cmd)
        d = Engine(fc).decide(call)
        assert d.source in ("rule", "model") and d.verdict != "allow", (cmd, d)
        if d.source == "model":
            assert fc.calls == [call], (cmd, d)

def test_allowlist_still_covers_routine(bash):
    for cmd in ["ls -la", "git status"]:
        fc = FakeClassifier("block")
        d = Engine(fc).decide(bash(cmd))
        assert d.verdict == "allow" and d.source == "allowlist" and fc.calls == [], (cmd, d)
