from __future__ import annotations
import argparse, json, os, shutil, sys
from pathlib import Path

MATCHER = "Bash|Read|Write|Edit|MultiEdit|NotebookEdit|WebFetch"

def hook_cmd() -> str:
    return shutil.which("bantay-hook") or str(Path(sys.executable).parent / "bantay-hook")

def hook_settings() -> dict:
    return {"hooks": {"PreToolUse": [{"matcher": MATCHER, "hooks": [{"type": "command", "command": hook_cmd(), "timeout": 20}]}]}}

def merge_settings(existing: dict, add: dict) -> dict:
    out = json.loads(json.dumps(existing or {}))
    groups = out.setdefault("hooks", {}).setdefault("PreToolUse", [])
    for g in add["hooks"]["PreToolUse"]:
        if not any(h.get("command") == g["hooks"][0]["command"] for grp in groups for h in grp.get("hooks", [])):
            groups.append(g)
    return out

def cmd_install(a):
    target = Path(a.project).expanduser() / ".claude" / "settings.json" if a.project else Path.home() / ".claude" / "settings.json"
    existing = json.loads(target.read_text()) if target.exists() else {}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merge_settings(existing, hook_settings()), indent=2) + "\n")
    print(f"installed Bantay hook into {target}")

def build_engine(a):
    from .engine import Engine
    if a.no_model:
        return Engine(None)
    from .classifier import Classifier, DEFAULT_MODEL
    return Engine(Classifier(a.model or DEFAULT_MODEL, a.adapter))

def build_redactor(a):
    if a.no_redact: return None
    from .redact import Redactor
    return Redactor(use_gliner=not a.no_gliner)

def cmd_serve(a):
    import uvicorn
    from .server import create_app
    app = create_app(build_engine(a), build_redactor(a), upstream=a.upstream)
    print(f"bantayd on http://127.0.0.1:{a.port}  (dashboard: /  proxy: /anthropic)")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")

def cmd_check(a):
    from .hook import decide_via_daemon
    payload = {"tool_name": a.tool, "tool_input": {"command": a.text} if a.tool == "Bash" else {"file_path": a.text}, "cwd": os.getcwd()}
    d = decide_via_daemon(payload, timeout=20)
    if d.get("source") == "error" and "unreachable" in d.get("reason", ""):
        from .types import ToolCall
        d = build_engine(a).decide(ToolCall(payload["tool_name"], payload["tool_input"], cwd=os.getcwd())).to_dict()
    print(json.dumps(d, indent=2)); return 0 if d["verdict"] != "block" else 2

def cmd_redact(a):
    from .redact import Redactor
    r = Redactor(use_gliner=not a.no_gliner).redact(a.text)
    print(r.text); print(json.dumps({"counts": r.counts}), file=sys.stderr)

def main(argv=None):
    p = argparse.ArgumentParser(prog="bantay")
    sub = p.add_subparsers(dest="cmd", required=True)
    def common(sp):
        sp.add_argument("--model"); sp.add_argument("--adapter", default=os.environ.get("BANTAY_ADAPTER"))
        sp.add_argument("--no-model", action="store_true"); sp.add_argument("--no-gliner", action="store_true")
    s = sub.add_parser("serve"); common(s); s.add_argument("--port", type=int, default=4477)
    s.add_argument("--no-redact", action="store_true"); s.add_argument("--upstream", default="https://api.anthropic.com"); s.set_defaults(fn=cmd_serve)
    c = sub.add_parser("check"); common(c); c.add_argument("text"); c.add_argument("--tool", default="Bash"); c.set_defaults(fn=cmd_check)
    i = sub.add_parser("install"); i.add_argument("--project"); i.set_defaults(fn=cmd_install)
    r = sub.add_parser("redact"); r.add_argument("text"); r.add_argument("--no-gliner", action="store_true"); r.set_defaults(fn=cmd_redact)
    b = sub.add_parser("bench"); common(b); b.add_argument("--heldout", default="data/heldout.jsonl"); b.add_argument("--out", default="BENCHMARKS.md")
    b.add_argument("--base-only", action="store_true"); b.set_defaults(fn=lambda a: __import__("bantay.bench", fromlist=["run"]).run(a))
    a = p.parse_args(argv)
    return a.fn(a) or 0

if __name__ == "__main__":
    sys.exit(main())
