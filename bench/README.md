# Benchmarks

Measurements of the vLLM server SparkDash monitors, taken with `vllm bench serve`,
the tool NVIDIA's own [DGX Spark performance guide](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/connect-two-sparks/assets/performance_benchmarking_guide.md)
uses. Raw console output for every run is in [`results/`](results/). The JSON
files the tool writes are copied out of the container with
[`fetch-results.sh`](fetch-results.sh).

Read the caveats before quoting any number. The first two runs measure a floor,
not a typical speed, and one of them exposes a scheduler setting rather than
the hardware.

## Server under test

| | |
| --- | --- |
| Model | GLM-5.3-Flash, 320B mixture-of-experts, 18B active per token |
| Weights | `Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw`, 4-bit EXL3, 164 GiB |
| Recipe | [MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks), image built locally 2026-09-13 from recipe stamp `659de733ccd3` |
| Engine | vLLM, system fingerprint `vllm-0.1.dev20051+g487ecf187-tp2-585efade` |
| Hardware | 2 × NVIDIA DGX Spark (GB10, 128 GB unified memory each) |
| Parallelism | Tensor parallel 2 across the two nodes, NCCL over RoCE v2 on one ConnectX-7 link |
| KV cache | FP8 (`fp8_ds_mla`), context cap 850,000 tokens, GPU memory utilization 0.85 |
| Speculative decoding | DFlash2 draft model, 7 draft tokens per step, draft sharded across both nodes |
| Scheduler | `MAX_NUM_SEQS=4`, `MAX_NUM_BATCHED_TOKENS=7168`, mixed-prefill policy **skip** (a decode step never carries another request's prefill) |
| Sampling | Server defaults from the checkpoint's `generation_config.json`: temperature 1.0, top-p 0.95. The benchmark no longer forces greedy decoding, and these runs did not override it |
| Endpoint | `/v1/completions`, raw prompts, so no chat template and no thinking phase |

All settings are the recipe's defaults as of 2026-09-13 except where the run notes say otherwise.

## Runs

### 2026-09-14 · random tokens, 1,024 in / 256 out, 16 prompts

Console logs: [`c1`](results/2026-09-14-vllm-bench-serve-random-1k-256-c1.txt), [`c4`](results/2026-09-14-vllm-bench-serve-random-1k-256-c4.txt). Screenshot of the c4 result block: [`evidence/2026-09-14-vllm-bench-serve-random-c4-result.png`](../evidence/2026-09-14-vllm-bench-serve-random-c4-result.png). Container clock 04:11 and 04:27. A repeat of the c1 run, captured by screenshot only, is described after the table.

```bash
docker exec glm53-exl3-head vllm bench serve \
  --backend openai --endpoint /v1/completions \
  --base-url http://127.0.0.1:8888 --model GLM-5.3-Flash-EXL3 \
  --tokenizer Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw \
  --dataset-name random --random-input-len 1024 --random-output-len 256 \
  --num-prompts 16 --max-concurrency 1 --save-result --result-dir /tmp/bench
# second run: --max-concurrency 4
```

| Metric | Concurrency 1 | Concurrency 4 |
| --- | ---: | ---: |
| Benchmark duration | 302.7 s | 227.9 s |
| Output token throughput, aggregate | 13.5 tok/s | 18.0 tok/s |
| Total token throughput (in + out) | 67.7 tok/s | 89.9 tok/s |
| Mean TTFT | 1,151 ms | 32,764 ms |
| Median TTFT | 1,153 ms | 39,073 ms |
| P99 TTFT | 1,363 ms | 63,747 ms |
| Mean time per output token | 69.7 ms | 80.6 ms |
| Mean inter-token latency (one decode step) | 113.1 ms | 140.6 ms |
| Draft acceptance rate | 9.1 % | 10.9 % |
| Accepted tokens per step | 1.64 | 1.76 |
| Position-0 draft acceptance | 21.2 % | 21.8 % |
| Failed requests | 0 | 0 |

**What the single-stream run says.** A 1,024-token prompt reaches its first
token in about 1.15 s, roughly 890 prompt tokens per second including
per-request overhead. Decode then runs at about 14 tokens per second: each
decode step takes 113 ms and keeps 1.64 of the 7 drafted tokens.

**Why 14 tok/s is a floor, not the typical speed.** The random dataset sends
1,024 random tokens, so the continuation is close to noise and the draft model
cannot predict it. Draft acceptance collapsed to 9 percent. The recipe authors
measured the same server at 63 to 65 tok/s on structured output (96 percent
acceptance) and 27 to 32 tok/s on prose (34 percent acceptance) with real
prompts. Under speculative decoding the per-step cost is roughly fixed, so
speed scales with how many drafts survive. Random input is the worst case for
that mechanism.

**What the concurrency-4 run says.** Four streams bought 1.3 times the
aggregate throughput and a 30-fold increase in time to first token, with a
median wait of 39 s. That is the mixed-prefill policy, not the cluster. With
the policy on `skip`, a new request's prefill is never mixed into a step that
is decoding for someone else, so a new request waits until running
generations finish. Each generation in this run took about 18 s, and the
median wait is roughly two of them. The recipe chose `skip` to keep a single
user's decode smooth; for two or more concurrent users it trades their first
token for it.

**Repeat of the single-stream run.** The same concurrency-1 command was run a
second time after the concurrency-4 run. Screenshot of the result block:
[`evidence/2026-09-14-vllm-bench-serve-random-c1-result.png`](../evidence/2026-09-14-vllm-bench-serve-random-c1-result.png).
No console log was saved; the JSON is in the container until
`fetch-results.sh` is run.

| Metric | First c1 run | Repeat c1 run |
| --- | ---: | ---: |
| Benchmark duration | 302.7 s | 270.1 s |
| Output token throughput | 13.5 tok/s | 15.2 tok/s |
| Mean TTFT | 1,151 ms | 1,108 ms |
| Mean time per output token | 69.7 ms | 61.9 ms |
| Mean inter-token latency | 113.1 ms | 112.3 ms |
| Draft acceptance rate | 9.1 % | 11.8 % |
| Accepted tokens per step | 1.64 | 1.83 |
| Position-0 draft acceptance | 21.2 % | 31.6 % |

The decode step itself is stable at about 112 ms. What moved is how many
drafts survived: the random prompts differ per run because the sampler is not
greedy, and the draft model got luckier the second time. That is a 12 percent
swing in throughput from acceptance alone, with 16 prompts per run. Quote a
range, not a point, until a run has more prompts and real text.

**Ignore** the "Peak concurrent requests" line (2 and 5). It counts the
benchmark's initial single-prompt test overlapping the first timed request.

## Caveats, in one place

1. Random-token input defeats the speculative decoder. Treat these decode
   numbers as the floor for this server. Real-text runs are listed under
   "Next" below.
2. The concurrency-4 TTFT reflects `GLM53_MIXED_PREFILL_CHUNK=skip` at the
   time of the run. Re-run after changing it before comparing.
3. Sampling was the server default (temperature 1.0, top-p 0.95), not greedy.
4. Only ConnectX-7 link A carried NCCL traffic. The second link was not
   configured for the run.
5. 16 prompts per run. Enough to see the shape, not enough for tight
   percentiles. Two single-stream runs with identical settings differed by
   12 percent in throughput, all of it from draft acceptance.

## Next

- The recipe's own decode protocol, for numbers directly comparable to the
  authors' table. On the head, from the recipe clone:
  ```bash
  python3 tests/bench_decode.py --phase structured --structured --runs 5 --max-tokens 400 --skip-coherence --out /tmp/glm53-structured.json
  python3 tests/bench_decode.py --phase prose --runs 5 --max-tokens 400 --skip-coherence --out /tmp/glm53-prose.json
  ```
- `vllm bench serve` with real text through the chat endpoint, thinking off,
  at concurrency 1 and 4:
  ```bash
  docker exec glm53-exl3-head vllm bench serve \
    --backend openai-chat --endpoint /v1/chat/completions \
    --base-url http://127.0.0.1:8888 --model GLM-5.3-Flash-EXL3 \
    --tokenizer Mia-AiLab/GLM-5.3-Flash-EXL3-TR3-4bpw \
    --dataset-name hf --dataset-path philschmid/mt-bench --hf-split train --hf-output-len 256 \
    --chat-template-kwargs '{"enable_thinking": false}' \
    --num-prompts 16 --max-concurrency 1 --save-result --result-dir /tmp/bench
  ```
- The concurrency-4 run again after setting `GLM53_MIXED_PREFILL_CHUNK=2048`
  in the recipe's `.env` and restarting, to measure what the trade costs the
  single stream and buys the second user.

## Adding a run

1. Save the full console output, command included, as
   `results/YYYY-MM-DD-vllm-bench-serve-<dataset>-<in>-<out>-c<N>.txt`.
2. On the head, run `bash fetch-results.sh` to pull the JSON files alongside it.
3. Add a row or a section above with the settings that differed from the
   table under "Server under test", and one sentence on what the run shows.
