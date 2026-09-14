# Evidence

Captures of SparkDash against the live two-Spark GLM-5.3-Flash server. Name
files `YYYY-MM-DD-<what>.<ext>`. Commit PNGs and GIFs here; keep raw screen
recordings out of git (`raw/` is ignored).

## To capture

- [ ] `2026-09-13-dashboard-idle.png` — the page with a green health badge, flat
  charts, and the KV gauge at rest.
- [ ] `2026-09-13-dashboard-streaming.gif` — a chat streaming in the panel with the
  thinking toggle open while the generation tokens/s line rises. 20 to 30 seconds.
- [ ] `2026-09-13-server-down-recovery.gif` — `./start.sh stop` on the head, the badge
  turns red and the charts hold, then the server returns and polling resumes.
- [ ] `2026-09-13-agent-session.png` — the VS Code agent transcript at the final
  summary, for the "How this was built" section.

## Making a GIF from a screen recording

```bash
ffmpeg -i raw/recording.mov -vf "fps=12,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
  2026-09-13-dashboard-streaming.gif
```

Trim first with `-ss <start> -t <seconds>` before `-i` if the recording is long.
