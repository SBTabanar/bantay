from __future__ import annotations
import re, time
from .types import ToolCall, Decision
from . import rules

# Commands that skip the model. Joins are [ \t] only, so a newline can never chain a second command.
# Each head ends at whitespace or end of string (not \b, which matches before "-": `git fetch-pack`,
# `npm run build-and-deploy`). The tail excludes pipes, chaining, redirects, substitution and grouping.
# Left out on purpose: find/fd (-delete/-exec), less (shell escapes), tree (-o writes a file),
# git fetch (network; git accepts abbreviated --upload-pack). git branch/remote and version probes
# are exact listing forms.
_END = r"(?=[ \t]|\Z)"
_SAFE_TAIL = r"[^|;&<>`$(){}\n\r]*"
_WITH_ARGS = (
    r"(?:ls|pwd|whoami|date|uname|which|type|wc|du|df|file|stat|head|tail|cat|grep|rg|diff|echo"
    r"|git[ \t]+(?:status|diff|log|show|stash[ \t]+list|rev-parse|blame)"
    r"|npm[ \t]+(?:test|run[ \t]+(?:test|lint|build|typecheck|dev)|ls)|pnpm[ \t]+(?:test|lint|build)|yarn[ \t]+(?:test|lint|build)"
    r"|pytest|python3?[ \t]+-m[ \t]+pytest|ruff|eslint|prettier|tsc|cargo[ \t]+(?:test|build|check|clippy)|go[ \t]+(?:test|build|vet)"
    r"|make[ \t]+(?:test|lint|build))" + _END + _SAFE_TAIL
)
_EXACT = (
    r"(?:git[ \t]+branch(?:[ \t]+(?:-a|-r|-v|-vv|-l|--list|--all|--remotes|--verbose|--show-current))*"
    r"|git[ \t]+remote(?:[ \t]+(?:-v|--verbose))?"
    r"|node[ \t]+(?:-v|--version)|python3?[ \t]+(?:-V|--version)|npm[ \t]+(?:-v|--version))[ \t]*"
)
# Options that make an otherwise read-only tool run a program, set config, or write a file.
# git accepts any unique prefix of a long option, so --output is matched from --ou on.
_OPTION_EXEC = (
    r"(?!.*(?:--pre\b|--upload-pack|--receive-pack|--exec\b|--ou(?:t(?:p(?:u(?:t)?)?)?)?(?=[= \t]|\Z)"
    r"|(?:^|[ \t])-c[ \t]*[\w.-]+="
    # The shell strips quotes and backslashes before the tool runs (--p''re, --"pre", --pr\e, '--pre'),
    # so any option-looking token (optionally after leading quotes) that contains one is rejected.
    r"|(?:^|[ \t])['\"\\]*-[^ \t]*['\"\\]))"
)
# Policy: the allowlist covers routine dev commands that may run project code by design (npm test,
# make, pytest, cargo build). It is a latency shortcut for commands a developer would run without
# thinking, not a sandbox, and the rules layer still runs first on every command.
ALLOWLIST_RE = re.compile(r"^[ \t]*" + _OPTION_EXEC + r"(?:" + _WITH_ARGS + r"|" + _EXACT + r")\Z")

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
        if label == "allow":
            reason = "Routine action inside the project."
        else:
            try:
                reason = self.classifier.reason(call, label)
            except Exception:  # a failed explanation must not downgrade a block to ask
                reason = f"Local model judged this {label} (reason unavailable)."
        return Decision(label, reason, "model", probs=probs)
