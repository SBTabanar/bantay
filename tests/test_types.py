from bantay.types import ToolCall, Decision, LABELS
from bantay import prompts

def test_summary_per_tool():
    assert ToolCall("Bash", {"command": "ls -la"}).summary() == "$ ls -la"
    assert ToolCall("Read", {"file_path": "/p/.env"}).summary() == "read /p/.env"
    assert ToolCall("Write", {"file_path": "/p/a.py", "content": "x"}).summary() == "write /p/a.py"
    assert ToolCall("Edit", {"file_path": "/p/a.py"}).summary() == "edit /p/a.py"
    assert ToolCall("WebFetch", {"url": "https://x.y"}).summary() == "fetch https://x.y"
    assert ToolCall("Bash", {}).summary() == "$ "

def test_text_includes_content_for_writes():
    t = ToolCall("Write", {"file_path": "/p/a.py", "content": "KEY=1"}).text()
    assert "/p/a.py" in t and "KEY=1" in t

def test_decision_roundtrip():
    d = Decision("block", "nope", "rule", rule="rm_outside")
    assert d.to_dict()["verdict"] == "block" and d.to_dict()["rule"] == "rm_outside"
    assert LABELS == ("allow", "ask", "block")

def test_prompt_messages_shape():
    call = ToolCall("Bash", {"command": "rm -rf /"}, cwd="/p", branch="main")
    m = prompts.messages_for(call)
    assert m[0]["role"] == "system" and m[1]["role"] == "user"
    assert "rm -rf /" in m[1]["content"] and "cwd=/p" in m[1]["content"]
    m2 = prompts.messages_for(call, label="block", reason="wipes disk")
    assert m2[2] == {"role": "assistant", "content": "block\nReason: wipes disk"}
