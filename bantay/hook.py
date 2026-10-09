"""Claude Code PreToolUse hook: stdin JSON -> bantayd -> stdout JSON. Always exits 0."""
import json, os, sys, urllib.request, urllib.error

DEFAULT_URL = os.environ.get("BANTAY_URL", "http://127.0.0.1:4477")

def decide_via_daemon(payload: dict, url: str = DEFAULT_URL, timeout: float = 15.0) -> dict:
    try:
        req = urllib.request.Request(url.rstrip("/") + "/decide", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        if not isinstance(d, dict): raise ValueError("daemon reply not an object")
        return d
    except Exception as e:
        return {"verdict": "ask", "reason": f"Bantay daemon unreachable ({type(e).__name__}); run `bantay serve`.", "source": "error"}

def to_hook_output(d: dict, allow_mode: str = "defer") -> dict | None:
    v = d.get("verdict", "ask")
    if v == "allow" and allow_mode != "allow":
        return None
    decision = {"allow": "allow", "ask": "ask", "block": "deny"}.get(v, "ask")
    reason = f"[bantay] {d.get('reason', '')}" + (f" (rule: {d['rule']})" if d.get("rule") else "")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}}

def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict): raise ValueError("hook input not an object")
        payload = {"tool_name": data.get("tool_name"), "tool_input": data.get("tool_input"),
                   "cwd": data.get("cwd") or os.getcwd(), "session_id": data.get("session_id", "")}
        d = decide_via_daemon(payload)
    except Exception as e:
        d = {"verdict": "ask", "reason": f"hook error: {e}", "source": "error"}
    try:
        out = to_hook_output(d, os.environ.get("BANTAY_ALLOW_MODE", "defer"))
    except Exception as e:
        out = to_hook_output({"verdict": "ask", "reason": f"hook error: {e}"})
    if out:
        sys.stdout.write(json.dumps(out))
    return 0

if __name__ == "__main__":
    sys.exit(main())
