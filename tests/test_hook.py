import json, subprocess, sys
from bantay.hook import to_hook_output, decide_via_daemon

def test_block_maps_to_deny():
    out = to_hook_output({"verdict": "block", "reason": "nope", "rule": "x"}, allow_mode="defer")
    h = out["hookSpecificOutput"]
    assert h["hookEventName"] == "PreToolUse" and h["permissionDecision"] == "deny" and "nope" in h["permissionDecisionReason"]

def test_ask_maps_to_ask_and_allow_defers():
    assert to_hook_output({"verdict": "ask", "reason": "r"}, "defer")["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert to_hook_output({"verdict": "allow", "reason": "r"}, "defer") is None
    assert to_hook_output({"verdict": "allow", "reason": "r"}, "allow")["hookSpecificOutput"]["permissionDecision"] == "allow"

def test_daemon_unreachable_returns_ask_fast():
    d = decide_via_daemon({"tool_name": "Bash", "tool_input": {"command": "ls"}}, url="http://127.0.0.1:1", timeout=1)
    assert d["verdict"] == "ask" and "unreachable" in d["reason"]

def test_hook_process_never_crashes_on_bad_stdin():
    p = subprocess.run([sys.executable, "-m", "bantay.hook"], input="not json", capture_output=True, text=True, env={"BANTAY_URL": "http://127.0.0.1:1", "PATH": "/usr/bin:/bin"})
    assert p.returncode == 0
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
