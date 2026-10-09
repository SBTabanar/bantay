import json
from fastapi.testclient import TestClient
from bantay.server import create_app
from bantay.engine import Engine
from bantay.classifier import FakeClassifier

def client(label="allow"):
    return TestClient(create_app(Engine(FakeClassifier(label))))

def test_decide_block_and_stats():
    c = client()
    r = c.post("/decide", json={"tool_name": "Bash", "tool_input": {"command": "rm -rf ~"}, "cwd": "/p"})
    assert r.status_code == 200 and r.json()["verdict"] == "block" and r.json()["rule"] == "rm_outside_project"
    s = c.get("/stats").json()
    assert s["decisions"] == 1 and s["block"] == 1 and s["bytes_to_cloud_for_decisions"] == 0

def test_decide_handles_missing_fields():
    r = client().post("/decide", json={"tool_name": "Bash"})
    assert r.status_code == 200 and r.json()["verdict"] in ("allow", "ask", "block")

def test_health_and_dashboard():
    c = client()
    assert c.get("/health").json()["ok"] is True
    assert "Bantay" in c.get("/").text

def test_dashboard_hides_original_redaction_text_by_default():
    html = client().get("/").text
    assert 'id="orig-toggle"' in html and 'aria-pressed="false"' in html and "show original" in html
    assert '<div id="r-orig-box" hidden>' in html
    assert "localStorage" in html and "try {" in html

def test_events_stream_receives_decision():
    # Starlette's TestClient buffers the whole body, so an endless SSE stream needs a real server.
    import socket, threading, time, httpx, uvicorn
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(Engine(FakeClassifier("allow"))), host="127.0.0.1", port=port,
                                           log_level="warning", timeout_graceful_shutdown=1))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(0.05)
        got = None
        with httpx.stream("GET", base + "/events", timeout=5) as s:
            httpx.post(base + "/decide", json={"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": "/p"}, timeout=5)
            for line in s.iter_lines():
                if line.startswith("data:"):
                    ev = json.loads(line[5:])
                    if ev["type"] == "decision":
                        got = ev
                        break
        assert got is not None
        assert got["verdict"] == "allow" and got["summary"] == "$ git status"
    finally:
        server.should_exit = True; t.join(timeout=5)
