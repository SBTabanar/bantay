"""Anthropic reverse proxy: redacts PII/secrets in outbound /messages bodies, restores replies.

Forwards only the agent's own traffic to the configured upstream; adds no calls of its own.
Placeholder mappings stay in daemon memory (the Redactor); nothing here writes them to disk.
"""
from __future__ import annotations
import json, time
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from .redact import Redactor, StreamRestorer, SECRET_KINDS, PLACEHOLDER_RE

HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding", "keep-alive",
       "proxy-connection", "te", "trailer", "upgrade"}

def _transport():
    return None  # patched in tests (httpx default transport otherwise)

def _redact_text(x, r: Redactor, counter: list[int]):
    if not isinstance(x, str): return x
    res = r.redact(x); counter[0] += sum(res.counts.values()); return res.text

def _reapply_json(obj, r: Redactor):
    if isinstance(obj, str): return r.reapply(obj)
    if isinstance(obj, list): return [_reapply_json(x, r) for x in obj]
    if isinstance(obj, dict): return {k: _reapply_json(v, r) for k, v in obj.items()}
    return obj

def _redact_user_content(c, r: Redactor, n: list[int]):
    if isinstance(c, str): return _redact_text(c, r, n)
    if isinstance(c, list):
        for block in c:
            if not isinstance(block, dict): continue
            if block.get("type") == "text":
                block["text"] = _redact_text(block.get("text", ""), r, n)
            elif block.get("type") == "tool_result":
                tc = block.get("content")
                if isinstance(tc, str): block["content"] = _redact_text(tc, r, n)
                elif isinstance(tc, list):
                    for b in tc:
                        if isinstance(b, dict) and b.get("type") == "text":
                            b["text"] = _redact_text(b.get("text", ""), r, n)
    return c

def _remask_assistant_content(c, r: Redactor):
    """Assistant turns carry values we restored on the way in; put the placeholders back.
    thinking / redacted_thinking blocks are signed by the API and must stay byte-identical."""
    if isinstance(c, str): return r.reapply(c)
    if isinstance(c, list):
        for block in c:
            if not isinstance(block, dict): continue
            if block.get("type") == "text":
                block["text"] = r.reapply(block.get("text", ""))
            elif block.get("type") == "tool_use" and "input" in block:
                block["input"] = _reapply_json(block["input"], r)
    return c

def _texts(content) -> list[str]:
    """Human-readable texts of one message, in block order (prompt text, tool output), for the dashboard."""
    if isinstance(content, str): return [content]
    out = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict): continue
            if block.get("type") == "text": out.append(str(block.get("text", "")))
            elif block.get("type") == "tool_result":
                tc = block.get("content")
                if isinstance(tc, str): out.append(tc)
                elif isinstance(tc, list):
                    out.append("\n".join(str(b.get("text", "")) for b in tc if isinstance(b, dict) and b.get("type") == "text"))
    return out

