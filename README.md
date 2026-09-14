# ⚡ SparkDash

[![tests](https://github.com/vondraysanford/SparkDash/actions/workflows/tests.yml/badge.svg)](https://github.com/vondraysanford/SparkDash/actions/workflows/tests.yml)

A tiny web dashboard + chat client for a [vLLM](https://docs.vllm.ai/) server.
Plain FastAPI + vanilla JS, no database, no bundler.

Built end to end by **GLM-5.3-Flash** running on two NVIDIA DGX Sparks, in one
VS Code agent session, from the spec in [How this was built](#how-this-was-built).

- **Health badge** — proxies the server's `/health`, shows up/down and latency
- **Tokens/s chart** — prompt and generation tokens per second for the last
  5 minutes, computed from Prometheus counter deltas between 2-second polls
- **KV cache gauge** — `vllm:kv_cache_usage_perc` as a percentage
- **Chat panel** — streams `/v1/chat/completions` over SSE with the model's
  reasoning (`delta.reasoning`) rendered separately from the answer,
  collapsible under a "thinking" toggle
- Keeps polling every 2 s; the page survives the server going down and
  picks back up when it returns

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
VLLM_BASE_URL=http://192.168.1.165:8888 uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Then open http://localhost:8080. The example address is a DGX Spark on the
LAN; point `VLLM_BASE_URL` at whatever serves your `/v1`.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `VLLM_BASE_URL` | `http://localhost:8000` | Where the vLLM server lives (vLLM's default port) |
| `VLLM_MODEL` | `GLM-5.3-Flash-EXL3` | Model id sent to `/v1/chat/completions`; must match the server's `--served-model-name` |

### Docker

```bash
docker build -t sparkdash .
docker run --rm -p 8080:8080 \
  -e VLLM_BASE_URL=http://192.168.1.165:8888 \
  sparkdash
```

## API

| Endpoint | Description |
| --- | --- |
| `GET /api/health` | `{"ok": bool, "latency_ms": int}` — proxies upstream `/health` |
| `GET /api/metrics` | Parsed metrics: prompt/generation tok/s, running/waiting requests, KV cache % |
| `GET /api/metrics/history` | Server-side rolling 5-minute history of the above |
| `POST /api/chat` | SSE stream of `{"type":"delta","content":...,"reasoning":...}` frames, ending with `{"type":"done"}` |

## Metrics parsing

Prometheus text format is parsed by hand (`app/main.py`), no prometheus-client
dependency. Rates come from comparing `vllm:prompt_tokens_total` and
`vllm:generation_tokens_total` against the previous poll and dividing by the
elapsed wall time. Tests parse a real `/metrics` sample captured with `curl`
from a live vLLM server (see `tests/data/metrics_sample.txt`).

## Tests

```bash
pip install -r requirements.txt
pytest
```

Tests mock the upstream with `httpx.MockTransport` and cover the parser
(including label escapes, comments, malformed lines), the health failure path,
counter-delta math (including a counter reset), and the SSE relay's
reasoning/content separation. The same suite runs in GitHub Actions on every
push.

## Evidence

Screenshots and clips of the dashboard against the live server live in
[`evidence/`](evidence/). See its README for the capture list.

## How this was built

| | |
| --- | --- |
| Date | 2026-09-13 |
| Model | GLM-5.3-Flash (320B MoE, 18B active), 4-bit EXL3, tensor-parallel 2 across two NVIDIA DGX Sparks via the [MiaAI-Lab recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks) |
| Client | VS Code, GitHub Copilot Chat agent mode, custom OpenAI-compatible endpoint pointed at the head Spark |
| Result | 4 commits, 16 passing tests, about 1,200 lines including tests |
| Session length | _fill in_ |
| Human interventions | _fill in — count and what for_ |

The dashboard monitors the same server that wrote it, so its first job was
to chart its own build.

The spec, pasted into the agent verbatim:

```text
Build "SparkDash", a small web app that monitors the vLLM server at
http://192.168.1.165:8888 and lets me chat with it.

Stack: Python 3.12, FastAPI, uvicorn, httpx, pytest. Frontend is plain
HTML/CSS/JS served by FastAPI, with Chart.js from a CDN. No database,
no bundler. Create the project in this folder and initialize git.

Requirements:
1. GET /api/health proxies the server's /health and returns
   {"ok": true|false, "latency_ms": n}.
2. GET /api/metrics fetches the server's /metrics (Prometheus text
   format), parses it yourself (no Prometheus client library), and
   returns JSON with prompt tokens/s and generation tokens/s computed
   from counter deltas between polls, running and waiting request
   counts, and KV cache usage percent.
3. POST /api/chat streams a chat completion from /v1/chat/completions
   to the browser as server-sent events, model "GLM-5.3-Flash-EXL3",
   passing the `reasoning` field through separately from `content`.
4. The page shows a health badge, a tokens/s chart for the last
   5 minutes, a KV cache gauge, and a chat panel that renders streamed
   content with reasoning collapsed under a "thinking" toggle.
5. Poll every 2 seconds. The page must keep working while the server
   is down and recover when it returns.
6. pytest tests with the upstream mocked, covering the metrics parser
   against a real /metrics sample you capture with curl, and the
   health failure path.
7. A Dockerfile and a README with run instructions.
8. Work in small commits. Run the tests and the server yourself, fix
   what breaks, and end with a summary of what you verified and what
   you did not.

Keep the code under about 800 lines excluding tests.
```
