"""SparkDash test suite.

Mocks the upstream vLLM server with httpx.MockTransport wired through
app.make_client, and exercises:
- the Prometheus parser against a real captured /metrics sample
- /api/health success and failure paths
- /api/metrics counter-delta math
- /api/chat SSE relay, including reasoning/content separation
"""

from __future__ import annotations

import json
import pathlib
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import main
from app.main import app, compute_stats, parse_prometheus

DATA_DIR = pathlib.Path(__file__).parent / "data"
METRICS_SAMPLE = (DATA_DIR / "metrics_sample.txt").read_text()


# ---------------------------------------------------------------------------
# Parser unit tests against the real captured sample
# ---------------------------------------------------------------------------


def test_sample_fixture_exists_and_parses():
    parsed = parse_prometheus(METRICS_SAMPLE)
    # The real server exposes these; names verified against live vLLM.
    for name in (
        main.PROMPT_COUNTER,
        main.GEN_COUNTER,
        main.RUNNING_GAUGE,
        main.WAITING_GAUGE,
        main.KV_CACHE_GAUGE,
    ):
        assert name in parsed, f"metric {name} missing from captured sample"
        assert parsed[name], f"metric {name} has no samples"


def test_parse_real_sample_values():
    parsed = parse_prometheus(METRICS_SAMPLE)
    # Sample was captured while idle; running/waiting were 0.
    assert main.first_value(parsed, main.RUNNING_GAUGE) == 0.0
    assert main.first_value(parsed, main.WAITING_GAUGE) == 0.0
    # Prefix cache had seen real traffic when captured.
    prompt_total = main.first_value(parsed, main.PROMPT_COUNTER)
    assert prompt_total is not None and prompt_total > 0
    # kv usage is a 0..1 fraction in the sample.
    kv = main.first_value(parsed, main.KV_CACHE_GAUGE)
    assert kv is not None and 0.0 <= kv <= 1.0


def test_parse_skips_comments_and_junk():
    text = """
# HELP some_metric help text
# TYPE some_metric counter
some_metric 42.0
broken_metric not_a_number
truncated_metric{a="b
also_broken

with_labels{engine="0",model_name="GLM-5.3-Flash-EXL3"} 1.7e+03
"""
    parsed = parse_prometheus(text)
    assert main.first_value(parsed, "some_metric") == 42.0
    assert main.first_value(parsed, "with_labels") == 1700.0
    assert "broken_metric" not in parsed
    assert "truncated_metric" not in parsed
    assert "also_broken" not in parsed


def test_parse_label_escape_sequences():
    text = 'm{path="/a\\"b\\\\c"} 7.0'
    parsed = parse_prometheus(text)
    sample = parsed["m"][0]
    assert sample.labels["path"] == '/a"b\\c'


def test_parse_bare_metric_without_labels():
    parsed = parse_prometheus("solo_metric 3.14\n")
    assert main.first_value(parsed, "solo_metric") == 3.14


# ---------------------------------------------------------------------------
# Rate computation (counter deltas)
# ---------------------------------------------------------------------------


def _parsed(counters: dict[str, float]) -> dict:
    out = {}
    for name, value in counters.items():
        out[name] = [
            main.Sample(name=name, labels={"engine": "0", "model_name": "m"}, value=value)
        ]
    return out


def test_first_poll_gives_zero_rates():
    stats = compute_stats(
        _parsed({main.PROMPT_COUNTER: 1000.0, main.GEN_COUNTER: 50.0}), None, now=1.0
    )
    assert stats["prompt_tokens_per_sec"] == 0.0
    assert stats["generation_tokens_per_sec"] == 0.0
    assert stats["ok"] is True


def test_counter_delta_computes_rates():
    prev = main.Snapshot(t=0.0, prompt_tokens_total=1000.0, generation_tokens_total=50.0)
    stats = compute_stats(
        _parsed(
            {
                main.PROMPT_COUNTER: 1200.0,
                main.GEN_COUNTER: 90.0,
                main.RUNNING_GAUGE: 2.0,
                main.WAITING_GAUGE: 1.0,
                main.KV_CACHE_GAUGE: 0.42,
            }
        ),
        prev,
        now=2.0,
    )
    assert stats["prompt_tokens_per_sec"] == 100.0  # 200 tokens / 2 s
    assert stats["generation_tokens_per_sec"] == 20.0  # 40 tokens / 2 s
    assert stats["requests_running"] == 2
    assert stats["requests_waiting"] == 1
    assert stats["kv_cache_usage_pct"] == 42.0


def test_counter_reset_clamps_to_zero():
    prev = main.Snapshot(t=0.0, prompt_tokens_total=1000.0, generation_tokens_total=50.0)
    stats = compute_stats(
        _parsed({main.PROMPT_COUNTER: 10.0, main.GEN_COUNTER: 5.0}), prev, now=2.0
    )
    assert stats["prompt_tokens_per_sec"] == 0.0
    assert stats["generation_tokens_per_sec"] == 0.0


# ---------------------------------------------------------------------------
# HTTP endpoint tests: /api/health, /api/metrics
# ---------------------------------------------------------------------------


def mockTransport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest_asyncio.fixture
async def client_factory():
    """Yield a factory that installs a mocked upstream and a fresh app state."""

    def _install(handler) -> AsyncClient:
        transport = mockTransport(handler)

        def _factory(timeout: float = 5.0) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, timeout=timeout)

        main.make_client = _factory
        app.state.prev_snapshot = None
        app.state.history = []
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _install
    # restore real factory so live server can be exercised after tests
    main.make_client = lambda timeout=5.0: httpx.AsyncClient(timeout=timeout)


