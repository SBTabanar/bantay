import json, httpx, pytest
from fastapi.testclient import TestClient
from bantay.server import create_app
from bantay.engine import Engine
from bantay.classifier import FakeClassifier
from bantay.redact import Redactor, StreamRestorer
from bantay.proxy import redact_messages_body, restore_sse_line

def test_redact_messages_body_touches_user_and_tool_results_only():
    r = Redactor(use_gliner=False)
    body = {"system": "You are helpful, contact admin@corp.com", "messages": [
        {"role": "user", "content": "refund a@b.com"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "x"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"}]},
    ]}
    new, n = redact_messages_body(body, r)
    assert new["system"] == body["system"]
    assert new["messages"][0]["content"] == "refund <EMAIL_1>"
    assert "<API_KEY_1>" in new["messages"][2]["content"][0]["content"] and n == 2

def test_restore_sse_line_text_delta_and_json_delta():
    r = Redactor(use_gliner=False); r.redact("mail a@b.com")
    s = StreamRestorer(r)
    line = 'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"send to <EMAIL_1>"}}'
    assert "a@b.com" in restore_sse_line(line, s)
    line2 = 'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"to\\":\\"<EMAIL_1>\\"}"}}'
    assert "a@b.com" in restore_sse_line(line2, s)
    assert restore_sse_line("event: ping", s) == "event: ping"

@pytest.fixture
def proxied(monkeypatch):
    """App whose upstream is a fake Anthropic that echoes what it received, streamed as SSE."""
    received = {}
    async def fake_upstream(request: httpx.Request):
        received["body"] = json.loads(request.content)
        received["headers"] = dict(request.headers)
        text = "echo: " + received["body"]["messages"][0]["content"]
        half = len(text) // 2
        sse = "".join(f'data: {json.dumps({"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":t}})}\n\n' for t in (text[:half], text[half:]))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
    import bantay.proxy as proxy
    monkeypatch.setattr(proxy, "_transport", lambda: httpx.MockTransport(fake_upstream))
    app = create_app(Engine(FakeClassifier()), Redactor(use_gliner=False), upstream="https://fake.anthropic")
    return TestClient(app), received

def test_proxy_redacts_outbound_and_restores_inbound(proxied):
    c, received = proxied
    body = {"model": "claude-x", "stream": True, "messages": [{"role": "user", "content": "email juan@x.ph the invoice"}]}
    r = c.post("/anthropic/v1/messages", json=body, headers={"x-api-key": "k", "anthropic-version": "2023-06-01"})
    assert r.status_code == 200
    assert received["body"]["messages"][0]["content"] == "email <EMAIL_1> the invoice"
    assert received["headers"]["x-api-key"] == "k"
    assert "juan@x.ph" in r.text and "<EMAIL_1>" not in r.text
    s = c.get("/stats").json()
    assert s["redactions"] == 1 and s["pii_redacted"] == 1 and s["bytes_proxied"] > 0

def test_redact_endpoint(proxied):
    c, _ = proxied
    r = c.post("/redact", json={"text": "call 0917-555-1234"})
    assert r.json()["text"] == "call <PHONE_1>" and r.json()["counts"]["PHONE"] == 1

# ---- hardening beyond the brief ----

def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)

def _parse_sse(text):
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = [l[5:].strip() for l in block.split("\n") if l.startswith("data:")]
        ev = [l[6:].strip() for l in block.split("\n") if l.startswith("event:")]
        if data:
            d = json.loads("\n".join(data))
            if ev: assert ev[0] == d["type"], block   # event name always matches its data
            out.append(d)
    return out

def _app_with(monkeypatch, handler, redactor=None):
    import bantay.proxy as proxy
    monkeypatch.setattr(proxy, "_transport", lambda: httpx.MockTransport(handler))
    r = redactor or Redactor(use_gliner=False)
    return TestClient(create_app(Engine(FakeClassifier()), r, upstream="https://fake.anthropic")), r

def test_placeholder_split_at_block_end_is_flushed_before_block_stop(monkeypatch):
    async def up(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse([
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "mail <EMAIL_1"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_stop"},
        ]).encode())
    c, r = _app_with(monkeypatch, up)
    r.redact("x a@b.com")
    resp = c.post("/anthropic/v1/messages", json={"messages": [{"role": "user", "content": "hi"}]})
    evs = _parse_sse(resp.text)
    text = "".join(e["delta"]["text"] for e in evs if e["type"] == "content_block_delta")
    assert text == "mail <EMAIL_1"            # an unterminated non-placeholder is passed through intact
    assert [e["type"] for e in evs][-2:] == ["content_block_stop", "message_stop"]

def test_tool_input_restore_is_json_escaped(monkeypatch):
    r = Redactor(use_gliner=False)
    r.redact('DATABASE_URL=postgres://u:pa"ss@db')   # db_url_password captures a value with a quote
    ph = next(iter(r.reverse))
    pj = json.dumps({"cmd": f"psql {ph}"})
    async def up(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse([
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": pj[:12]}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": pj[12:]}},
            {"type": "content_block_stop", "index": 1},
        ]).encode())
    c, _ = _app_with(monkeypatch, up, r)
    evs = _parse_sse(c.post("/anthropic/v1/messages", json={"messages": [{"role": "user", "content": "hi"}]}).text)
    full = "".join(e["delta"]["partial_json"] for e in evs if e["type"] == "content_block_delta")
    assert json.loads(full)["cmd"] == 'psql ' + r.reverse[ph]

