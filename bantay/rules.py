from __future__ import annotations
import json, os, posixpath, re, shlex
from dataclasses import dataclass, replace
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
READS = ("Read", "Grep", "Glob", "LS")  # LS: OpenCode's `list`
HOME = os.path.expanduser("~")

def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE | re.MULTILINE)

# --- shell parsing helpers ---

def _segment_spans(cmd: str) -> list[tuple[int, int]]:
    """Spans of the simple commands in a command line, split on ; && || | & and newlines outside quotes."""
    spans, start, i, quote, n = [], 0, 0, None, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = None
        elif ch == "\\":
            i += 1
        elif ch in "'\"":
            quote = ch
        elif ch in ";|\n" or (ch == "&" and cmd[i - 1:i] != ">" and cmd[i + 1:i + 2] != ">"):
            spans.append((start, i))
            if cmd[i:i + 2] in ("&&", "||"):
                i += 1
            start = i + 1
        i += 1
    spans.append((start, n))
    return spans

def _segments(cmd: str) -> list[str]:
    return [cmd[a:b] for a, b in _segment_spans(cmd)]

def _words(seg: str) -> list[str]:
    try:
        words = shlex.split(seg)
    except ValueError:
        words = seg.split()
    while words and (words[0] == "sudo" or re.match(r"^[A-Za-z_]\w*=", words[0])):
        words = words[1:]
    return words

def _git_subcommand(words: list[str]) -> tuple[str, list[str]] | None:
    """(subcommand, args) for `git [global options] <subcommand> ...`."""
    if not words or os.path.basename(words[0]) != "git":
        return None
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
    return (words[i], words[i + 1:]) if i < len(words) else None

# wrapper -> its options that take a value
WRAPPERS: dict[str, set[str]] = {
    "sudo": {"-u", "-g", "-C", "-h", "-p"}, "env": {"-u", "-C"}, "command": set(), "exec": {"-a"},
    "nohup": set(), "time": set(), "nice": {"-n"}, "timeout": {"-s", "-k"}, "caffeinate": {"-t", "-w"},
    "xargs": {"-n", "-I", "-P", "-L", "-d", "-E", "-s", "-a"},
}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}

