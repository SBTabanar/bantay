from __future__ import annotations
import os, re
from dataclasses import dataclass
from typing import Callable
from .types import ToolCall, Decision, Verdict
from .secrets import SECRET_FILE_RE, find_secrets

@dataclass
class Rule:
    name: str
    tools: tuple[str, ...]
    verdict: Verdict
    reason: str
    pattern: re.Pattern | None = None
    predicate: Callable[[ToolCall], bool] | None = None

    def matches(self, call: ToolCall) -> bool:
        if call.tool_name not in self.tools:
            return False
        if self.predicate is not None:
            return bool(self.predicate(call))
        return bool(self.pattern and self.pattern.search(call.text()))

BASH = ("Bash",)
FILES = ("Write", "Edit", "MultiEdit", "NotebookEdit")
READS = ("Read",)
HOME = os.path.expanduser("~")

def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE | re.MULTILINE)

RM_RE = _rx(r"\brm\s+(?:-[a-z]*(?:r[a-z]*f|f[a-z]*r)[a-z]*|-r\s+-f|-f\s+-r|--recursive(?:\s+--force)?|-r\b)\s+(?P<targets>[^|;&]*)")
DANGEROUS_TARGET = _rx(r"(?:^|\s)(?:/|~|\$HOME|\*|\.\.|/(?:Users|home|etc|var|usr|opt|Library|System)\b)")

def _rm_outside(call: ToolCall) -> bool:
    for m in RM_RE.finditer(call.command):
        targets = m.group("targets").strip()
        if not targets:
            return True
        for t in targets.split():
            if t.startswith("-"):
                continue
            if DANGEROUS_TARGET.search(" " + t) or t in ("/", "~", "*", "."):
                return True
            if t.startswith("/") and call.cwd and not os.path.abspath(t).startswith(os.path.abspath(call.cwd) + os.sep):
                return True
    return False

def _rm_inside(call: ToolCall) -> bool:
    return bool(RM_RE.search(call.command)) and not _rm_outside(call)

def _force_push_main(call: ToolCall) -> bool:
    c = call.command
    m = re.search(r"\bgit\s+push\b(?P<args>[^|;&]*)", c)
    if not m or not re.search(r"(?:\s--force\b|\s-f\b)", m.group("args")):
        return False
    if "--force-with-lease" in c:
        return False
    if re.search(r"\b(?:main|master)\b", m.group("args")):
        return True
    positional = [a for a in m.group("args").split() if not a.startswith("-")]
    if len(positional) >= 2:  # explicit refspec names a non-main branch
        return False
    return call.branch in ("main", "master")

def _force_push_other(call: ToolCall) -> bool:
    return bool(re.search(r"\bgit\s+push\b.*(?:--force(?:-with-lease)?\b|\s-f\b)", call.command)) and not _force_push_main(call)

# `cp .env.example .env` seeds a new env file from a template; it reads no secrets.
TEMPLATE_COPY_RE = re.compile(r"\b(?:cp|mv)\s+(?:-\w+\s+)*\S*\.env\.(?:example|sample|template)\s+\S+")

def _secret_file_access(call: ToolCall) -> bool:
    return bool(SECRET_FILE_RE.search(TEMPLATE_COPY_RE.sub(" ", call.command)))

def _sudo_destructive(call: ToolCall) -> bool:
    return bool(re.search(r"\bsudo\b.*\b(?:rm|dd|mkfs|chmod|chown|kill|launchctl|shutdown|reboot)\b", call.command))

def _secret_in_write(call: ToolCall) -> bool:
    body = "\n".join(str(call.tool_input.get(k, "")) for k in ("content", "new_string")) + \
        "\n".join(str(e.get("new_string", "")) for e in (call.tool_input.get("edits") or []) if isinstance(e, dict))
    return bool(find_secrets(body))

def _outside_cwd(call: ToolCall) -> bool:
    p = call.path
    if not p or not p.startswith("/") or not call.cwd:
        return False
    ap = os.path.abspath(p)
    if ap.startswith(os.path.abspath(call.cwd) + os.sep) or ap.startswith("/tmp") or ap.startswith("/private/tmp"):
        return False
    return True

def _url_has_secret(call: ToolCall) -> bool:
    return bool(find_secrets(str(call.tool_input.get("url", ""))))