def test_assistant_history_is_remasked_but_thinking_untouched():
    r = Redactor(use_gliner=False)
    r.redact("contact juan@x.ph")
    body = {"messages": [
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "user said <EMAIL_1>", "signature": "sig"},
            {"type": "text", "text": "I emailed juan@x.ph"},
            {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "mail juan@x.ph"}}]},
        {"role": "assistant", "content": "sent to juan@x.ph"},
    ]}
    new, _ = redact_messages_body(body, r)
    blocks = new["messages"][0]["content"]
    assert blocks[0] == {"type": "thinking", "thinking": "user said <EMAIL_1>", "signature": "sig"}
    assert blocks[1]["text"] == "I emailed <EMAIL_1>"
    assert blocks[2]["input"]["command"] == "mail <EMAIL_1>"
    assert new["messages"][1]["content"] == "sent to <EMAIL_1>"

def test_non_messages_paths_are_byte_for_byte(monkeypatch):
    seen = {}
    async def up(request):
        seen["body"] = request.content; seen["url"] = str(request.url); seen["ae"] = request.headers.get("accept-encoding")
        return httpx.Response(200, headers={"content-type": "application/json", "x-upstream": "1"}, content=b'{"echo":"<EMAIL_1> a@b.com"}')
    c, r = _app_with(monkeypatch, up)
    r.redact("a@b.com")
    raw = b'{"messages":[{"role":"user","content":"a@b.com"}]}'
    resp = c.post("/anthropic/v1/messages/count_tokens?beta=true", content=raw, headers={"content-type": "application/json", "authorization": "Bearer t"})
    assert seen["body"] == raw and seen["url"].endswith("/v1/messages/count_tokens?beta=true") and seen["ae"] == "identity"
    assert resp.content == b'{"echo":"<EMAIL_1> a@b.com"}' and resp.headers["x-upstream"] == "1"

def test_non_stream_messages_response_restored_and_query_kept(monkeypatch):
    seen = {}
    async def up(request):
        seen["url"] = str(request.url); seen["auth"] = request.headers.get("authorization"); seen["beta"] = request.headers.get("anthropic-beta")
        return httpx.Response(200, headers={"content-type": "application/json", "request-id": "req_1"},
                              json={"content": [{"type": "text", "text": "hi <EMAIL_1>"}]})
    c, _ = _app_with(monkeypatch, up)
    resp = c.post("/anthropic/v1/messages?beta=true", json={"messages": [{"role": "user", "content": "to a@b.com"}]},
                  headers={"authorization": "Bearer tok", "anthropic-beta": "x-1"})
    assert resp.json()["content"][0]["text"] == "hi a@b.com" and resp.headers["request-id"] == "req_1"
    assert seen["url"].endswith("/v1/messages?beta=true") and seen["auth"] == "Bearer tok" and seen["beta"] == "x-1"

def test_upstream_unreachable_gives_502(monkeypatch):
    async def up(request):
        raise httpx.ConnectError("nope", request=request)
    c, _ = _app_with(monkeypatch, up)
    resp = c.post("/anthropic/v1/messages", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 502 and resp.json()["type"] == "error"

def test_redact_event_shows_prompt_tail_and_placeholder_names(monkeypatch):
    async def up(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"content": []})
    c, r = _app_with(monkeypatch, up)
    reminder = "<system-reminder>" + ("context line\n" * 400) + "</system-reminder>"
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "reply with exactly: hi juan@x.ph"},
                                                      {"type": "text", "text": reminder}]}]}
    c.post("/anthropic/v1/messages", json=body)
    ev = [e for e in c.app.state.bus.history if e["type"] == "redact"][-1]
    assert ev["redacted"].endswith("hi <EMAIL_1>") and ev["original"].endswith("hi juan@x.ph")
    assert ev["placeholder_names"] == ["<EMAIL_1>"] and ev["placeholders"] == 1 and ev["redact_ms"] >= 0

def test_redact_event_centres_on_placeholder_in_long_block(monkeypatch):
    async def up(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"content": []})
    c, r = _app_with(monkeypatch, up)
    long_block = "reply with exactly: hi juan@x.ph\n" + ("unrelated skill listing line\n" * 300)
    c.post("/anthropic/v1/messages", json={"messages": [{"role": "user", "content": [{"type": "text", "text": long_block}]}]})
    ev = [e for e in c.app.state.bus.history if e["type"] == "redact"][-1]
    assert "hi <EMAIL_1>" in ev["redacted"] and "hi juan@x.ph" in ev["original"] and len(ev["redacted"]) <= 2000

def test_redact_event_finds_change_in_earlier_message(monkeypatch):
    async def up(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"content": []})
    c, r = _app_with(monkeypatch, up)
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "reply with exactly: hi juan@x.ph"}]},
                         {"role": "system", "content": [{"type": "text", "text": "skills listing " * 200}]}]}
    c.post("/anthropic/v1/messages", json=body)
    ev = [e for e in c.app.state.bus.history if e["type"] == "redact"][-1]
    assert ev["redacted"].endswith("hi <EMAIL_1>") and ev["original"].endswith("hi juan@x.ph")
