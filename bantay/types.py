from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from typing import Literal

Verdict = Literal["allow", "ask", "block"]
LABELS: tuple[str, ...] = ("allow", "ask", "block")

@dataclass
class ToolCall:
    tool_name: str
    tool_input: dict
    cwd: str = ""
    branch: str = ""
    session_id: str = ""

    def __post_init__(self):
        if not isinstance(self.tool_input, dict):
            self.tool_input = {"value": str(self.tool_input)}

    @property
    def command(self) -> str:
        # "value" holds a coerced non-dict input (see __post_init__)
        return str(self.tool_input.get("command", "") or self.tool_input.get("value", "") or "")

    @property
    def path(self) -> str:
        return str(self.tool_input.get("file_path", "") or self.tool_input.get("path", "") or "")

    def summary(self) -> str:
        t = self.tool_name
        if t == "Bash":
            return f"$ {self.command}"
        if t == "Read":
            return f"read {self.path}"
        if t in ("Write", "Edit", "MultiEdit"):
            return f"{t.lower().replace('multiedit','edit')} {self.path}"
        if t == "WebFetch":
            return f"fetch {self.tool_input.get('url', '')}"
        return f"{t} {json.dumps(self.tool_input)[:80]}"

    def text(self) -> str:
        """All text a rule might need to inspect."""
        parts = [self.command, self.path, str(self.tool_input.get("url", ""))]
        for k in ("content", "new_string", "old_string"):
            v = self.tool_input.get(k)
            if v:
                parts.append(str(v))
        for e in self.tool_input.get("edits", []) or []:
            if isinstance(e, dict):
                parts.append(str(e.get("new_string", "")))
        return "\n".join(p for p in parts if p)

@dataclass
class Decision:
    verdict: Verdict
    reason: str
    source: Literal["rule", "allowlist", "model", "error"]
    rule: str | None = None
    probs: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)
