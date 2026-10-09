import json
from bantay.cli import hook_settings, merge_settings

def test_hook_settings_shape():
    s = hook_settings()
    pre = s["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "Bash|Read|Write|Edit|MultiEdit|NotebookEdit|WebFetch|Grep|Glob"
    assert pre["hooks"][0]["command"].endswith("bantay-hook") and pre["hooks"][0]["timeout"] == 20

def test_merge_keeps_existing_hooks():
    existing = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}]}, "model": "x"}
    merged = merge_settings(existing, hook_settings())
    cmds = [h["command"] for g in merged["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert "rtk hook claude" in cmds and any(c.endswith("bantay-hook") for c in cmds) and merged["model"] == "x"
    assert merge_settings(merged, hook_settings()) == merged  # idempotent

def test_install_opencode_places_plugin(tmp_path):
    from bantay.cli import main, OPENCODE_PLUGIN
    assert main(["install", "--opencode", "--project", str(tmp_path)]) == 0
    placed = tmp_path / ".opencode" / "plugin" / "bantay.js"
    assert placed.read_text() == OPENCODE_PLUGIN.read_text() and "tool.execute.before" in placed.read_text()
    assert not (tmp_path / ".claude").exists()  # Claude Code settings untouched

def test_default_adapter_prefers_env_then_bundled(monkeypatch, tmp_path):
    import bantay.cli as cli
    monkeypatch.setattr(cli, "BUNDLED_ADAPTER", tmp_path / "bantay-lora")
    monkeypatch.delenv("BANTAY_ADAPTER", raising=False)
    assert cli.default_adapter() is None  # nothing bundled
    (tmp_path / "bantay-lora").mkdir(); (tmp_path / "bantay-lora" / "adapters.safetensors").write_bytes(b"x")
    assert cli.default_adapter() == str(tmp_path / "bantay-lora")
    monkeypatch.setenv("BANTAY_ADAPTER", "/elsewhere/lora")
    assert cli.default_adapter() == "/elsewhere/lora"
    monkeypatch.setenv("BANTAY_ADAPTER", "none")
    assert cli.default_adapter() is None

def test_merge_updates_stale_bantay_group_in_place():
    from bantay.cli import MATCHER
    cur = hook_settings()["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    old = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]},
        {"matcher": "Bash|Write", "hooks": [{"type": "command", "command": "/old/venv/bin/bantay-hook", "timeout": 5}]},
    ]}}
    merged = merge_settings(old, hook_settings())
    groups = merged["hooks"]["PreToolUse"]
    mine = [g for g in groups if any("bantay-hook" in h["command"] for h in g["hooks"])]
    assert len(groups) == 2 and len(mine) == 1
    assert mine[0]["matcher"] == MATCHER and mine[0]["hooks"] == [{"type": "command", "command": cur, "timeout": 20}]
    assert groups[0] == old["hooks"]["PreToolUse"][0]  # unrelated hooks untouched
    assert merge_settings(merged, hook_settings()) == merged  # idempotent

def test_merge_moves_bantay_out_of_a_shared_group():
    from bantay.cli import MATCHER
    old = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "rtk hook claude"}, {"type": "command", "command": "bantay-hook", "timeout": 5}]}]}}
    merged = merge_settings(old, hook_settings())
    groups = merged["hooks"]["PreToolUse"]
    assert groups[0] == {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}
    assert len(groups) == 2 and groups[1]["matcher"] == MATCHER
    assert merge_settings(merged, hook_settings()) == merged
