import asyncio, time
from collections import deque

class EventBus:
    def __init__(self, history: int = 200):
        self.subs: set[asyncio.Queue] = set()
        self.history: deque = deque(maxlen=history)
        self.stats = {"decisions": 0, "allow": 0, "ask": 0, "block": 0, "redactions": 0,
                      "secrets_redacted": 0, "pii_redacted": 0, "bytes_to_cloud_for_decisions": 0,
                      "bytes_proxied": 0, "latency_ms_sum": 0.0, "started": time.time()}

    def publish(self, event: dict):
        event.setdefault("ts", time.time())
        self.history.append(event)
        for q in list(self.subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def record_decision(self, d: dict, summary: str, tool: str):
        s = self.stats
        s["decisions"] += 1; s[d["verdict"]] += 1; s["latency_ms_sum"] += d.get("latency_ms", 0)
        self.publish({"type": "decision", "tool": tool, "summary": summary, **d})

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.subs.add(q); return q

    def unsubscribe(self, q): self.subs.discard(q)