def _snippet(redacted: str, r: Redactor, width: int = 1600) -> tuple[str, str]:
    """Window of the redacted text around its last placeholder, plus the same window restored."""
    hits = list(PLACEHOLDER_RE.finditer(redacted))
    if not hits: win = redacted[-width:]
    else:
        end = min(len(redacted), hits[-1].end() + width // 4)
        win = redacted[max(0, end - width):end]
    return r.restore(win), win

def _changed_pair(before: list[str], after: list[str]) -> tuple[str, str]:
    """The last block redaction changed (else the last block): what the dashboard shows."""
    pairs = list(zip(before, after))
    for b, a in reversed(pairs):
        if a != b: return b, a
    return pairs[-1] if pairs else ("", "")

def redact_messages_body(body: dict, r: Redactor) -> tuple[dict, int]:
    """Redact user text + tool results; re-mask assistant history. System prompt is left as-is.
    Returns (body, placeholders inserted by fresh detection)."""
    n = [0]
    for m in body.get("messages") or []:
        if not isinstance(m, dict): continue
        if m.get("role") == "user":
            m["content"] = _redact_user_content(m.get("content"), r, n)
        elif m.get("role") == "assistant":
            m["content"] = _remask_assistant_content(m.get("content"), r)
    return body, n[0]

class SSERestorer:
    """One StreamRestorer per content block, so a placeholder held back in one block never leaks into another."""
    def __init__(self, r: Redactor):
        self.r = r; self.blocks: dict[int, tuple[str, StreamRestorer]] = {}

    def for_delta(self, index: int, dtype: str) -> StreamRestorer:
        hit = self.blocks.get(index)
        if hit is None:
            hit = (dtype, StreamRestorer(self.r, json_escape=(dtype == "input_json_delta")))
            self.blocks[index] = hit
        return hit[1]

    def flush_event(self, index: int) -> str:
        """SSE event carrying whatever is still held back for this block ('' if nothing)."""
        hit = self.blocks.pop(index, None)
        if not hit: return ""
        dtype, s = hit; tail = s.flush()
        if not tail: return ""
        key = "partial_json" if dtype == "input_json_delta" else "text"
        ev = {"type": "content_block_delta", "index": index, "delta": {"type": dtype, key: tail}}
        return f"event: content_block_delta\ndata: {json.dumps(ev)}\n\n"

def restore_sse_line(line: str, s) -> str:
    """Restore placeholders inside one SSE `data:` line. `s` is a StreamRestorer or an SSERestorer."""
    if not line.startswith("data:"): return line
    try:
        ev = json.loads(line[5:].strip())
    except Exception:
        return line
    if not isinstance(ev, dict): return line
    d = ev.get("delta")
    if not isinstance(d, dict): return line
    dtype = d.get("type"); key = {"text_delta": "text", "input_json_delta": "partial_json"}.get(dtype)
    if key is None or not isinstance(d.get(key), str): return line
    st = s.for_delta(ev.get("index", 0), dtype) if isinstance(s, SSERestorer) else s
    before = d[key]; d[key] = st.feed(before)
    if d[key] == before: return line
    return "data: " + json.dumps(ev)

def restore_sse_event(block: str, s: SSERestorer) -> str:
    """Restore one SSE event (lines without the trailing blank line); returns it with its terminator.
    Before a content_block_stop, emits any held-back text for that block as its own delta event."""
    lines = block.split("\n")
    prefix = ""
    for ln in lines:
        if ln.startswith("data:"):
            try:
                ev = json.loads(ln[5:].strip())
            except Exception:
                ev = None
            if isinstance(ev, dict):
                if ev.get("type") == "content_block_stop":
                    prefix = s.flush_event(ev.get("index", 0))
                elif ev.get("type") == "message_stop":
                    prefix = "".join(s.flush_event(i) for i in list(s.blocks))
    return prefix + "\n".join(restore_sse_line(ln, s) for ln in lines) + "\n\n"

def _restore_json(obj, r: Redactor):
    if isinstance(obj, str): return r.restore(obj)
    if isinstance(obj, list): return [_restore_json(x, r) for x in obj]
    if isinstance(obj, dict): return {k: _restore_json(v, r) for k, v in obj.items()}
    return obj

def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"type": "error", "error": {"type": "api_error", "message": f"bantay proxy: {msg}"}}, status_code=status)

