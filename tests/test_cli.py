import json
from bantay.cli import hook_settings, merge_settings

def test_hook_settings_shape():
    s = hook_settings()
    pre = s["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "Bash|Read|Write|Edit|MultiEdit|NotebookEdit|WebFetch"
    assert pre["hooks"][0]["command"].endswith("bantay-hook") and pre["hooks"][0]["timeout"] == 20

def test_merge_keeps_existing_hooks():
    existing = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}]}, "model": "x"}
    merged = merge_settings(existing, hook_settings())
    cmds = [h["command"] for g in merged["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert "rtk hook claude" in cmds and any(c.endswith("bantay-hook") for c in cmds) and merged["model"] == "x"
    assert merge_settings(merged, hook_settings()) == merged  # idempotent
