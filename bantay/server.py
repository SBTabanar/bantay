from __future__ import annotations
import asyncio, json, os, subprocess, time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from .types import ToolCall
from .events import EventBus
from .engine import Engine

STATIC = Path(__file__).parent / "static"
_branch_cache: dict[str, tuple[float, str]] = {}

def git_branch(cwd: str) -> str:
    if not cwd: return ""
    now = time.time(); hit = _branch_cache.get(cwd)
    if hit and now - hit[0] < 5: return hit[1]
    try:
        b = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=1).stdout.strip()
    except Exception:
        b = ""
    _branch_cache[cwd] = (now, b); return b

def create_app(engine: Engine, redactor=None, upstream: str = "https://api.anthropic.com") -> FastAPI:
    app = FastAPI(title="bantayd")
    bus = EventBus()
    app.state.engine, app.state.bus, app.state.redactor, app.state.upstream = engine, bus, redactor, upstream

    @app.get("/health")
    async def health():
        return {"ok": True, "model": engine.model_name, "redactor": getattr(redactor, "name", None)}

    @app.post("/decide")
    async def decide(req: Request):
        try:
            body = await req.json() if (await req.body()) else {}
        except Exception:
            body = {}
        if not isinstance(body, dict): body = {}
        cwd = str(body.get("cwd", "") or "")
        call = ToolCall(str(body.get("tool_name", "") or "Unknown"), body.get("tool_input") or {}, cwd=cwd,
                        branch=git_branch(cwd), session_id=str(body.get("session_id", "") or ""))
        d = await run_in_threadpool(engine.decide, call)
        dd = d.to_dict()
        bus.record_decision(dd, call.summary(), call.tool_name)
        return dd

    @app.get("/stats")
    async def stats():
        s = dict(bus.stats)
        s["avg_latency_ms"] = round(s["latency_ms_sum"] / s["decisions"], 1) if s["decisions"] else 0
        s["model"] = engine.model_name
        return s

    @app.get("/events")
    async def events():
        q = bus.subscribe()
        async def gen():
            try:
                yield f"data: {json.dumps({'type': 'hello', 'history': list(bus.history)[-50:], 'stats': bus.stats})}\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(ev)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                bus.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC / "index.html").read_text()

    if redactor is not None:
        from .proxy import attach_redact_routes
        attach_redact_routes(app)
    return app