HEREDOC_OP_RE = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\([^\s;&|<>()]+)|([^\s;&|<>()'\"`]+))")

def _scan_substitutions(cmd: str, lo: int, hi: int, heredoc: bool) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(substitution body spans, heredoc body spans) in cmd[lo:hi]. Bodies of $(...) and `...` are found at
    any nesting depth, inside double quotes and unquoted heredoc bodies too, never inside single quotes or
    a quoted-delimiter heredoc body (both literal). heredoc=True scans an unquoted heredoc body."""
    bodies, cuts, pending = [], [], []
    stack: list[tuple[str, int]] = [("H", lo)] if heredoc else []
    i = lo
    while i < hi:
        ch, top = cmd[i], (stack[-1][0] if stack else None)
        if top == "'":
            if ch == "'":
                stack.pop()
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        quoting = top in ('"', "H")
        if quoting and ch == '"' and top == '"':
            stack.pop(); i += 1
            continue
        if not quoting and ch in "'\"":
            stack.append((ch, i)); i += 1
            continue
        if ch == "`":
            if top == "`":
                bodies.append((stack.pop()[1], i))
            else:
                stack.append(("`", i + 1))
            i += 1
            continue
        if cmd.startswith("$(", i) and not cmd.startswith("$((", i):
            stack.append(("$(", i + 2)); i += 2
            continue
        if quoting:
            i += 1
            continue
        if ch == "(":
            stack.append(("(", i))
        elif ch == ")" and top in ("$(", "("):
            start = stack.pop()[1]
            if top == "$(":
                bodies.append((start, i))
        elif cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = HEREDOC_OP_RE.match(cmd, i)
            if m:
                delim = next(g for g in m.groups()[1:] if g is not None)
                pending.append((delim, bool(m.group(1)), m.group(5) is None))
                i = m.end()
                continue
        elif ch == "\n" and pending:
            pos = i + 1
            for delim, dash, quoted in pending:
                start, body_end = pos, hi
                while pos < hi:
                    end = cmd.find("\n", pos, hi)
                    end = hi if end < 0 else end
                    line = cmd[pos:end]
                    pos = end + 1
                    if (line.lstrip("\t") if dash else line) == delim:
                        body_end = end - len(line)
                        break
                cuts.append((start, body_end))
                if not quoted:
                    b, c = _scan_substitutions(cmd, start, body_end, True)
                    bodies += b; cuts += c
            pending = []
            i = min(pos, hi)
            continue
        i += 1
    bodies += [(start, hi) for kind, start in stack if kind in ("$(", "`")]  # unterminated
    return bodies, cuts

def _substitution_bodies(cmd: str) -> list[str]:
    """Text of every command substitution body in cmd (see _scan_substitutions), heredoc bodies removed."""
    bodies, cuts = _scan_substitutions(cmd, 0, len(cmd), False)
    out = []
    for a, b in bodies:
        parts, last = [], a
        for ca, cb in sorted(c for c in cuts if a <= c[0] and c[1] <= b):
            if ca >= last:
                parts.append(cmd[last:ca]); last = cb
        parts.append(cmd[last:b])
        out.append("".join(parts))
    return out

def _commands_ex(cmd: str, depth: int = 0, sudo: bool = False, subs: bool = True) -> list[tuple[list[str], bool]]:
    """(words, runs_as_root) of every simple command, with subshell parens and wrappers (see WRAPPERS, plus
    VAR=x) removed, `sh -c "..."` / `eval "..."` scripts expanded recursively, and the commands inside
    $(...) / `...` substitutions added. A command fed by xargs is reported without the arguments xargs
    would append."""
    out = []
    for seg in _segments(cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        words = [w.lstrip("(").rstrip(");") for w in words]
        words = [w for w in words if w]
        root = sudo
        while words:
            name = os.path.basename(words[0])
            if name in WRAPPERS:
                root = root or name == "sudo"
                words = words[1:]
                while words and words[0].startswith("-") and words[0] != "-":
                    words = words[2:] if words[0] in WRAPPERS[name] else words[1:]
                if name == "timeout" and words:
                    words = words[1:]  # the duration
            elif re.match(r"^[A-Za-z_]\w*=", words[0]):
                words = words[1:]
            else:
                break
        if words and words[0] == "eval":
            if depth < 3:
                out += _commands_ex(" ".join(words[1:]), depth + 1, root)
            continue
        if words and os.path.basename(words[0]) in SHELLS:
            i = next((i for i, w in enumerate(words[1:], 1)
                      if w.startswith("-") and not w.startswith("--") and "c" in w), None)
            if i is not None and i + 1 < len(words) and depth < 3:
                out += _commands_ex(words[i + 1], depth + 1, root)
                continue
        if words:
            out.append((words, root))
    if subs and depth < 3:
        for body in _substitution_bodies(cmd):
            out += _commands_ex(body, depth + 1, sudo, subs=False)  # bodies are already flattened
    return out

def _commands(cmd: str, depth: int = 0) -> list[list[str]]:
    return [words for words, _ in _commands_ex(cmd, depth)]

GREP_CMDS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
GREP_VALUE_FLAGS = {"-A", "-B", "-C", "-m", "-g", "-t", "-T", "-f", "--file", "--glob", "--type", "--max-count", "--context"}

def _split_search_pattern(args: list[str]) -> tuple[list[str], list[str]]:
    """(grep/rg args minus the search pattern, the pattern words). Files and flags are kept."""
    out, patterns, i = [], [], 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "--regexp"):
            patterns.append(args[i + 1] if i + 1 < len(args) else "")
            i += 2
            continue
        if a.startswith("--regexp=") or (a.startswith("-e") and len(a) > 2):
            patterns.append(a)
            i += 1
            continue
        if a == "--":
            rest = args[i + 1:]
            if not patterns and rest:
                patterns.append(rest[0])
                rest = rest[1:]
            return out + rest, patterns
        if a.startswith("-"):
            out.append(a)
            if a in GREP_VALUE_FLAGS and i + 1 < len(args):
                out.append(args[i + 1])
                i += 1
        elif not patterns:
            patterns.append(a)
        else:
            out.append(a)
        i += 1
    return out, patterns

def _strip_grep(head: list[str], args: list[str]) -> list[str] | None:
    """grep words without the search pattern, or None when the pattern itself runs code."""
    kept, patterns = _split_search_pattern(args)
    if any(x in w for w in patterns for x in SUBSTITUTIONS):
        return None
    return head + kept

def _redirections(args: list[str]) -> list[str]:
    """Only the output redirections (and their targets) of an echo/printf."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if ">" in a:
            op = re.match(r">>?&?", a[a.index(">"):]).group(0)
            target = a[a.index(">") + len(op):]
            if not target and i + 1 < len(args):
                target = args[i + 1]
                i += 1
            out += [op, target]
        i += 1
    return out

SUBSTITUTIONS = ("$(", "`", "<(", ">(")

# Claude Code commits with `git commit -m "$(cat <<'EOF'\n<message>\nEOF\n)"`. For the rules scan only
# (never the allowlist), that message is treated as a literal so its words (shutdown, DROP TABLE) do not
# trip a rule. The shape is strict, and anything else is scanned raw (the safe direction): the command must
# begin with `git commit` (nothing before it can rebind `cat` via alias/hash/PATH), the delimiter must be
# quoted, and the body may hold none of $ ` " ' \ ( ) (bash 3.2 ends the substitution at an unbalanced `)`
# and then evaluates what follows) nor a line equal to the delimiter (which would end the heredoc early).
HEREDOC_MSG_RE = re.compile(
    r"\A(?P<pre>[ \t]*git[ \t]+commit(?=[ \t])[^\n|;&'\"\\`$#<>(){}]*?[ \t](?:-[asnvq]*m|--message)[ \t]*=?[ \t]*)"
    r"\"\$\([ \t]*cat[ \t]+<<(?P<dash>-?)[ \t]*(?P<q>['\"])(?P<d>[A-Za-z_]\w*)(?P=q)[ \t]*\n"
    r"(?P<body>(?:.*\n)*?)\t*(?P=d)\n[ \t]*\)\"")
UNSAFE_BODY_RE = re.compile(r"[$`\"'\\()]")
COMMIT_MSG_PLACEHOLDER = "heredoc-message"

def collapse_heredoc_messages(cmd: str) -> str:
    """`cmd` with a leading literal heredoc commit message replaced by a placeholder word (rules scan only)."""
    m = HEREDOC_MSG_RE.match(cmd)
    if not m:
        return cmd
    body, d = m.group("body"), m.group("d")
    if UNSAFE_BODY_RE.search(body) or any(line.strip() == d for line in body.split("\n")):
        return cmd
    if not m.group("dash") and re.search(r"(?m)^\t+" + re.escape(d) + r"$", m.group(0)):
        return cmd  # without <<- a tab-indented delimiter does not end the heredoc
    if not COMMIT_SUFFIX_RE.fullmatch(cmd[m.end():]):
        return cmd  # a tail like `&& git log -1 --format=%B | sh` could run the message
    return m.group("pre") + COMMIT_MSG_PLACEHOLDER + " " + cmd[m.end():]  # space: a glued `cmd` stays a separate word

# Heredoc commits the strict collapse refuses (a chained `git add … &&` prefix, or a body holding ( ) ' as in
# `feat(server): …` or Claude Code's `[Claude Code](https://…)` trailer) are still scanned raw. `evaluate`
# uses this span only to soften a word-level block (shutdown, DROP TABLE, .env) found inside the body to ask.
# The body counts as text only when nothing before the commit can rebind `cat` (only `git` / `cd` commands
# of plain words), the `"$(` sits at top level (no quote before it), the delimiter is quoted, no body line
# starts with the delimiter, and the body holds none of $ ` " \. Checked on bash 3.2 and zsh 5.9: with those
# absent, a `)` that ends the substitution early leaves the rest of the body inside the outer double quotes
# (still a literal), and an apostrophe is a syntax error that runs nothing.
COMMIT_PREFIX_RE = re.compile(r"(?:[ \t]*(?:(?:git|cd)(?:[ \t]+[\w./@:+,%-]+)*[ \t]*)?(?:&&|;|\n))*[ \t]*")
# What may follow the closing `)"`: nothing, or plain git/cd commands joined by && ; or newline. A pipe
# (`| sh`) or anything else could run the message, so the soft downgrade does not apply.
COMMIT_SUFFIX_RE = re.compile(r"(?:[ \t]*(?:&&|;|\n)[ \t]*(?:(?:git|cd)(?:[ \t]+[\w./@:+,%-]+)*)?)*[ \t;]*\n?")
HEREDOC_COMMIT_RE = re.compile(
    r"(?P<pre>git[ \t]+commit(?=[ \t])[^\n|;&'\"\\`$#<>(){}]*?[ \t](?:-[asnvq]*m|--message)[ \t]*=?[ \t]*)"
    r"\"\$\([ \t]*cat[ \t]+<<(?P<dash>-?)[ \t]*(?P<q>['\"])(?P<d>[A-Za-z_]\w*)(?P=q)[ \t]*\n"
    r"(?P<body>(?:.*\n)*?)(?P<tabs>\t*)(?P=d)\n[ \t]*\)\"")
TEXT_BODY_UNSAFE_RE = re.compile(r"[$`\"\\]")

def without_commit_heredoc_body(cmd: str) -> str | None:
    """`cmd` with a literal heredoc commit message (see HEREDOC_COMMIT_RE) replaced by a placeholder word,
    or None when the command has no such message."""
    m = HEREDOC_COMMIT_RE.search(cmd)
    if not m or not COMMIT_PREFIX_RE.fullmatch(cmd[:m.start()]):
        return None
    body, d = m.group("body"), m.group("d")
    if TEXT_BODY_UNSAFE_RE.search(body) or any(re.match(r"\t*" + re.escape(d) + r"(?!\w)", line) for line in body.split("\n")):
        return None
    if m.group("tabs") and not m.group("dash"):
        return None  # without <<- a tab-indented delimiter does not end the heredoc
    return cmd[:m.end("pre")] + COMMIT_MSG_PLACEHOLDER + " " + cmd[m.end():]

def commit_heredoc_tail_ok(cmd: str) -> bool:
    """Is the text after the heredoc commit's closing `)"` empty or plain git/cd commands (COMMIT_SUFFIX_RE)?"""
    m = HEREDOC_COMMIT_RE.search(cmd)
    return bool(m and COMMIT_SUFFIX_RE.fullmatch(cmd[m.end():]))

def _commit_message_flag(w: str) -> int:
    """Words consumed by a commit message option at w (2 = option + value, 1 = attached value), else 0."""
    if w in ("-m", "--message") or re.fullmatch(r"-[asnvq]*m", w):
        return 2
    if w.startswith("--message=") or re.match(r"-[asnvq]*m", w):
        return 1
    return 0

def _literal_scan_text(cmd: str) -> str:
    """The command with grep/rg search patterns and echo/printf literals removed, so a string that
    is only searched for or printed (e.g. `grep -rn "DROP TABLE"`) does not trip a rule. A grep whose
    pattern holds a command/process substitution, and an echo containing one or piped onward, are
    left untouched."""
    cmd = collapse_heredoc_messages(cmd)
    out, last = [], 0
    for a, b in _segment_spans(cmd):
        seg = cmd[a:b]
        words = _words(seg)
        if not words:
            continue
        name = os.path.basename(words[0])
        new = None
        if name in GREP_CMDS:
            new = _strip_grep(words[:1], words[1:])
        elif words[:2] == ["git", "grep"]:
            new = _strip_grep(words[:2], words[2:])
        elif words[:2] == ["git", "commit"] and not any(x in seg for x in SUBSTITUTIONS):
            new, i = words[:2], 2
            while i < len(words):
                step = _commit_message_flag(words[i])
                if step:
                    i += step
                else:
                    new.append(words[i]); i += 1
        elif name in ("echo", "printf") and not any(x in seg for x in SUBSTITUTIONS) \
                and not (cmd[b:b + 1] == "|" and cmd[b:b + 2] != "||"):
            new = [words[0]] + _redirections(words[1:])
        if new is not None:
            out += [cmd[last:a], " " + " ".join(new) + " "]
            last = b
    out.append(cmd[last:])
    return "".join(out)

# --- rm ---

RM_SPLIT_RE = re.compile(r"[;&|()\n`]")  # also splits inside quotes, so `bash -c "cd x && rm -rf ~"` and `x`rm -rf ~`` are seen
HOME_VAR_RE = re.compile(r"\$\{HOME\}|\$HOME(?!\w)")
REDIRECT_RE = re.compile(r"^(?:\d*|&)[<>]")
BARE_REDIRECT_RE = re.compile(r"(?:\d*|&)(?:>>?|<)&?")

PWD_RE = re.compile(r"\$\(\s*pwd\s*\)|`\s*pwd\s*`|\$\{PWD\}|\$PWD(?!\w)")
SUBST_TARGET = "__bantay_subst__"

def _subst_target(prefix: str) -> str:
    """An rm target whose tail is an unknown expansion: inside only when its literal prefix names an
    in-project path (build/$(x), dist-${V}); a bare or dots-only prefix ($(x), ./$(x), ../$(x)) is outside."""
    return prefix + SUBST_TARGET if prefix.replace(".", "").replace("/", "") else "/"

def _rm_invocations(call: ToolCall) -> list[list[str]]:
    """Normalized targets of every recursive rm (quotes stripped, $HOME/${HOME} -> ~, $(pwd)/$PWD -> cwd).
    A target holding a $(...), `...` or ${...} expansion is resolved by _subst_target."""
    found, text = [], _literal_scan_text(call.command)
    text = PWD_RE.sub(lambda m: call.cwd or ".", text)
    seps = list(RM_SPLIT_RE.finditer(text))
    starts = [0] + [m.end() for m in seps]
    for k, a in enumerate(starts):
        b = seps[k].start() if k < len(seps) else len(text)
        part, sep = text[a:b], (seps[k].group(0) if k < len(seps) else "")
        # does an expansion open right where this part was cut? ($( or an opening backtick)
        opens = (sep == "(" and part.endswith("$")) or (sep == "`" and text[:b].count("`") % 2 == 0)
        words = [HOME_VAR_RE.sub("~", re.sub(r"[\"'`\\]", "", w)) for w in part.split()]
        idx = next((i for i, w in enumerate(words) if w == "rm" or w.endswith("/rm")), None)
        if idx is None:
            continue
        recursive, targets, opts_done, skip, last_target = False, [], False, False, False
        for w in words[idx + 1:]:
            last_target = False
            if skip:
                skip = False
            elif REDIRECT_RE.match(w):
                skip = bool(BARE_REDIRECT_RE.fullmatch(w))
            elif not opts_done and w == "--":
                opts_done = True
            elif not opts_done and w.startswith("-"):
                if w == "--recursive" or (not w.startswith("--") and ("r" in w or "R" in w)):
                    recursive = True
            elif w:
                targets.append(_subst_target(w[:w.index("${")]) if "${" in w else w)
                last_target = True
        if opens:
            glued = last_target and not part[-1:].isspace()
            if glued and targets[-1] != "/":
                targets[-1] = _subst_target(targets[-1].removesuffix("$"))
            else:
                targets.append("/")
        if recursive:
            found.append(targets)
    return found

def _inside(abspath: str, cwd: str) -> bool:
    root = os.path.normpath(cwd)
    return root != "/" and abspath.startswith(root + "/")

def _rm_target_outside(t: str, cwd: str) -> bool:
    if t.startswith("~"):
        return True
    bare = t
    while bare.startswith("./"):
        bare = bare[2:]
    if bare.startswith(("*", ".*")):
        return True
    if t.startswith("/"):
        ap = os.path.normpath(t)
        if ap.startswith(("/tmp/", "/private/tmp/")):
            return False
        return not (cwd and _inside(ap, cwd))
    if not cwd:
        return bare in ("", ".", "..") or bare.startswith("../")
    return not _inside(os.path.normpath(os.path.join(cwd, t)), cwd)

def _rm_outside(call: ToolCall) -> bool:
    return any(not ts or any(_rm_target_outside(t, call.cwd) for t in ts) for ts in _rm_invocations(call))

def _rm_inside(call: ToolCall) -> bool:
    return bool(_rm_invocations(call)) and not _rm_outside(call)

# --- git ---

def _is_main(ref: str, branch: str) -> bool:
    ref = ref.removeprefix("refs/heads/")
    if ref in ("HEAD", "@"):
        ref = branch
    return ref in ("main", "master")

def _pushes(call: ToolCall) -> list[tuple[bool, bool, bool]]:
    """(hard_force, lease, hits_main) for every `git push` in the command."""
    out = []
    for words in _commands(call.command):
        sub = _git_subcommand(words)
        if not sub or sub[0] != "push":
            continue
        hard = lease = all_refs = skip = False
        positional = []
        for a in sub[1]:
            if skip:
                skip = False
            elif a in ("-o", "--push-option"):
                skip = True
            elif a.startswith("--force-with-lease"):
                lease = True
            elif a == "--force":
                hard = True
            elif a in ("--all", "--mirror"):
                all_refs = True
            elif a.startswith("-"):
                hard = hard or (not a.startswith("--") and "f" in a[1:])
            else:
                positional.append(a)
        refs = positional[1:]
        hard = hard or any(r.startswith("+") for r in refs)
        dests = [r.lstrip("+").split(":")[-1] for r in refs] or [call.branch]
        out.append((hard, lease, all_refs or any(_is_main(d, call.branch) for d in dests)))
    return out

def _force_push_main(call: ToolCall) -> bool:
    return any((hard or lease) and main for hard, lease, main in _pushes(call))

def _force_push_other(call: ToolCall) -> bool:
    return any(hard or lease for hard, lease, _ in _pushes(call)) and not _force_push_main(call)

def _secret_file_git_add(call: ToolCall) -> bool:
    for words in _commands(call.command):
        sub = _git_subcommand(words)
        if sub and sub[0] == "add" and SECRET_FILE_RE.search(" " + " ".join(sub[1])):
            return True
    return False

# --- guardrail self-protection ---

# Guard paths match case-insensitively: macOS volumes are, so .Claude/Settings.json is the same file.
OPENCODE_GUARD_RE = re.compile(r"(?:^|/)(?:\.opencode|\.config/opencode)/plugins?(?:/|$)", re.I)
GUARD_FILE_RE = re.compile(r"(?:^|/)\.claude/settings(?:\.local)?\.json$|(?:^|/)\.claude/hooks/", re.I)
# a shell word naming the .claude dir, a settings file, or the hooks dir/contents
GUARD_WORD_RE = re.compile(r"(?:^|/)\.claude(?:/(?:settings(?:\.local)?\.json|hooks(?:/\S*)?))?/?$", re.I)
REL_GUARD_RE = re.compile(r"^(?:\./)?(?:settings(?:\.local)?\.json|hooks(?:/\S*)?)/?$", re.I)  # after `cd .claude`
GUARD_TEXT_RE = re.compile(r"\.claude/(?:settings(?:\.local)?\.json|hooks\b)", re.I)
CODE_WRITE_RE = re.compile(
    r"open\s*\([^)]*?['\"][wax]\+?b?['\"]|(?<!stdout)(?<!stderr)\.write(?:_text|_bytes)?\s*\(|writeFile(?:Sync)?\s*\("
    r"|\bos\.(?:remove|unlink|rename|replace)\b|\bshutil\.\w+|\.unlink\s*\(|\.rename\s*\(")
BANTAY_ENV_RE = re.compile(r"\bexport\s+BANTAY_URL=|\bunset\s+(?:-v\s+)?BANTAY_URL\b|\bBANTAY_ALLOW_MODE=[\"']?allow\b")
MUTATE_ANY = {"rm", "rmdir", "unlink", "shred", "truncate", "chmod", "chown", "chflags", "tee", "mv"}
MUTATE_DEST = {"cp", "ln", "install", "rsync"}  # only the last argument (destination) is written
IN_PLACE = {"sed", "perl", "ruby"}

def _guard_bash(call: ToolCall) -> bool:
    text = _literal_scan_text(call.command)
    if BANTAY_ENV_RE.search(text):
        return True
    if (GUARD_TEXT_RE.search(text) or OPENCODE_GUARD_RE.search(text)) and CODE_WRITE_RE.search(text):  # e.g. a python heredoc rewriting settings
        return True
    where = None  # None, "claude" or "hooks": tracks `cd .claude[/hooks]`

    def guarded(w: str) -> bool:
        normalized = os.path.normpath(w).casefold()
        if GUARD_WORD_RE.search(normalized) or OPENCODE_GUARD_RE.search(normalized) or re.search(r"(?:^|/)(?:\.opencode|\.config/opencode)$", normalized):
            return True
        if where == "claude":
            return bool(REL_GUARD_RE.match(w))
        if where == "opencode":
            return bool(re.match(r"^(?:\./)?plugins?(?:/|$)", w, re.I))
        return where in ("hooks", "plugins") and not w.startswith(("-", "/", "~", "&", "$"))

    for words in _commands(call.command):
        name, args = os.path.basename(words[0]), words[1:]
        if name == "cd":
            t = os.path.normpath(args[0]).casefold() if args else ""
            where = "hooks" if re.search(r"(?:^|/)\.claude/hooks/?$", t) else \
                "claude" if re.search(r"(?:^|/)\.claude/?$", t) else None
            if re.search(r"(?:^|/)(?:\.opencode|\.config/opencode)/plugins?/?$", t):
                where = "plugins"
            elif re.search(r"(?:^|/)(?:\.opencode|\.config/opencode)/?$", t):
                where = "opencode"
            continue
        for i, w in enumerate(words):
            if ">" in w:
                target = w.rsplit(">", 1)[1] or (words[i + 1] if i + 1 < len(words) else "")
                if target and guarded(target):
                    return True
        pos = [a for a in args if not a.startswith("-")]
        if name in MUTATE_ANY and any(guarded(a) for a in pos):
            return True
        if name in MUTATE_DEST and pos and guarded(pos[-1]):
            return True
        if name in IN_PLACE and any(a.startswith("--in-place") or (a.startswith("-") and not a.startswith("--") and "i" in a)
                                    for a in args) and any(guarded(a) for a in pos):
            return True
        if name == "dd" and any(a.startswith("of=") and guarded(a[3:]) for a in args):
            return True
        sub = _git_subcommand(words)
        if sub and sub[0] in ("checkout", "restore", "rm", "mv") and any(guarded(a) for a in sub[1] if not a.startswith("-")):
            return True
    return False

HOOK_COMMAND_RE = re.compile(r"[\"']?command[\"']?\s*:\s*[\"']\s*(?:[^\"'\s]*/)?bantay-")
DISABLE_HOOKS_RE = re.compile(r"[\"']?disableAllHooks[\"']?\s*:\s*true")

def _has_bantay_pretooluse(content: str) -> bool:
    """Does this settings content still register bantay-hook as a PreToolUse command hook?"""
    try:
        data = json.loads(content)
    except ValueError:
        return "bantay-hook" in content and not DISABLE_HOOKS_RE.search(content)
    try:
        if data.get("disableAllHooks") is True:
            return False
        return any(re.match(r"\s*(?:\S*/)?bantay-hook\b", str(h.get("command", "")))
                   for entry in data["hooks"]["PreToolUse"] for h in entry.get("hooks", []))
    except (AttributeError, KeyError, TypeError):
        return False

def _edit_removes_hook(old: str, new: str) -> bool:
    if not new or DISABLE_HOOKS_RE.search(new):
        return True
    markers = (r"bantay-hook", HOOK_COMMAND_RE.pattern, r"[\"']PreToolUse[\"']", r"[\"']hooks[\"']")
    return any(len(re.findall(m, old)) > len(re.findall(m, new)) for m in markers)

def _guardrail_tamper(call: ToolCall) -> bool:
    if call.tool_name == "Bash":
        return _guard_bash(call)
    if OPENCODE_GUARD_RE.search(os.path.normpath(call.path).casefold()):
        return True
    if not GUARD_FILE_RE.search(os.path.normpath(call.path) if call.path else ""):
        return False
    if call.tool_name == "Write":
        return not _has_bantay_pretooluse(str(call.tool_input.get("content", "")))
    edits = [call.tool_input] if call.tool_name == "Edit" else \
        [e for e in (call.tool_input.get("edits") or []) if isinstance(e, dict)]
    return any(_edit_removes_hook(str(e.get("old_string", "")), str(e.get("new_string", "") or "")) for e in edits)

# `cp .env.example .env` seeds a new env file from a template; it reads no secrets.
TEMPLATE_COPY_RE = re.compile(r"\b(?:cp|mv)\s+(?:-\w+\s+)*\S*\.env\.(?:example|sample|template)\s+\S+")

# `scp -i ~/.ssh/id_ed25519 ...` uses the key as an identity; it does not read it out. Only in a segment
# whose command is ssh/scp/sftp/rsync/git (rsync -e "ssh -i k", GIT_SSH_COMMAND="ssh -i k" git ...).
IDENTITY_CMDS = {"ssh", "scp", "sftp", "rsync", "git"}
IDENTITY_ARG_RE = re.compile(r"(?<![^\s'\"])(?:-i[ \t]*|-o[ \t]*IdentityFile[= \t])['\"]?[^\s'\"]+['\"]?")

def _without_identity_args(text: str) -> str:
    out, last = [], 0
    for a, b in _segment_spans(text):
        words = _words(text[a:b])
        if words and os.path.basename(words[0]) in IDENTITY_CMDS:
            out += [text[last:a], IDENTITY_ARG_RE.sub(" ", text[a:b])]
            last = b
    out.append(text[last:])
    return "".join(out)

def _secret_file_access(call: ToolCall) -> bool:
    text = TEMPLATE_COPY_RE.sub(" ", _literal_scan_text(call.command))
    return bool(SECRET_FILE_RE.search(_without_identity_args(text)))

DISK_RE = _rx(r"\bmkfs(?:\.\w+)?\b|\bdiskutil\s+(?:erase\w*|reformat|zeroDisk|randomDisk|secureErase|partitionDisk"
              r"|apfs\s+delete(?:Container|Volume))\b|\bsystemctl\s+(?:reboot|poweroff|halt)\b|\bdd\s+if=|:\(\)\s*\{\s*:\|:&\s*\};:")
# shutdown/reboot/halt/poweroff only in command position (`xcrun simctl shutdown all`, `celery ... control
# shutdown` are not the machine): the start of a line or segment, inside $( or `, after sudo, or /sbin/<name>.
POWER_CMDS = {"shutdown", "reboot", "halt", "poweroff"}
POWER_RE = _rx(
    r"(?:(?:^|[;&|`]|\$\()[ \t]*(?:sudo(?:[ \t]+-\S+)*[ \t]+)?|\bsudo(?:[ \t]+-\S+)*[ \t]+|(?<![\w/.-])/(?:usr/)?s?bin/)"
    r"(?:\S*/)?(?:shutdown|reboot|halt|poweroff)\b(?![ \t]*=)")

POWER_WORD_RE = _rx(r"\b(?:shutdown|reboot|halt)\b")
HEREDOC_COMMIT_HINT_RE = re.compile(r"\bgit\s+commit\b[\s\S]*<<")

def _disk_or_system(call: ToolCall) -> bool:
    text = _literal_scan_text(call.command)
    if DISK_RE.search(text) or POWER_RE.search(text):
        return True
    # Inside a heredoc commit the strict collapse refused, the word itself still counts: `evaluate` softens
    # it to ask when the message is plain text, and keeps the block when the shape could run the body.
    if HEREDOC_COMMIT_HINT_RE.search(call.command) and POWER_WORD_RE.search(text):
        return True
    return any(os.path.basename(words[0]) in POWER_CMDS for words in _commands(call.command))
SQL_DESTROY_RE = _rx(r"\b(?:DROP\s+(?:TABLE|DATABASE|SCHEMA)|TRUNCATE(?:\s+TABLE)?\s+\w+|DELETE\s+FROM\s+\w+\s*(?:;|[\"']|$))")
PIPE_INTO_RE = _rx(
    r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\b(?:\s+-\S+)*\s+)?(?:env\s+)?(?:\S*/)?"
    r"(?:(?P<shell>(?:ba|z|da|k|fi)?sh\b)|(?P<interp>python[0-9.]*+|perl|ruby|node|php)(?![\w.]))(?P<rest>(?:'[^']*'|\"[^\"]*\"|\d*>&(?:\d+|-)|&>>?|[^|;&\n'\"])*)")
SUBST_FETCH_RE = _rx(r"\b(?:(?:ba|z|da|k)?sh|python[0-9.]*|perl|ruby|node|source|eval)\b[^|;\n]*?(?:<\(|\$\(|`)\s*(?:curl|wget)\b")
# Case-sensitive: `python3 -E` / `perl -M...` are not the safe forms. Safe = print the version, pretty-print
# JSON, or a quoted one-liner that only reads stdin as data (no exec/eval/system/import machinery).
SAFE_INTERP_RE = re.compile(
    r"\s+(?:--version|-m\s+json(?:\.tool)?(?:\s+--?[\w-]+)*|-[ce]\s+(?P<q>['\"])(?P<code>(?:(?!(?P=q)).)*)(?P=q))\s*$", re.S)
UNSAFE_CODE_RE = re.compile(
    r"\b(?:exec|eval|compile|__import__|subprocess|system|popen|spawn|child_process|Function|runpy|pty|socket)\b"
    r"|\bos\.|`|%x")

def _pipe_to_shell(call: ToolCall) -> bool:
    cmd = call.command
    if SUBST_FETCH_RE.search(cmd):
        return True
    for m in PIPE_INTO_RE.finditer(cmd):
        if m.group("shell"):
            return True
        rest = m.group("rest")
        # Literal output paths (`> out.json`, `&> log`) and fd duplication (`2>&1`) do not turn a JSON
        # formatter into downloaded-code execution.
        rest = re.sub(r"(?:\s+(?:\d*>&(?:\d+|-)|(?:\d*|&)>>?\s*(?:[^\s'\"<>$`()&]+|'[^'$`]*'|\"[^\"$`]*\")))+\s*$", "", rest)
        safe = SAFE_INTERP_RE.match(rest)
        if not safe or (safe.group("code") is not None and UNSAFE_CODE_RE.search(safe.group("code"))):
            return True
    return False
# case-sensitive on purpose: `$monkey` is not a secret, `$OPENAI_API_KEY` is
ENV_DUMP_RE = re.compile(
    r"(?:^|[;&|(`])\s*(?:printenv\b|(?:env|set)\s*(?:$|[;&|)`]))"
    r"|\b(?:echo|printf)\b[^|;&\n]*\$\{?[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASS|CREDENTIAL)", re.MULTILINE)

SUDO_DESTRUCTIVE_RE = re.compile(r"\bsudo(?:\s+-\S+)*\s+(?:\S*/)?(?:rm|dd|mkfs(?:\.\w+)?|kill|launchctl|shutdown|reboot|halt)\b")
SUDO_ALWAYS = {"rm", "dd", "kill", "launchctl", "shutdown", "reboot", "halt", "poweroff"}
# The system directories themselves (and their core children). `sudo mkdir -p /opt/myapp` or
# `sudo cp tool /usr/local/bin` are not this; they stay with the ask-tier `sudo` rule.
SYSTEM_DIR_RE = re.compile(r"^/(?:etc|bin|sbin|System(?:/Library)?|Library|var(?:/(?:db|root|lib))?|private(?:/(?:etc|var))?"
                           r"|usr(?:/(?:bin|sbin|lib|libexec|share|include))?)?$")
SYS_ANY_OPERAND = {"mv", "mkdir", "rmdir", "tee", "chmod", "chown", "chgrp", "truncate"}
SYS_DEST_OPERAND = {"cp", "ln", "install", "rsync", "ditto"}  # only the destination is overwritten

def _norm_path(p: str) -> str:
    return re.sub(r"^/+", "/", posixpath.normpath(p)) if p.startswith("/") else p

def _sudo_destructive(call: ToolCall) -> bool:
    if SUDO_DESTRUCTIVE_RE.search(call.command):  # also inside a quoted remote command (ssh host 'sudo rm ...')
        return True
    for words, root in _commands_ex(call.command):
        if not root:
            continue
        name = os.path.basename(words[0])
        if name in SUDO_ALWAYS or name.startswith("mkfs"):
            return True
        pos = [_norm_path(a) for a in words[1:] if not a.startswith("-")]
        if name in SYS_ANY_OPERAND and any(SYSTEM_DIR_RE.match(a) for a in pos):
            return True
        if name in SYS_DEST_OPERAND and pos and SYSTEM_DIR_RE.match(pos[-1]):
            return True
    return False

# sudoers and the account/auth databases: a write by any means is a privilege-escalation or lockout
AUTH_FILE_RE = re.compile(r"^(?:/private)?/etc/(?:sudoers(?:\.d(?:/.*)?)?|passwd|shadow|master\.passwd|group|pam\.d(?:/.*)?)$")
AUTH_WRITE_RE = re.compile(
    r"(?:>>?\|?|(?:^|[|;&`'\"\n]|\$\(|\bsudo)(?:\s+-\S+)*\s*tee\b[^|;&\n<>]*?\s)\s*['\"]?(?:/private)?/etc/"
    r"(?:sudoers|passwd\b|shadow\b|master\.passwd\b|group\b|pam\.d/)"
    r"|\b(?:SUDO_EDITOR|VISUAL|EDITOR)=[^;&|\n]*\bvisudo\b", re.M)
IN_PLACE_FLAG_RE = re.compile(r"^(?:--in-place|-[A-Za-z]*i)")

def _writes_auth_file(call: ToolCall) -> bool:
    if AUTH_WRITE_RE.search(_literal_scan_text(call.command)):
        return True
    auth = lambda w: bool(AUTH_FILE_RE.match(_norm_path(w)))
    for words in _commands(call.command):
        name, args = os.path.basename(words[0]), words[1:]
        for i, w in enumerate(words):
            if ">" in w:
                target = w.rsplit(">", 1)[1].lstrip("|") or (words[i + 1] if i + 1 < len(words) else "")
                if target and auth(target):
                    return True
        pos = [a for a in args if not a.startswith("-")]
        if name == "tee" and any(auth(a) for a in pos):
            return True
        if name in SYS_DEST_OPERAND | {"mv"} and pos and auth(pos[-1]):
            return True
        if name == "dd" and any(a.startswith("of=") and auth(a[3:]) for a in args):
            return True
        if name in IN_PLACE and any(IN_PLACE_FLAG_RE.match(a) for a in args) and any(auth(a) for a in pos):
            return True
    return False

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

def _reads_secret_path(call: ToolCall) -> bool:
    # a directory named without its trailing slash (`~/.ssh`) still names the secrets directory
    p = call.path
    return bool(p and any(SECRET_FILE_RE.search(x) for x in (p, p.rstrip("/") + "/", posixpath.normpath(p))))

def _url_has_secret(call: ToolCall) -> bool:
    return bool(find_secrets(str(call.tool_input.get("url", ""))))

RULES: list[Rule] = [
    # --- guardrail self-protection (block) ---
    Rule("guardrail_tamper", BASH + ("Write", "Edit", "MultiEdit"), "block",
         "Agent attempted to disable its own guardrail.", predicate=_guardrail_tamper),
    # --- exfiltration / credential access (block) ---
    Rule("pipe_to_shell", BASH, "block", "Downloads a script and pipes it straight into a shell.", predicate=_pipe_to_shell),
    Rule("reverse_shell", BASH, "block", "Opens a reverse shell to a remote host.",
         _rx(r"/dev/tcp/|\bnc\b.*\s-e\s|\bncat\b.*\s-e\s|\bsocat\b.*exec")),
    Rule("upload_file", BASH, "block", "Uploads a local file to a remote host.",
         _rx(r"\b(?:curl|wget|http)\b[^|;&]*(?:\s-d\s*@|--data(?:-binary|-raw|-urlencode)?\s*@|\s-F\s+\S*=@|\s-T\s|--upload-file)")),
    Rule("secret_file_git_add", BASH, "block", "Stages a secrets file into git.", predicate=_secret_file_git_add),
    Rule("secret_file_access", BASH, "block", "Touches a credentials or secrets file (.env, keys, SSH, history).",
         predicate=_secret_file_access),
    Rule("env_dump", BASH, "block", "Prints environment variables, which usually contain secrets.", ENV_DUMP_RE),
    Rule("keychain_dump", BASH, "block", "Reads passwords from the system keychain.",
         _rx(r"\bsecurity\s+(?:find-(?:generic|internet)-password|dump-keychain|export)\b")),
    Rule("write_auth_file", BASH, "block", "Writes sudoers or a system account file (privilege escalation).",
         predicate=_writes_auth_file),
    # --- destruction (block) ---
    Rule("rm_outside_project", BASH, "block", "Recursive delete of a path outside the project (home, root, parent, wildcard).", predicate=_rm_outside),
    Rule("disk_or_system", BASH, "block", "Wipes a disk or shuts down the machine.",
         predicate=_disk_or_system),
    Rule("sudo_destructive", BASH, "block", "Destructive command run as root.", predicate=_sudo_destructive),
    Rule("chmod_777", BASH, "block", "Makes files world-writable.", _rx(r"\bchmod\s+(?:-R\s+)?(?:a\+rwx|0?777)\b")),
    Rule("sql_destroy", BASH, "block", "Drops or truncates a database table, or deletes every row.",
         predicate=lambda c: bool(SQL_DESTROY_RE.search(_literal_scan_text(c.command)))),
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
    Rule("read_secret_file", READS, "block", "Reads or searches a credentials or secrets file.", predicate=_reads_secret_path),
    Rule("write_secret", FILES, "block", "Writes a secret-shaped string (API key, token, private key) into a file.", predicate=_secret_in_write),
    Rule("write_system_or_auth", FILES, "block", "Writes to a system or SSH authorization file.",
         predicate=lambda c: bool(re.search(r"^/etc/|/\.ssh/authorized_keys$|^/System/|^/usr/", c.path))),
    Rule("write_shell_or_ci", FILES, "ask", "Changes shell startup, CI workflow, or git hooks.",
         predicate=lambda c: bool(re.search(r"/\.(?:zshrc|bashrc|zprofile|bash_profile|profile)$|/\.github/workflows/|/\.git/hooks/|/\.claude/settings", c.path, re.I))),
    Rule("write_outside_project", FILES, "ask", "Writes a file outside the project directory.", predicate=_outside_cwd),
    Rule("fetch_with_secret", ("WebFetch",), "block", "URL contains a secret.", predicate=_url_has_secret),
]

# Word-level rules that a heredoc commit message can trip by merely naming the command.
SOFT_IN_COMMIT_BODY = {"disk_or_system", "sql_destroy", "secret_file_access"}
COMMIT_BODY_REASON = "Commit message mentions a sensitive command; confirm."

def _first_match(call: ToolCall) -> Rule | None:
    return next((r for r in RULES if r.matches(call)), None)

def _decision(rule: Rule) -> Decision:
    return Decision(rule.verdict, rule.reason, "rule", rule=rule.name)

def evaluate(call: ToolCall) -> Decision | None:
    try:
        first = _first_match(call)
        if first is None:
            return None
        if first.name in SOFT_IN_COMMIT_BODY and call.tool_name == "Bash":
            stripped = without_commit_heredoc_body(call.command)
            if stripped is not None:
                # any other block rule keeps its verdict; a soft rule keeps it when it also matches outside
                # the message (prefix, or the tail after `)"`); a match only inside the message asks
                hard = next((r for r in RULES if r.verdict == "block" and r.name not in SOFT_IN_COMMIT_BODY
                             and r.matches(call)), None)
                if hard:
                    return _decision(hard)
                outside = _first_match(replace(call, tool_input={**call.tool_input, "command": stripped}))
                if outside:
                    return _decision(outside)
                if not commit_heredoc_tail_ok(call.command):  # `)" | sh` could run the message
                    return _decision(first)
                return Decision("ask", COMMIT_BODY_REASON, "rule", rule=first.name)
        return _decision(first)
    except Exception as e:  # rules must never crash the judge
        return Decision("ask", f"rule engine error: {e}", "error")