def _upstream_ok(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200)
    if request.url.path == "/metrics":
        return httpx.Response(200, text=METRICS_SAMPLE)
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_health_ok(client_factory):
    async with client_factory(_upstream_ok) as ac:
        resp = await ac.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_health_failure_path(client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with client_factory(handler) as ac:
        resp = await ac.get("/api/health")
    assert resp.status_code == 200  # SparkDash still answers
    body = resp.json()
    assert body["ok"] is False
    assert isinstance(body["latency_ms"], int)


@pytest.mark.asyncio
async def test_health_non_200_is_down(client_factory):
    async with client_factory(lambda req: httpx.Response(503)) as ac:
        resp = await ac.get("/api/health")
    assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_metrics_ok_and_rates_restart_after_failure(client_factory):
    current = {"text": METRICS_SAMPLE}

    def bump(text: str, name: str, delta: float) -> str:
        sample = parse_prometheus(text)[name][0]
        labels = ",".join(f'{k}="{v}"' for k, v in sample.labels.items())
        old = f"{name}{{{labels}}} {sample.value}"
        new = f"{name}{{{labels}}} {sample.value + delta}"
        assert old in text, f"bump failed to match {old!r}"
        return text.replace(old, new, 1)

    def handler(request: httpx.Request) -> httpx.Response:
        text = current["text"]
        return httpx.Response(200, text=text)

    async with client_factory(handler) as ac:
        # Poll 1: upstream down -> ok False, zeros.
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        main.make_client = lambda timeout=5.0: httpx.AsyncClient(
            transport=mockTransport(down), timeout=timeout
        )
        b1 = (await ac.get("/api/metrics")).json()
        assert b1["ok"] is False
        assert b1["prompt_tokens_per_sec"] == 0.0

        # Poll 2: recovered; baseline snapshot established -> 0 rate.
        current["text"] = METRICS_SAMPLE
        main.make_client = lambda timeout=5.0: httpx.AsyncClient(
            transport=mockTransport(handler), timeout=timeout
        )
        b2 = (await ac.get("/api/metrics")).json()
        assert b2["ok"] is True
        assert b2["kv_cache_usage_pct"] >= 0.0
        assert b2["prompt_tokens_per_sec"] == 0.0

        # Poll 3: counters bumped by +200 prompt / +40 gen -> positive rates.
        bumped = bump(METRICS_SAMPLE, main.PROMPT_COUNTER, 200.0)
        bumped = bump(bumped, main.GEN_COUNTER, 40.0)
        current["text"] = bumped
        b3 = (await ac.get("/api/metrics")).json()
        assert b3["prompt_tokens_per_sec"] > 80.0
        assert b3["generation_tokens_per_sec"] > 15.0


# ---------------------------------------------------------------------------
# /api/chat SSE relay
# ---------------------------------------------------------------------------

CHAT_UPSTREAM_LINES = [
    'data: {"choices":[{"delta":{"role":"assistant"}}]}',
    'data: {"choices":[{"delta":{"reasoning":"thinking hard"}}]}',
    'data: {"choices":[{"delta":{"content":"Hello"}}]}',
    'data: {"choices":[{"delta":{"content":" world","reasoning":" more"}}]}',
    "data: [DONE]",
]


def chat_transport(lines: list[str], status: int = 200) -> httpx.MockTransport:
    body = ("\n\n".join(lines) + "\n\n").encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return mockTransport(handler)


async def collect_sse(ac: AsyncClient, payload: dict) -> list[dict]:
    events = []
    async with ac.stream("POST", "/api/chat", json=payload) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            events.append(json.loads(data))
    return events


@pytest.mark.asyncio
async def test_chat_streams_reasoning_and_content_separately(client_factory):
    async def _run():
        main.make_client = lambda timeout=None: httpx.AsyncClient(
            transport=chat_transport(CHAT_UPSTREAM_LINES)
        )
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        return await collect_sse(ac, {"messages": [{"role": "user", "content": "hi"}]})

    events = await _run()
    types = [e["type"] for e in events]
    assert "error" not in types

    reasoning = "".join(e["reasoning"] for e in events if "reasoning" in e)
    content = "".join(e["content"] for e in events if "content" in e)
    assert reasoning == "thinking hard more"
    assert content == "Hello world"

    # The delta that carried both fields forwards both, separately.
    both = [e for e in events if "reasoning" in e and "content" in e]
    assert len(both) == 1
    assert both[0]["reasoning"] == " more"
    assert both[0]["content"] == " world"

    # always ends with a done event
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_chat_upstream_error_relays_error_event(client_factory):
    main.make_client = lambda timeout=None: httpx.AsyncClient(
        transport=chat_transport(["upstream exploded"], status=500)
    )
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    events = await collect_sse(ac, {"messages": [{"role": "user", "content": "hi"}]})
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "upstream 500" in events[0]["error"]


@pytest.mark.asyncio
async def test_chat_upstream_unreachable(client_factory):
    def _factory(timeout=None):
        t = httpx.MockTransport(
            lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused"))
        )
        return httpx.AsyncClient(transport=t)

    main.make_client = _factory
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    events = await collect_sse(ac, {"messages": [{"role": "user", "content": "hi"}]})
    assert events and events[0]["type"] == "error"
    assert "unreachable" in events[0]["error"]


@pytest.mark.asyncio
async def test_chat_sends_model_and_stream_fields(client_factory):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    main.make_client = lambda timeout=None: httpx.AsyncClient(
        transport=mockTransport(handler)
    )
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await collect_sse(ac, {"messages": [{"role": "user", "content": "q"}], "max_tokens": 12})
    sent = captured["json"]
    assert sent["model"] == main.MODEL
    assert sent["stream"] is True
    assert sent["max_tokens"] == 12
    assert sent["messages"] == [{"role": "user", "content": "q"}]
