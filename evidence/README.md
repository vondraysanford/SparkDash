# Evidence

Captures of SparkDash and of the two-Spark GLM-5.3-Flash server it monitors. Files
are named `YYYY-MM-DD-<what>.<ext>`. Commit PNGs and GIFs here; keep raw screen
recordings out of git (`raw/` is ignored).

## Inventory

### SparkDash build (2026-09-13)

| File | What it shows |
| --- | --- |
| `2026-09-13-sparkdash-in-vscode-agent-session.png` | Mid-build: the dashboard live in VS Code's browser tab (health up at 16 ms, prompt tok/s spikes, a 2+2 chat with the thinking toggle open) beside the Copilot agent session with the spec, 9 files changed, tests task at 2 of 5 |
| `2026-09-13-nvidia-sync-cluster-during-sparkdash-build.png` | NVIDIA Sync cluster view over the 40 minutes of the build: CPU bursts on the head while the agent worked, 250 GB of 261 GB in use, GPUs idle between requests |
| `2026-09-13-agent-session-summary.md` | The model's own structured summary of the session: timeline, decisions, errors and fixes, verification, open items |

### Server bring-up, same day (context for the post)

| File | What it shows |
| --- | --- |
| `2026-09-13-glm-preflight-refusal-then-ok-and-rsync.png` | Preflight refusing on an empty GID table for the worker's `rocep1s0f0`, dumping both nodes' GID tables; then, after the `.env` fix, preflight OK, image rebuild and ship, overlay verify OK, and the 164 GiB rsync running at ~398 MB/s |
| `2026-09-13-glm-server-up-after-640s.png` | vLLM route list, health check passed after 640 s, boot warmup 20 of 20 in 45 s, and the "GLM-5.3-Flash EXL3 is UP (TP=2, nnodes=2)" banner with endpoints |
| `2026-09-13-glm-first-reply-garbled.png` | Health 200, then the first "hello!" reply: 323 tokens of unrelated HTML ending in a claim to be Claude. Overlapped the boot warmup; never recurred |
| `2026-09-13-glm-health-200-and-17x23-391.png` | Health 200 and "What is 17 times 23?" answered `391` at temperature 0 |
| `2026-09-13-glm-prose-and-fibonacci.png` | Default sampling: a two-sentence DGX Spark description, then a Fibonacci function with a short reasoning trace at `reasoning_effort: high` |
| `2026-09-13-nvidia-sync-cluster-glm-loaded.png` | NVIDIA Sync cluster view with the model loaded: 244 GB of 261 GB used, spark-927a at 95 % (124 GB) and spark-11ed at 92 % (120 GB) |
| `2026-09-13-vscode-copilot-glm-first-chat.png` | First use from VS Code Copilot Chat with GLM-5.3-Flash-EXL3 selected: the model reviewing the SparkBench guide |

## Still to capture

- [ ] `2026-09-13-dashboard-streaming.gif` — a chat streaming in the panel with the
  thinking toggle open while the generation tokens/s line rises. 20 to 30 seconds.
- [ ] `2026-09-13-server-down-recovery.gif` — `./start.sh stop` on the head, the badge
  turns red and the charts hold, then the server returns and polling resumes.
- [ ] A clean full-window shot of the dashboard alone, without the VS Code chrome.

## Making a GIF from a screen recording

```bash
ffmpeg -i raw/recording.mov -vf "fps=12,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
  2026-09-13-dashboard-streaming.gif
```

Trim first with `-ss <start> -t <seconds>` before `-i` if the recording is long.
