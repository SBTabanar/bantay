# Notes

## Proxy with OAuth

Verified 2026-10-09 with Claude Code on a claude.ai subscription (OAuth, no API key):

```
bantay serve --no-model
ANTHROPIC_BASE_URL=http://127.0.0.1:4477/anthropic claude -p "reply with exactly: hi juan@x.ph"
-> hi juan@x.ph
```

The base-URL override works with subscription auth: the proxy forwards `authorization` / `anthropic-beta`
untouched and Claude Code accepts the restored stream. A local tap between bantay and api.anthropic.com
confirmed the outbound `/v1/messages` body carried `reply with exactly: hi <EMAIL_2>` and never `juan@x.ph`
(`<EMAIL_1>` was the account email from Claude Code's own context, also redacted). So the proxy is the
redaction path for both API-key and OAuth users; the `bantay redact` + `UserPromptSubmit` fallback is not needed.

Caveats seen live:
- First request of a session redacts ~400 KB of Claude Code context (system reminders, CLAUDE.md, skills list);
  with GLiNER on CPU that took 9-26 s. Later requests hit the per-block cache (0.2 ms for an identical turn).
  `--no-gliner` makes it regex-only and near-instant.
- Top-level `system` and `role: "system"` messages are forwarded unredacted (by design in the brief).
- Review correction: `/v1/messages/count_tokens` now redacts outbound messages too. Its response and unrelated non-message endpoints pass through unchanged. Regression tests cover this route and fail-closed behavior.