def attach_redact_routes(app: FastAPI):
    r: Redactor = app.state.redactor; bus = app.state.bus; upstream: str = app.state.upstream.rstrip("/")
    client = httpx.AsyncClient(transport=_transport(), timeout=httpx.Timeout(600, connect=10))
    app.state.proxy_client = client

    def _update_counts():
        secrets = sum(v for k, v in r.counters.items() if k in SECRET_KINDS)
        bus.stats["secrets_redacted"] = secrets; bus.stats["pii_redacted"] = sum(r.counters.values()) - secrets

    @app.post("/redact")
    async def redact_ep(req: Request):
        try:
            body = await req.json()
        except Exception:
            body = {}
        text = str(body.get("text", "")) if isinstance(body, dict) else ""
        res = await run_in_threadpool(r.redact, text)
        bus.stats["redactions"] += 1; _update_counts()
        bus.publish({"type": "redact", "source": "redact", "original": text[:2000], "redacted": res.text[:2000], "counts": res.counts})
        return {"text": res.text, "counts": res.counts}

    @app.api_route("/anthropic/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def anthropic_proxy(path: str, req: Request):
        raw = await req.body()
        headers = {k: v for k, v in req.headers.items() if k.lower() not in HOP}
        headers["accept-encoding"] = "identity"
        is_messages = path.rstrip("/").endswith("/messages") or path.rstrip("/") == "messages"
        carries_messages = is_messages or path.rstrip("/").endswith("/messages/count_tokens")
        send = raw
        if carries_messages and raw:
            try:
                body = json.loads(raw)
            except Exception:
                body = None   # not JSON: nothing structured to redact; upstream will reject it
            if isinstance(body, dict):
                try:
                    msgs = body.get("messages") or []
                    orig_texts = [t for m in msgs if isinstance(m, dict) for t in _texts(m.get("content"))]
                    t0 = time.perf_counter()
                    body, n = await run_in_threadpool(redact_messages_body, body, r)
                    redact_ms = round((time.perf_counter() - t0) * 1000, 1)
                    send = json.dumps(body).encode()
                except Exception as e:   # fail closed: never forward content we failed to redact
                    bus.publish({"type": "error", "where": "proxy-redact", "error": type(e).__name__})
                    return _err(500, "redaction failed; request not forwarded")
                # count_tokens carries the same conversation as the /messages call that follows, so it is
                # redacted silently: only /messages feeds the dashboard and the redaction counters.
                if n and is_messages:
                    bus.stats["redactions"] += 1; _update_counts()
                    msgs = body.get("messages") or []
                    red_texts = [t for m in msgs if isinstance(m, dict) for t in _texts(m.get("content"))]
                    _, red_last = _changed_pair(orig_texts, red_texts)
                    orig_last, red_last = _snippet(red_last, r)
                    bus.publish({"type": "redact", "source": "proxy", "original": orig_last[-2000:],
                                 "redacted": red_last, "placeholders": n, "redact_ms": redact_ms,
                                 "placeholder_names": sorted({m.group(0) for m in PLACEHOLDER_RE.finditer(send.decode("utf-8", "replace"))})[:50]})
        bus.stats["bytes_proxied"] += len(send)
        url = f"{upstream}/{path}" + (f"?{req.url.query}" if req.url.query else "")
        up = client.build_request(req.method, url, headers=headers, content=send)
        try:
            resp = await client.send(up, stream=True)
        except httpx.HTTPError as e:
            bus.publish({"type": "error", "where": "proxy-upstream", "error": type(e).__name__})
            return _err(502, f"upstream unreachable ({type(e).__name__})")
        ctype = resp.headers.get("content-type", "")
        raw_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
        decoded_headers = {k: v for k, v in raw_headers.items() if k.lower() not in ("content-encoding", "content-type")}

        if not is_messages:   # byte-for-byte passthrough
            async def raw_gen():
                try:
                    if resp.is_stream_consumed:   # body already buffered (e.g. mock transports)
                        yield resp.content
                    else:
                        async for chunk in resp.aiter_raw(): yield chunk
                finally:
                    await resp.aclose()
            return StreamingResponse(raw_gen(), status_code=resp.status_code, headers=raw_headers)

        if "text/event-stream" in ctype:
            s = SSERestorer(r)
            async def gen():
                buf = ""
                try:
                    async for chunk in resp.aiter_text():
                        buf = (buf + chunk).replace("\r\n", "\n")   # a CRLF may straddle chunks
                        while "\n\n" in buf:
                            block, buf = buf.split("\n\n", 1)
                            yield restore_sse_event(block, s)
                    if buf.strip(): yield restore_sse_event(buf.rstrip("\n"), s)
                    tail = "".join(s.flush_event(i) for i in list(s.blocks))
                    if tail: yield tail
                finally:
                    await resp.aclose()
            return StreamingResponse(gen(), status_code=resp.status_code, headers=decoded_headers, media_type="text/event-stream")

        data = await resp.aread(); await resp.aclose()
        if "application/json" in ctype:
            try:
                restored = _restore_json(json.loads(data), r)
                return Response(content=json.dumps(restored).encode(), status_code=resp.status_code,
                                headers=decoded_headers, media_type=ctype)
            except Exception:
                pass
        return Response(content=data, status_code=resp.status_code, headers=decoded_headers, media_type=ctype or None)
