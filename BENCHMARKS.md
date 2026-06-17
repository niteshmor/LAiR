# Benchmarks

Everything measured on the target machine (RTX 4070 12 GB + 32 GB RAM, llama.cpp
`b9354`), each model loaded one at a time with **its exact `.env` samplers**
(as-configured — the goal is to measure what actually ships). Numbers
are **coarse** — small N, so read accuracy as *tiers, not a leaderboard*, and
treat a few-point speed gap as noise. Harnesses live in `bench/`:
[`accuracy.py`](bench/accuracy.py), [`perf_curve.py`](bench/perf_curve.py),
[`ctx_audit.py`](bench/ctx_audit.py); see [How it's measured](#how-its-measured)
to reproduce.

> **Test machine (reference).** Every number here comes from one box: **NVIDIA
> RTX 4070** (12 GB VRAM), **Intel Core i7-13700F** (13th-gen), **DDR5-4400**
> system memory, running on **Windows + WSL2 + Docker**, llama.cpp `b9354`. The
> host has considerably more RAM than 32 GB; **32 GB is the slice granted to
> WSL2/Docker**, and that 32 GB (plus the 12 GB VRAM) is the envelope every
> result lives within. Beyond the GPU and that memory budget the specifics don't
> change the conclusions — they're listed for reproducibility, not as knobs to
> tune against.

> **Units.** Context sizes use the field's convention: **128K = 131072 tokens**
> (128 × 1024 — a deliberate power of two, the largest all six models support),
> and **200K = 200000 tokens** (a round decimal, used only by the Qwen3.6 default).
> Depths written like `~44k` are approximate *fill* levels (decimal token counts),
> not powers of two.

---

## Vocabulary (read this first)

If the terms below are already familiar, skip to the primer. They're the minimum
needed to read every table here.

- **Token** — the unit a model reads and writes; roughly ¾ of a word. A "200-token
  generation" is about 150 words out.
- **Context window** — the maximum number of tokens the model can hold at once
  (prompt + everything generated so far). Here it's `LLAMA_CTX_SIZE`, e.g. 128K.
- **Prefill (a.k.a. prompt-eval)** — processing the input prompt *before* the
  first new token. Done in parallel, so it's fast: hundreds–thousands of tok/s.
  **Prompt-eval t/s** measures this. It's the wait at the *start* of a turn, and
  it grows with the amount of accumulated context.
- **Decode** — generating the answer, one token at a time (each token depends on the
  last, so it can't be parallelized). This is the slow part, and **decode t/s** is
  the rate perceived while the model is typing. When this doc says "speed," it
  means decode t/s unless stated otherwise.
- **KV cache** — attention's running memory: for every token in context the model
  stores a key/value pair so it doesn't recompute the past each step. It **grows
  with context length**, and *how fast* it grows is set by the attention design
  (next section) — that's what makes long contexts expensive and why decode slows as
  a conversation fills up. We quantize it to 8-bit (`q8_0`) to fit more in VRAM.
- **Needle-in-a-haystack** — a long-context test: bury a specific fact (the
  "needle") deep inside a big filler document (the "haystack"), then ask for it.
  It measures whether the model can actually *retrieve* from deep context, which —
  as the primer explains — some attention designs cannot.
- **As-configured** — every benchmark runs each model with the exact samplers from
  its `.env` block (temperature, top-k, etc.), not some neutral test setting.

---

## Why the models behave differently (architecture primer)

The six models post very different speed-at-depth and long-context results, and
it's almost entirely explained by two architectural choices. This section is the
"why" behind every table below.

**1. Mixture-of-Experts (MoE) with few active parameters.** Every model here is
MoE: a large total parameter count, but only a few "expert" sub-networks fire per
token. So a 30B model with **3–4B active** decodes about as fast as a ~3–4B dense
model. `--fit on` exploits this by keeping the big, rarely-touched expert weights
**CPU-mapped in RAM** and putting only the dense attention on the GPU — which is
why these fit in 12 GB VRAM at all, and why VRAM stays ~11 GB regardless of model
size. Weights are **4-bit** (`UD-Q4_K_XL`, or gpt-oss's native MXFP4) and the KV
cache is **8-bit** (`q8_0`); both are what make long contexts fit.

**2. Attention design — the dominant factor.** This decides how the KV cache grows,
how fast decode is at depth, and whether the model can recall information from deep
in a long context. The KV cache is the per-token memory of attention; how it scales
determines the rest:

| Model | Total / active | Attention design | KV growth | Decode at depth | Deep retrieval |
|---|---|---|---|---|---|
| GLM-4.7-Flash | 30B / 3.6B | **Full, no GQA** (20 KV heads) | heaviest | slowest (≈O(n²)) | best — sees everything |
| Qwen3-Coder-30B | 30B / 3B | **GQA** (4 KV heads) | medium | degrades, collapses very deep | good |
| gpt-oss-20b | 21B / 3.6B | **Sliding-window** (half the layers) | small, capped | fast, fairly flat | limited — window horizon |
| Gemma-4-26B | 26B / 4B | **Sliding-window** (25 of 30 layers) | small, capped | fast | poor — can't see deep |
| Qwen3.6-35B-A3B | 35B / 3B | **Hybrid linear + full** | tiny | holds up well | good — full layers retain |
| Nemotron-3-Nano | 30B / 3B | **Hybrid SSM (Mamba2) + attn** | tiny | flattest | good — attn layers retain |

How to read each design:

- **Full attention, no GQA (GLM).** Every token keeps a separate key/value for
  every attention head, and attends to *all* previous tokens. That gives the
  richest recall but the **heaviest KV cache** and an attention cost that grows
  quadratically — so prefill and decode both get slow as the context fills.
- **Grouped-Query Attention / GQA (Qwen3-Coder).** Many query heads share a few
  key/value heads (here 4), shrinking the KV cache several-fold. Still full
  attention over all tokens, so decode still slows with depth — and at extreme
  depth it can fall off a cliff.
- **Sliding-window (gpt-oss, Gemma).** Most layers attend only to a fixed window
  of recent tokens, so the KV cache is **capped** and decode stays fast at any
  depth. The catch: a token outside the window is **invisible** — which is why
  these models can struggle to recall facts planted deep in a long context.
- **Hybrid linear + full (Qwen3.6).** Mixes linear-attention layers (a
  constant-size running summary, no per-token KV growth) with a few full-attention
  layers. Result: **tiny KV, fast decode at depth, and recall preserved** by the
  full layers. Best of both.
- **Hybrid SSM / Mamba2 (Nemotron).** State-space layers carry a fixed-size
  recurrent state instead of a growing KV cache, interleaved with a few attention
  layers. Decode is **nearly flat** with depth; the attention layers keep recall.

**3. Quantization tax.** Everything runs at ~4-bit. That's a small, model-dependent
quality hit — and it's why a model's *published* full-precision score doesn't carry
over (see Gemma in [Accuracy](#accuracy-local-q4)). Measuring on the target
hardware matters precisely because published numbers don't reflect Q4 on 12 GB.

---

## Performance

### Headline throughput (shallow ~2k context)

Decode is a 200-token generation (`ignore_eos`) over a ~2.2k-token prompt;
prompt-eval is that prompt's prefill rate. VRAM is `nvidia-smi`; host RAM is the
llama-server process resident set (`/proc/1/smaps_rollup`), which counts the
CPU-mapped weights that `docker stats` hides.

| Model | load | VRAM | host RAM | prompt-eval t/s | decode t/s | tool-calling |
|---|---|---|---|---|---|---|
| GLM-4.7-Flash | 20 s | 11.5 GB | 17.3 GB | 875 | 31 | clean |
| Qwen3.6-35B-A3B | 24 s | 11.2 GB | 20.8 GB | 777 | 43 | clean |
| Qwen3-Coder-30B-A3B | 19 s | 11.5 GB | 16.9 GB | 981 | 28 | XML leak → fixed (temp 0.2) |
| gpt-oss-20b | 13 s | 11.5 GB | 11.7 GB | 2003 | 40 | harmony 500 → fixed (top-k 100) |
| Gemma-4-26B | 17 s | 11.5 GB | 16.8 GB | 959 | 37 | clean |
| Nemotron-3-Nano | 30 s | 11.3 GB | 21.5 GB | 966 | 38 | clean |

VRAM is ~11.5 GB for everyone (`--fit on` fills the card); host RAM — the real
differentiator — ranges 11.7 GB (gpt-oss) to 21.5 GB (Nemotron).

### Decode vs context fill — how longer conversations slow down

The number that matters for a long agent session: **decode tokens/sec as the
context fills with conversation history.** Measured at the 128K (131072) context by filling
the KV cache to each depth and timing generation there (`bench/perf_curve.py`).

Decode throughput (tokens/sec) as the KV cache fills, ordered by how well each
holds up — which tracks the attention design exactly:

| Model | Attention | ~3k | ~11k | ~22k | ~44k | ~89k | ~120k | 3k→120k |
|---|---|---|---|---|---|---|---|---|
| Nemotron-3-Nano | hybrid SSM (Mamba2) | 46 | 42 | 46 | 42 | 40 | 37 | **−20%** |
| Qwen3.6-35B-A3B | hybrid linear+full | 42 | 43 | 41 | 39 | 34 | 31 | **−27%** |
| Gemma-4-26B | sliding-window | 39 | 37 | 35 | 32 | 27 | 25 | −35% |
| gpt-oss-20b | sliding-window | 46 | 45 | 43 | 38 | 31 | 28 | −40% |
| GLM-4.7-Flash | full, no GQA | 32 | 29 | 31 | 25 | 18 | 15 | **−53%** |
| Qwen3-Coder-30B | GQA (full) | 30 | 29 | 25 | 22 | 16 | 13 | **−57%** |

The ~3k–89k points come from `perf_curve.py`; the ~120k point is the `ctx_audit.py`
full-prefill measurement (same decode-at-depth metric). Small shallow wobble
(±2 t/s) is measurement noise — the trend is what matters.

**The practical takeaway for long conversations:** a context that fills toward
~120k **barely dents** the SSM/linear models (Nemotron −20%, Qwen3.6 −27%) but
**roughly halves** the full-attention ones (GLM −53%, Qwen3-Coder −57%). The
sliding-window models sit in between. So for an agent that accumulates a long
history, the *default (Qwen3.6) and Nemotron stay fast deep into the session*,
while GLM and Qwen3-Coder slow down exactly when the conversation is longest.

The shape is set entirely by the attention design above: the SSM/linear/sliding
families stay near-flat, while the full-attention models (GLM, Qwen3-Coder) lose
throughput as the cache grows — Qwen3-Coder most steeply.

### Maximum context: 128K vs 200K

Can we push past 128K? Audited by filling each model to ~92% of its window
(`bench/ctx_audit.py`). **Memory is not the limiter** — VRAM stays ~11 GB at any
context, and 200K adds only +0.3–2.8 GB RAM, so every model *fits* 200K. The wall
is speed at depth.

| Model | Native | RAM 128K → 200K | Prefill @120k | Decode @120k | Decode @184k (200K) | 200K verdict |
|---|---|---|---|---|---|---|
| Qwen3.6-35B-A3B | 256K | 20.8 → 21.6 GB | 936 | 31 | 27 | ✅ viable |
| Nemotron-3-Nano | 128K | 21.5 → 22.1 GB | 1313 | 37 | 33 | ⚠ past 128K training |
| gpt-oss-20b | 128K | 11.7 → 13.2 GB | 1898 | 28 | — | ❌ capped at 128K |
| Gemma-4-26B | 256K | 16.8 → 17.1 GB | 1094 | 25 | 20 | ⚠ fast but can't retrieve deep |
| GLM-4.7-Flash | ~200K | 17.0 → 17.3 GB | 311 | 15 | — | ❌ prefill too slow |
| Qwen3-Coder-30B | 256K | 17.0 → 19.8 GB | 681 | 13 | **1.4** | ❌ decode collapses |

- **gpt-oss is hard-capped at its 128K training** — the 200K request failed outright.
- **GLM's no-GQA prefill (311 t/s) is ~6× slower than gpt-oss** — a 184k prefill
  takes ~10 min and timed out in the audit.
- **Qwen3-Coder's decode collapses to ~1.4 t/s at 184k** — unusable.
- **Fast ≠ usable:** Gemma holds 20 t/s at 200k but can't retrieve from deep
  context; Nemotron's 200K speed is real but *beyond* its 128K training.

**Verdict:** 128K (`131072`) is the standard (largest context all six support
natively, what these benchmarks use). **Qwen3.6 is the one model where going higher
is a real win** — within its 256K training, fits, fast at depth — so its block (the
default) ships at 200K (`200000`). Every other block stays at 131072.

---

## Perceived performance — real tasks

Raw decode tok/s is not what users perceive as speed. What matters is **wall-clock
time to a finished, correct answer**, which depends just as much on *how many* tokens a
model emits (verbosity + "thinking") as on how fast it emits them. We ran 24 real,
auto-graded tasks in three sizes (`bench/realtask.py`), recording output tokens,
wall-clock latency, and correctness:

- **Quick** — one-liners: `47*13`, reverse a string, fix a buggy `add()`.
- **Medium** — one function + a short word problem: `is_palindrome`, a phone regex.
- **Large** — a full class/algorithm: `Stack`, `merge_sort`, a calculator, `LRUCache`.

**Every model solved essentially everything (7–8/8 each)** — so on everyday tasks
accuracy is *not* the differentiator. Verbosity and latency are, and they diverge
by up to ~10×, which **inverts the raw-tok/s ranking**:

| Model | thinking | Quick (tok / wall) | Medium (tok / wall) | Large (tok / wall) |
|---|---|---|---|---|
| Qwen3-Coder-30B | off (instruct) | 4 / 0.6 s | 34 / 2.0 s | 198 / 8.1 s |
| GLM-4.7-Flash | off | 3 / 0.5 s | 48 / 2.1 s | 264 / 9.4 s |
| **Qwen3.6-35B-A3B** *(default)* | off | 4 / 0.8 s | 31 / 1.5 s | 291 / 8.2 s |
| Nemotron-3-Nano | on | 60 / 2.1 s | 136 / 4.6 s | 447 / 13.3 s |
| gpt-oss-20b | on (always) | 55 / 1.6 s | 174 / 4.7 s | 568 / 13.9 s |
| Gemma-4-26B | on | 188 / 5.7 s | 472 / 14.0 s | 1368 / 40.2 s |

All accuracy 8/8 except GLM Medium (7/8). Wall-clock is the server round-trip;
~0.2 s is fixed overhead, which dominates the sub-second Quick times. Since
accuracy ≈ 100%, the composite "expected seconds per solved task" (= wall ÷
accuracy) ≈ the wall-clock column.

**Takeaways:**

- **The raw-tok/s ranking inverts.** Gemma posts a healthy ~37 tok/s at a shallow
  context — yet it is the *slowest model to actually finish anything*, because it
  emits 5–60× more tokens. A Quick task: GLM **0.5 s** vs Gemma **5.7 s** — **11×
  slower for the same trivial answer**, purely from thinking overhead.
- **The thinking tax is worst on small tasks.** The no-think models answer Quick
  tasks in 3–4 tokens (~0.5 s); the reasoning models spend 55–188 tokens "thinking"
  about `47*13`. The gap narrows on Large tasks (everyone writes real output) but
  never closes.
- **No-think wins for an interactive agent.** The three thinking-off models
  (Qwen3-Coder, GLM, Qwen3.6) are 3–11× faster across the board — and per the
  accuracy section, thinking-on bought *no* reasoning advantage. For an agent that
  fires many small/medium turns, **low verbosity beats raw throughput.** It's also
  why the default (Qwen3.6) feels fast: near-instant on small turns, terse on large
  ones, top-tier accuracy.
- This is the **cost** side of the thinking-on/off question: enabling thinking on a
  model would multiply its per-turn latency several-fold, so it's only worth a
  dedicated variant where it clears a real accuracy gain.

## Accuracy (local, Q4)

An **as-configured** probe across five auto-graded categories (~68 items/model):
logic/reasoning, computed math (incl. AIME-style), unit-tested code (DP/graph
algorithms), adversarial tool-calling, and needle retrieval in an ~88k-token
haystack (`bench/accuracy.py`). Coarse (±~6%) — tiers, not a leaderboard.

| Model | Reason | Math | Code | Tool | LongCtx 88k | Composite |
|---|---|---|---|---|---|---|
| **Qwen3.6-35B-A3B** *(default)* | 100 | 95 | 83 | 86 | 90 | **90.8** |
| gpt-oss-20b | 83 | 95 | 100 | 93 | 80 | 90.2 |
| Nemotron-3-Nano | 100 | 85 | 100 | 64 | 100 | 89.9 |
| GLM-4.7-Flash | 100 | 85 | 92 | 71 | 100 | 89.6 |
| Qwen3-Coder-30B | 92 | 85 | 100 | 64 | 100 | 88.2 |
| Gemma-4-26B | 83 | **50** | 100 | 86 | **20** | 67.8 |

**The top five are a statistical tie (88–91), but each has a *different* weakness**
— there's no dominant model; match the model to the task:

- **Qwen3.6** (default): best all-rounder; weakest only at hard **code** (DP/graph).
- **gpt-oss-20b**: best at **tools** (93%) and math; weakest at hard **reasoning**,
  and its long-context retrieval slips at depth (100% at a ~57k needle depth → 80% at ~88k;
  the sliding window has a horizon).
- **Nemotron / GLM**: strong generalists; weakest at **tool-calling** (64% / 71%).
- **Qwen3-Coder**: the **code specialist** (only model at 100% on the hard
  algorithms) but the **worst at tools** (64%).
- **Gemma-4-26B** is the clear weak link — **50% hard math, 2/10 long-context** (the
  sliding-window retrieval failure, reproduced on the deeper haystack). Strong on
  paper, worst here; **disqualified for math or long-context work**.

Tool-calling is the sharpest separator (93% → 64%), which matters most for an agent
like this — and it tracks the architecture story only loosely, so it's worth
measuring directly.

The categories above lean technical (math/code). To check whether any model is
*too* programming-specialized for everyday use, see the next section.

## General knowledge & web research

**General knowledge (non-code).** A 15-item everyday-facts quiz (capitals, history,
science, language — `bench/accuracy.py --cats general`): **all six models scored
15/15.** So there is *no* recall gap — the code-specialist Qwen3-Coder knows
general facts as well as any of them. (Easy and saturated; it rules out a knowledge
gap but doesn't test open-ended competence — that's the research test below.)

**Web research (end-to-end, real search).** Each model gets a `web_search` tool and
must research 8 verifiable questions through the **live SearXNG/Tor stack**:
formulate a query, read genuinely noisy results, and synthesize an answer
(`bench/research.py`). This is the real test of "can it do general tasks, not just
code" — and it captures real perceived performance, since wall-clock is dominated by
slow searches and a model that issues poor queries wastes extra ones.

| Model | accuracy | searches / q | wall / q | sec / correct |
|---|---|---|---|---|
| **Qwen3-Coder-30B** | 8/8 | **1.0** | **12 s** | **12 s** |
| Qwen3.6-35B-A3B *(default)* | 8/8 | 1.4 | 20 s | 20 s |
| Nemotron-3-Nano | 8/8 | 1.0 | 21 s | 21 s |
| Gemma-4-26B | 8/8 | 1.1 | 19 s | 19 s |
| GLM-4.7-Flash | 7/8 | 1.4 | 18 s | 20 s |
| gpt-oss-20b | 7/8 | 1.6 | 21 s | 24 s |

- **No code-specialization penalty — the opposite.** Qwen3-Coder was the *best*
  researcher: 8/8, the most query-efficient (one well-formed search per question),
  and the fastest (12 s/q). All six research general topics well (7–8/8).
- **Query efficiency is a real differentiator.** The terse, no-think models issue
  one good query and stop; gpt-oss issues the most redundant queries (1.6 searches/q). Both misses
  (GLM on the NVIDIA CEO, gpt-oss on the tallest building) were *synthesis* failures
  — extra searches, then an empty answer — not knowledge gaps.
- **Wall-clock is search-bound (~12–22 s/q),** and the verbosity tax shows up again:
  the reasoning models (Nemotron, gpt-oss) are slower per question than the terse
  ones. *Caveat:* live Tor search is slow and noisy, N is small (8), and scores
  vary run-to-run — read this as "all competent, Qwen-Coder notably crisp," not a
  precise ranking.

---

## Thinking on/off

GLM, Qwen3.6, and Nemotron are run with **thinking disabled** (tight agent loop).
Is that the right call? We toggled thinking **on** — `enable_thinking:true` plus
each model's *matched* thinking samplers (params only, no recompile) — and re-ran
the two categories with headroom (math, code). Comparison vs the thinking-off
baseline:

| Model | math (off → on) | code (off → on) | net |
|---|---|---|---|
| GLM-4.7-Flash | 85% → 95% | 92% → 100% | +1–2 items |
| Nemotron-3-Nano | 85% → 95% | 100% → 100% | +1 item (math) |
| **Qwen3.6-35B-A3B** | 95% → **70%** | 83% → **67%** | **worse** |

- **GLM / Nemotron**: a +1–2 item bump on n=12–20 — *within noise*, not a robust
  gain, and it still buys the 3–11× thinking latency tax (see
  [Perceived performance](#perceived-performance--real-tasks)).
- **Qwen3.6: thinking-on is actively harmful.** On the AIME-style problems its
  reasoning **runs away** — given a 12,000-token budget it still hit the cap
  without reaching an answer (**~5.5 min per problem, wrong**), while thinking-off
  solves the same problems tersely and correctly. The high thinking temperature
  (1.0) sends it into non-terminating chains.

**Verdict:** thinking-on earns no robust accuracy gain on any of the three, costs
several-fold latency, and is catastrophic for the default (Qwen3.6). **Thinking
stays off for all of them — no thinking variant is added to `.env`.** (Feasible via
params, just not worth it — which is the whole point of measuring before shipping.)

## How it's measured

The `llama` container sits on an internal Docker network with no host route, so the
harnesses talk to it the same way Claude Code does — through the `claude` container
(`docker exec … curl http://llama:8080/v1/messages`). Each model is loaded once via
`docker compose up --force-recreate llama` with its `.env` params exported (shell
env overrides `.env`), all categories run on that load, and the default model is
restored at the end.

Design choices that keep the numbers honest:

- **As-configured.** Samplers come from each `.env` block; only context/fill is
  varied for the speed sweeps. Temperature etc. are never changed.
- **Auto-gradeable only.** Every accuracy task has an objective check — a number, a
  letter, code that passes unit tests, a planted fact. No human/LLM judging.
- **Math golds are computed in-process** (e.g. the count of `a+b=999` pairs with no
  digit 0), never typed by hand, so a hard problem can't have a wrong answer key.
- **Code runs sandboxed** — the model's function is executed in
  `docker run --rm --network none python:3-alpine` with a timeout; the unit tests
  are the gold.
- **Tool discipline is tested** — some items are answerable directly ("capital of
  Japan?"); calling a tool there is a failure.
- **Long context reuses one haystack** — built once with needles at increasing
  depth, so llama-server's prompt cache makes only the first deep query expensive.
- **Speed sweeps reuse a growing prefix** so the KV cache is reused across depths;
  only the deepest prefill is paid in full.

Reproduce:

```bash
python3 bench/accuracy.py                 # ~68-item accuracy suite, all 6 models
python3 bench/accuracy.py --cats general  # general-knowledge (non-code) quiz
python3 bench/accuracy.py --thinking --cats math,code   # thinking-ON variants (GLM/Qwen3.6/Nemotron)
python3 bench/research.py                 # end-to-end web research through real SearXNG/Tor
python3 bench/realtask.py                 # perceived performance: tokens + wall-clock on real tasks
python3 bench/perf_curve.py               # decode t/s vs context fill
python3 bench/ctx_audit.py                # 128K vs 200K hardware + speed
```

A run takes from minutes (`--smoke`) to a few hours (full accuracy across the
reasoning models). Difficulty is calibrated to land models in the ~40–80% band; if
a future run ceilings at ~100%, *harden the items* rather than adding more.
