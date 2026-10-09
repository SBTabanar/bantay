import { resolve } from "node:path";
// Bantay adapter for OpenCode (>= 1.18): every guarded tool call is judged by the
// local bantayd daemon before it runs. Plain ESM, no dependencies.
//
// Hook: "tool.execute.before"(input: {tool, sessionID, callID}, output: {args}).
// OpenCode runs it right before the tool; a thrown Error aborts the call and the
// error message is returned to the model as the tool result.

const DEFAULT_URL = "http://127.0.0.1:4477";
const TIMEOUT_MS = 15000;
const RANK = { allow: 0, ask: 1, block: 2 };
// The plugin file itself, and the dirs OpenCode auto-loads plugins from.
const SELF_RE = /(?:^|\/)(?:\.opencode|\.config\/opencode)\/plugins?\//i;
const SELF_BASH_RE = /\b(?:rm|mv|cp|unlink|truncate|shred|tee|ln|chmod)\b[^;&|\n]*(?:\.opencode|\.config\/opencode)\/plugins?\b|\bsed\b[^;&|\n]*\s-i[^;&|\n]*\.opencode\/plugins?\b|>\s*["']?\S*\.opencode\/plugins?\//i;

function parsePatch(text) {
  // apply_patch envelope: "*** Add File: p" / "*** Update File: p" / "*** Delete File: p"
  const files = [];
  let cur = null;
  for (const line of String(text || "").split("\n")) {
    const m = line.match(/^\*\*\* (Add|Update|Delete) File:\s*(.+)$/);
    if (m) { cur = { op: m[1], path: m[2].trim(), plus: [], minus: [] }; files.push(cur); continue; }
    const mv = line.match(/^\*\*\* Move to:\s*(.+)$/);
    if (mv && cur) { files.push({ op: "Add", path: mv[1].trim(), plus: cur.plus, minus: [] }); continue; }
    if (!cur || line.startsWith("***")) continue;
    if (line.startsWith("+")) cur.plus.push(line.slice(1));
    else if (line.startsWith("-")) cur.minus.push(line.slice(1));
  }
  return files;
}

// OpenCode tool call -> list of Claude-Code-shaped {tool_name, tool_input}. [] = not guarded.
function translate(tool, args) {
  const a = args || {};
  switch (tool) {
    case "bash": return [{ tool_name: "Bash", tool_input: { command: a.command ?? "" } }];
    case "read": return [{ tool_name: "Read", tool_input: { file_path: a.filePath ?? "" } }];
    case "write": return [{ tool_name: "Write", tool_input: { file_path: a.filePath ?? "", content: a.content ?? "" } }];
    case "edit": return [{ tool_name: "Edit", tool_input: { file_path: a.filePath ?? "", old_string: a.oldString ?? "", new_string: a.newString ?? "" } }];
    case "grep": return [{ tool_name: "Grep", tool_input: { pattern: a.pattern ?? "", path: a.path ?? "", include: a.include ?? "" } }];
    case "glob": return [{ tool_name: "Glob", tool_input: { pattern: a.pattern ?? "", path: a.path ?? "" } }];
    case "list": return [{ tool_name: "LS", tool_input: { path: a.path ?? "" } }];
    case "webfetch": return [{ tool_name: "WebFetch", tool_input: { url: a.url ?? "" } }];
    case "apply_patch":
      return parsePatch(a.patchText).length ? parsePatch(a.patchText).map((f) =>
        f.op === "Delete" ? { tool_name: "Bash", tool_input: { command: `rm -- ${JSON.stringify(f.path)}` } }
        : f.op === "Add" ? { tool_name: "Write", tool_input: { file_path: f.path, content: f.plus.join("\n") } }
        : { tool_name: "Edit", tool_input: { file_path: f.path, old_string: f.minus.join("\n"), new_string: f.plus.join("\n") } }) : [{ tool_name: tool, tool_input: a }];
    case "todowrite": case "todoread": case "question": case "invalid": return [];
    default: return [{ tool_name: tool, tool_input: a }];
  }
}

function selfTamper(call) {
  if (call.tool_name === "Bash") return SELF_BASH_RE.test(call.tool_input.command);
  if (call.tool_name === "Write" || call.tool_name === "Edit") return SELF_RE.test(call.tool_input.file_path);
  return false;
}

async function decide(payload, url = process.env.BANTAY_URL || DEFAULT_URL) {
  try {
    const r = await fetch(url.replace(/\/+$/, "") + "/decide", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    if (!d || typeof d !== "object" || !(d.verdict in RANK)) throw new Error("bad reply");
    return d;
  } catch (e) {
    return { verdict: "block", reason: "daemon unreachable — run bantay serve", source: "error", unreachable: true };
  }
}

function message(d) {
  if (d.unreachable) return "[bantay] daemon unreachable — run bantay serve";
  const rule = d.rule ? ` (rule: ${d.rule})` : "";
  const reason = typeof d.reason === "string" && d.reason.trim() ? d.reason : "(no reason given)";
  if (d.verdict === "ask") return `[bantay] ask: needs human approval, refused in OpenCode — ${reason}${rule}`;
  return `[bantay] ${reason}${rule}`;
}

// Judge one OpenCode tool call. Resolves on allow, throws Error("[bantay] ...") otherwise.
async function guard(tool, args, { directory = process.cwd(), sessionID = "" } = {}) {
  const calls = translate(tool, args);
  if (!calls.length) return null;
  const cwd = resolve(directory, (tool === "bash" && args?.workdir) || ".");
  let worst = null;
  for (const c of calls) {
    const d = selfTamper(c)
      ? { verdict: "block", reason: "Disables the Bantay OpenCode plugin.", source: "rule", rule: "guardrail_tamper" }
      : await decide({ ...c, cwd, session_id: sessionID });
    if (!worst || RANK[d.verdict] > RANK[worst.verdict]) worst = d;
    if (d.verdict === "block") break;
  }
  if (worst.verdict !== "allow") throw new Error(message(worst));
  return worst;
}

export const BantayPlugin = async ({ directory } = {}) => ({
  "tool.execute.before": async (input, output) => {
    await guard(input.tool, output?.args, { directory, sessionID: input.sessionID });
  },
});
// OpenCode calls every exported value as a plugin, so helpers ride on the one export.
Object.assign(BantayPlugin, { translate, decide, guard });