RULES: list[Rule] = [
    # --- exfiltration / credential access (block) ---
    Rule("pipe_to_shell", BASH, "block", "Downloads a script and pipes it straight into a shell.",
         _rx(r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b")),
    Rule("reverse_shell", BASH, "block", "Opens a reverse shell to a remote host.",
         _rx(r"/dev/tcp/|\bnc\b.*\s-e\s|\bncat\b.*\s-e\s|\bsocat\b.*exec")),
    Rule("upload_file", BASH, "block", "Uploads a local file to a remote host.",
         _rx(r"\b(?:curl|wget|http)\b[^|;&]*(?:\s-d\s*@|--data(?:-binary|-raw|-urlencode)?\s*@|\s-F\s+\S*=@|\s-T\s|--upload-file)")),
    Rule("secret_file_access", BASH, "block", "Touches a credentials or secrets file (.env, keys, SSH, history).",
         predicate=_secret_file_access),
    Rule("secret_file_git_add", BASH, "block", "Stages a secrets file into git.",
         _rx(r"\bgit\s+add\b.*\.env\b")),
    Rule("env_dump", BASH, "block", "Prints environment variables, which usually contain secrets.",
         _rx(r"(?:^|[;&|]\s*)(?:printenv|env|set)\s*(?:$|[;&|])|\becho\s+[\"']?\$\{?[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASS|CREDENTIAL)[A-Z0-9_]*")),
    Rule("keychain_dump", BASH, "block", "Reads passwords from the system keychain.",
         _rx(r"\bsecurity\s+(?:find-(?:generic|internet)-password|dump-keychain|export)\b")),
    # --- destruction (block) ---
    Rule("rm_outside_project", BASH, "block", "Recursive delete of a path outside the project (home, root, parent, wildcard).", predicate=_rm_outside),
    Rule("disk_or_system", BASH, "block", "Wipes a disk or shuts down the machine.",
         _rx(r"\b(?:mkfs(?:\.\w+)?|diskutil\s+(?:erase|reformat)|shutdown|reboot|halt)\b|\bdd\s+if=|:\(\)\s*\{\s*:\|:&\s*\};:")),
    Rule("sudo_destructive", BASH, "block", "Destructive command run as root.", predicate=_sudo_destructive),
    Rule("chmod_777", BASH, "block", "Makes files world-writable.", _rx(r"\bchmod\s+(?:-R\s+)?(?:a\+rwx|0?777)\b")),
    Rule("sql_destroy", BASH, "block", "Drops or truncates a database table, or deletes every row.",
         _rx(r"\b(?:DROP\s+(?:TABLE|DATABASE|SCHEMA)|TRUNCATE(?:\s+TABLE)?\s+\w+|DELETE\s+FROM\s+\w+\s*(?:;|[\"']|$))")),
    Rule("force_push_main", BASH, "block", "Force-pushes over main/master.", predicate=_force_push_main),
    # --- ask ---
    Rule("force_push", BASH, "ask", "Force push rewrites remote history.", predicate=_force_push_other),
    Rule("rm_in_project", BASH, "ask", "Recursive delete inside the project.", predicate=_rm_inside),
    Rule("git_history_loss", BASH, "ask", "Discards local changes or history.",
         _rx(r"\bgit\s+(?:reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s+\.|restore\s+\.|branch\s+-D|stash\s+(?:drop|clear)|push\s+--delete)")),
    Rule("db_reset", BASH, "ask", "Resets a database.", _rx(r"\b(?:supabase\s+db\s+reset|prisma\s+migrate\s+reset|rails\s+db:drop|flask\s+db\s+downgrade|alembic\s+downgrade)\b")),
    Rule("deploy_or_publish", BASH, "ask", "Publishes or deploys to a live environment.",
         _rx(r"\b(?:npm\s+publish|yarn\s+publish|pnpm\s+publish|vercel\b.*--prod|vercel\s+deploy|gh\s+release\s+create|terraform\s+(?:apply|destroy)|kubectl\s+(?:delete|apply|rollout)|eas\s+submit|fly\s+deploy|aws\s+\w+\s+delete|gcloud\s+\w+\s+delete|cargo\s+publish|twine\s+upload)\b")),
    Rule("sudo", BASH, "ask", "Runs as root.", _rx(r"\bsudo\b")),
    Rule("remote_copy", BASH, "ask", "Copies files to or from a remote host.", _rx(r"\b(?:scp|rsync|sftp)\b.*\S+@\S+:")),
    Rule("persistence", BASH, "ask", "Changes cron, launchd, or shell startup files.",
         _rx(r"\bcrontab\b|\blaunchctl\s+(?:load|bootstrap)|LaunchAgents|>>?\s*~/\.(?:zshrc|bashrc|zprofile|bash_profile)")),
    Rule("kill_broad", BASH, "ask", "Kills many processes at once.", _rx(r"\bkill\s+-9\s+-1\b|\bkillall\b|\bpkill\s+-f\b")),
    # --- file tools ---
    Rule("read_secret_file", READS, "block", "Reads a credentials or secrets file.", predicate=lambda c: bool(SECRET_FILE_RE.search(c.path))),
    Rule("write_secret", FILES, "block", "Writes a secret-shaped string (API key, token, private key) into a file.", predicate=_secret_in_write),
    Rule("write_system_or_auth", FILES, "block", "Writes to a system or SSH authorization file.",
         predicate=lambda c: bool(re.search(r"^/etc/|/\.ssh/authorized_keys$|^/System/|^/usr/", c.path))),
    Rule("write_shell_or_ci", FILES, "ask", "Changes shell startup, CI workflow, or git hooks.",
         predicate=lambda c: bool(re.search(r"/\.(?:zshrc|bashrc|zprofile|bash_profile|profile)$|/\.github/workflows/|/\.git/hooks/|/\.claude/settings", c.path))),
    Rule("write_outside_project", FILES, "ask", "Writes a file outside the project directory.", predicate=_outside_cwd),
    Rule("fetch_with_secret", ("WebFetch",), "block", "URL contains a secret.", predicate=_url_has_secret),
]

def evaluate(call: ToolCall) -> Decision | None:
    try:
        for rule in RULES:
            if rule.matches(call):
                return Decision(rule.verdict, rule.reason, "rule", rule=rule.name)
    except Exception as e:  # rules must never crash the judge
        return Decision("ask", f"rule engine error: {e}", "error")
    return None
