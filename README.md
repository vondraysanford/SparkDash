# ⚡ SparkDash

A tiny web dashboard + chat client for a [vLLM](https://docs.vllm.ai/) server.
Plain FastAPI + vanilla JS, no database, no bundler.

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
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Then open http://localhost:8080

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `VLLM_BASE_URL` | `http://192.168.1.165:8888` | Where the vLLM server lives |
| `VLLM_MODEL` | `GLM-5.3-Flash-EXL3` | Model id sent to `/v1/chat/completions` |

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
reasoning/content separation.
