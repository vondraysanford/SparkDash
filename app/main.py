"""SparkDash backend: vLLM dashboard + chat proxy.

Proxies a remote vLLM server's /health and /metrics, parses the Prometheus
text format by hand, computes token rates from counter deltas, and streams
chat completions to the browser as server-sent events.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

VLLM_BASE = os.environ.get("VLLM_BASE_URL", "http://192.168.1.165:8888")
MODEL = os.environ.get("VLLM_MODEL", "GLM-5.3-Flash-EXL3")
HISTORY_MAX_SECONDS = 300.0  # keep 5 minutes of metric history

PROMPT_COUNTER = "vllm:prompt_tokens_total"
GEN_COUNTER = "vllm:generation_tokens_total"
RUNNING_GAUGE = "vllm:num_requests_running"
WAITING_GAUGE = "vllm:num_requests_waiting"
KV_CACHE_GAUGE = "vllm:kv_cache_usage_perc"

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.prev_snapshot = None
    app.state.history = []
    yield


app = FastAPI(title="SparkDash", lifespan=lifespan)


def make_client(timeout: float = 5.0) -> httpx.AsyncClient:
    """Factory so tests can swap in a mocked transport."""
    return httpx.AsyncClient(timeout=timeout)


@dataclass
class Snapshot:
    """One poll of upstream counters, kept for rate computation."""

    t: float
    prompt_tokens_total: float = 0.0
    generation_tokens_total: float = 0.0


# ---------------------------------------------------------------------------
# Prometheus text-format parser (hand-rolled, no client library)
# ---------------------------------------------------------------------------


@dataclass
class Sample:
    name: str
    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0


def parse_prometheus(text: str) -> dict[str, list[Sample]]:
    """Parse Prometheus text format into {metric_name: [Sample, ...]}.

    Tolerates: blank lines, # HELP / # TYPE comments, labeled and bare samples,
    exponents (1.7e+09), and trailing whitespace. Label values may contain
    escaped backslashes and quotes.
    """
    out: dict[str, list[Sample]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, labels, value_str = _split_sample(line)
        if value_str is None:
            continue
        try:
            value = float(value_str)
        except ValueError:
            continue
        out.setdefault(name, []).append(Sample(name, labels, value))
    return out


def _split_sample(line: str) -> tuple[str, dict[str, str], str | None]:
    """Split 'name{a="b"} 1.0' -> (name, labels, "1.0")."""
    brace = line.find("{")
    if brace == -1:
        parts = line.rsplit(None, 1)
        return (parts[0], {}, parts[1]) if len(parts) == 2 else (line, {}, None)

    end = _find_closing_brace(line, brace)
    if end == -1:
        return line, {}, None
    name = line[:brace]
    labels = _parse_labels(line[brace + 1 : end])
    rest = line[end + 1 :].strip()
    if not rest:
        return name, labels, None
    return name, labels, rest.split()[-1]


def _find_closing_brace(line: str, start: int) -> int:
    """Index of the '}' closing a label block, honoring quoted strings."""
    in_quote = False
    i = start + 1
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            in_quote = not in_quote
        elif ch == "}" and not in_quote:
            return i
        i += 1
    return -1


def _parse_labels(label_text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    i, n = 0, len(label_text)
    while i < n:
        while i < n and label_text[i] in ", ":
            i += 1
        eq = label_text.find("=", i)
        if eq == -1:
            break
        key = label_text[i:eq].strip()
        i = eq + 1
        while i < n and label_text[i] == " ":
            i += 1
        if i >= n or label_text[i] != '"':
            break
        i += 1
        buf: list[str] = []
        while i < n:
            ch = label_text[i]
            if ch == "\\" and i + 1 < n:
                buf.append(label_text[i + 1])
                i += 2
                continue
            if ch == '"':
                i += 1
                break
            buf.append(ch)
            i += 1
        labels[key] = "".join(buf)
    return labels


def first_value(parsed: dict[str, list[Sample]], name: str) -> float | None:
    """Value of the first sample of `name`, regardless of label set.

    vLLM exposes one aggregate sample per metric here (single engine), so the
    label variant doesn't matter for the values SparkDash tracks.
    """
    samples = parsed.get(name)
    return samples[0].value if samples else None


def compute_stats(parsed: dict[str, list[Sample]], prev: Snapshot | None, now: float) -> dict:
    """Turn a parsed /metrics body into the JSON the browser consumes."""
    prompt_total = first_value(parsed, PROMPT_COUNTER)
    gen_total = first_value(parsed, GEN_COUNTER)
    running = first_value(parsed, RUNNING_GAUGE)
    waiting = first_value(parsed, WAITING_GAUGE)
    kv_pct = first_value(parsed, KV_CACHE_GAUGE)

    prompt_rate = gen_rate = 0.0
    if prev is not None and prompt_total is not None and gen_total is not None:
        dt = now - prev.t
        if dt > 0:
            prompt_rate = max(0.0, (prompt_total - prev.prompt_tokens_total) / dt)
            gen_rate = max(0.0, (gen_total - prev.generation_tokens_total) / dt)

    return {
        "ok": True,
        "prompt_tokens_per_sec": round(prompt_rate, 2),
        "generation_tokens_per_sec": round(gen_rate, 2),
        "requests_running": int(running or 0),
        "requests_waiting": int(waiting or 0),
        "kv_cache_usage_pct": round((kv_pct or 0.0) * 100, 2),
    }


# ---------------------------------------------------------------------------
# /api/health and /api/metrics
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> JSONResponse:
    start = time.perf_counter()
    ok = False
    try:
        async with make_client() as client:
            resp = await client.get(VLLM_BASE + "/health", timeout=5.0)
        ok = resp.status_code == 200
    except httpx.HTTPError:
        pass
    latency_ms = int((time.perf_counter() - start) * 1000)
    return JSONResponse({"ok": ok, "latency_ms": latency_ms})


@app.get("/api/metrics")
async def metrics() -> JSONResponse:
    prev: Snapshot | None = getattr(app.state, "prev_snapshot", None)
    now = time.monotonic()

    try:
        async with make_client() as client:
            resp = await client.get(VLLM_BASE + "/metrics", timeout=5.0)
        resp.raise_for_status()
        body = resp.text
    except httpx.HTTPError:
        # Server down/erred: keep the API contract, mark not-ok, and drop the
        # previous snapshot so rates restart cleanly when it returns.
        app.state.prev_snapshot = None
        return JSONResponse(
            {
                "ok": False,
                "prompt_tokens_per_sec": 0.0,
                "generation_tokens_per_sec": 0.0,
                "requests_running": 0,
                "requests_waiting": 0,
                "kv_cache_usage_pct": 0.0,
            }
        )

    parsed = parse_prometheus(body)
    stats = compute_stats(parsed, prev, now)
    app.state.prev_snapshot = Snapshot(
        t=now,
        prompt_tokens_total=first_value(parsed, PROMPT_COUNTER) or 0.0,
        generation_tokens_total=first_value(parsed, GEN_COUNTER) or 0.0,
    )

    # Server-side 5-minute history so a page reload can backfill the chart.
    history: list[dict] = app.state.history
    stats["t"] = time.time()
    history.append(stats)
    cutoff = time.time() - HISTORY_MAX_SECONDS
    history[:] = [p for p in history if p["t"] >= cutoff]
    return JSONResponse(stats)


@app.get("/api/metrics/history")
async def metrics_history() -> JSONResponse:
    return JSONResponse(app.state.history)


# ---------------------------------------------------------------------------
# /api/chat -> SSE
# ---------------------------------------------------------------------------


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def extract_delta_event(delta: dict, chunk: dict) -> dict | None:
    """Map an upstream delta to our SSE event, keeping reasoning separate.

    Observed vLLM behavior: reasoning appears in delta["reasoning"] and a
    single delta may carry both reasoning and content at once.
    """
    event: dict = {}
    if delta.get("role"):
        event["role"] = delta["role"]
    if delta.get("reasoning"):
        event["reasoning"] = delta["reasoning"]
    if delta.get("content"):
        event["content"] = delta["content"]
    if not event and chunk.get("usage"):
        event["usage"] = chunk["usage"]
    return event or None


async def stream_chat(user_body: dict):
    payload = {
        "model": MODEL,
        "messages": user_body.get("messages", []),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    for key in ("temperature", "top_p", "max_tokens", "stop"):
        if key in user_body:
            payload[key] = user_body[key]

    try:
        async with make_client(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            async with client.stream(
                "POST", VLLM_BASE + "/v1/chat/completions", json=payload
            ) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode(errors="replace")[:300]
                    yield sse({"type": "error", "error": f"upstream {resp.status_code}: {detail}"})
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield sse({"type": "done"})
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or [{}]
                    event = extract_delta_event(choices[0].get("delta") or {}, chunk)
                    if event:
                        event["type"] = "delta"
                        yield sse(event)
    except httpx.HTTPError as exc:
        yield sse({"type": "error", "error": f"upstream unreachable: {exc}"})


@app.post("/api/chat")
async def chat(request: Request) -> StreamingResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return StreamingResponse(
        stream_chat(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
