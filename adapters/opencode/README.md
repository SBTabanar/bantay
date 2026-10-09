# Bantay for OpenCode

Same local judge, different cloud agent. This plugin sends every guarded OpenCode
tool call to `bantayd` before it runs, exactly like the Claude Code `PreToolUse` hook.

## Install

```bash
bantay serve                                   # daemon on http://127.0.0.1:4477
bantay install --opencode --project /path/to/project
```

That copies `bantay.js` to `<project>/.opencode/plugin/bantay.js`; OpenCode loads
every `{plugin,plugins}/*.{js,ts}` file from the project `.opencode/` dir at startup.
For all projects, copy it to `~/.config/opencode/plugin/bantay.js` instead.
Point at another daemon with `BANTAY_URL` (read by the OpenCode process).

## Hook

OpenCode 1.18 plugin API, `@opencode-ai/plugin` `Hooks`:

```ts
"tool.execute.before"?: (input: { tool: string; sessionID: string; callID: string },
                         output: { args: any }) => Promise<void>
```

OpenCode awaits it before running the tool. A thrown `Error` aborts the call and its
message becomes the tool result the model sees.

## What is translated

| OpenCode tool | args | sent to `/decide` as |
|---|---|---|
| `bash` | `command`, `workdir` | `Bash {command}`, cwd = `workdir` or the project dir |
| `read` | `filePath` | `Read {file_path}` |
| `write` | `filePath`, `content` | `Write {file_path, content}` |
| `edit` | `filePath`, `oldString`, `newString` | `Edit {file_path, old_string, new_string}` |
| `grep` | `pattern`, `path`, `include` | `Grep {pattern, path, include}` |
| `glob` | `pattern`, `path` | `Glob {pattern, path}` |
| `webfetch` | `url` | `WebFetch {url}` |
| `apply_patch` | `patchText` | one call per file: Add → `Write`, Update → `Edit` (`-` lines / `+` lines), Delete → `Bash rm -- path`; the strictest verdict wins |

Other tools (`todowrite`, `task`, `skill`, `question`, MCP tools, ...) are not sent,
matching the Claude Code hook's matcher.

Verdicts: `allow` → the tool runs. `block` → `Error("[bantay] <reason> (rule: <rule>)")`.

## Fail closed

If the daemon cannot be reached, answers with a non-200, or takes longer than 15 s,
the call is refused with `[bantay] daemon unreachable — run bantay serve`. The first
model-judged decision after `bantay serve` can be slow while the model warms up, so
send one request first (for example `bantay check --tool Read README.md`).

The plugin also refuses, locally, any write, edit, or shell `rm`/`mv`/`sed -i`/`>` aimed
at `.opencode/plugin(s)/` or `~/.config/opencode/plugin(s)/`, so the agent cannot
delete its own guard (the daemon's `guardrail_tamper` rule only knows Claude Code paths).

## Ask mode caveat

OpenCode 1.18 has no plugin hook that can raise an interactive approval prompt. The
`permission.ask` hook is declared in the plugin types but OpenCode 1.18.10 never
triggers it. So an `ask` verdict is refused like a block, with a distinct message:
`[bantay] ask: needs human approval, refused in OpenCode — <reason>`. To allow such an
action, run it yourself or use the Claude Code adapter, which shows a real prompt.
