from __future__ import annotations
import re, time
from .types import ToolCall, Decision
from . import rules

ALLOWLIST_RE = re.compile(
    r"^\s*(?:ls|pwd|whoami|date|uname|which|type|wc|du|df|tree|file|stat|head|tail|cat|less|grep|rg|find|fd|diff"
    r"|git\s+(?:status|diff|log|branch|show|remote|fetch|stash\s+list|rev-parse|blame)"
    r"|npm\s+(?:test|run\s+(?:test|lint|build|typecheck|dev)|ls|-v|--version)|pnpm\s+(?:test|lint|build)|yarn\s+(?:test|lint|build)"
    r"|pytest|python3?\s+-m\s+pytest|ruff|eslint|prettier|tsc|cargo\s+(?:test|build|check|clippy)|go\s+(?:test|build|vet)|make\s+(?:test|lint|build)"
    r"|node\s+-v|python3?\s+--version|echo\s+[^$`]*)\b[^|;&>`$]*$"
)

class Engine:
    def __init__(self, classifier=None, block_floor: float = 0.35):
        self.classifier, self.block_floor = classifier, block_floor

    @property
    def model_name(self) -> str:
        return getattr(self.classifier, "name", "none")

    def decide(self, call: ToolCall) -> Decision:
        t0 = time.perf_counter()
        try:
            d = self._decide(call)
        except Exception as e:
            d = Decision("ask", f"judge error: {type(e).__name__}: {e}", "error")
        d.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return d

    def _decide(self, call: ToolCall) -> Decision:
        if not isinstance(call.tool_input, dict):
            call.tool_input = {}
        d = rules.evaluate(call)
        if d:
            return d
        if call.tool_name == "Bash" and ALLOWLIST_RE.match(call.command):
            return Decision("allow", "Read-only or routine dev command.", "allowlist")
        if call.tool_name in ("Glob", "Grep", "LS", "TodoWrite", "Task"):
            return Decision("allow", "Read-only tool.", "allowlist")
        if self.classifier is None:
            return Decision("ask", "No local model loaded; defaulting to ask.", "error")
        label, probs = self.classifier.classify(call)
        if label == "allow" and probs.get("block", 0) >= self.block_floor:
            return Decision("ask", f"Model uncertain (block={probs['block']:.2f}); asking the human.", "model", probs=probs)
        reason = "Routine action inside the project." if label == "allow" else self.classifier.reason(call, label)
        return Decision(label, reason, "model", probs=probs)
