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
