# LAiR — LLM Agent In Residence

LAiR lets you run a full coding agent —
[Claude Code](https://github.com/anthropics/claude-code) — against an LLM running
**on your own GPU**, while keeping it plugged into the live web for search,
browsing, and research. You get a private agent you fully own and control that
*isn't* boxed in by the model's training cutoff: it can pull up current events,
read today's documentation, dig through real sources, and act on the world — the
kind of "deep research" the big labs sell as a subscription, running on your
machine instead of theirs.

Spin it up with a single `docker compose up`. The agent's web traffic exits through
a rotating pool of Tor nodes — which keeps your browsing anonymous and, just as
important, keeps it *working*: an automated agent hammering search engines gets
flagged as a bot and blocked, and rotating exits give you a way around that.
Everything below — hardware, the model menu, and the internals — is the operator's
manual.

## Hardware this is tuned for

Every model and parameter choice in this repo targets one machine profile:

- **GPU:** NVIDIA RTX 4070 — **12 GB VRAM**
- **RAM:** **32 GB** system memory
- **Combined budget:** ~44 GB (VRAM + RAM), of which the other containers
  (claude, searxng, playwright, haproxy, 3× tor) take a few GB.
- **Goal:** 20+ tokens/sec at a usable quality level.

That budget is why every bundled model is a **Mixture-of-Experts (MoE) with only
3–4 B active parameters**. `--fit on` keeps the large expert weights CPU-mapped
in RAM and puts only the dense attention on the GPU, so decode speed tracks the
small *active* parameter count rather than the full model size. Dense models of
comparable quality (Gemma 4 31B, Qwen3.6-27B) are deliberately excluded — they
don't fit 12 GB VRAM and fall back to slow CPU inference.

If you run different hardware, the model menu and the context sizes in `.env`
are the first things to revisit.

## Model menu (quick reference)

Six vetted models live in `.env` as commented blocks — uncomment exactly one.
All fit the ~44 GB budget at a 128K (131 072-token) context with a quality-grade Q4
quant. The `decode` / `VRAM` / `RAM` columns are **measured on this machine** (a
shallow-context snapshot; full results, scaling curves, and method are in
[BENCHMARKS.md](BENCHMARKS.md)), not estimates.

| # | Model | Total / active | Best for | decode t/s | VRAM | RAM |
|---|---|---|---|---|---|---|
| 1 | GLM-4.7-Flash | 30B / 3.6B | agentic general-purpose | 31 | 11.5 GB | 17.3 GB |
| 2 | **Qwen3.6-35B-A3B** *(default)* | 35B / 3B | newest general all-rounder | 43 | 11.2 GB | 20.8 GB |
| 3 | Qwen3-Coder-30B-A3B-Instruct | 30B / 3B | code + tools | 28 | 11.5 GB | 16.9 GB |
| 4 | gpt-oss-20b | 21B / 3.6B | fastest, flat at depth | 40 | 11.5 GB | 11.7 GB |
| 5 | Gemma 4 26B-A4B-it | 26B / 4B | newest Gemma, general | 37 | 11.5 GB | 16.8 GB |
| 6 | Nemotron-3-Nano-30B-A3B | 30B / 3B | hybrid SSM, tool-calling | 38 | 11.3 GB | 21.5 GB |

`VRAM` is GPU memory in use (`--fit on` fills the card to ~11.5 GB for every
model, so it is not a differentiator). `RAM` is the llama-server process's
resident set — the real host-memory cost, including the CPU-mapped expert
weights. Every model lands well inside the 12 GB VRAM + 32 GB RAM envelope.

**Note on context vs. memory:** the KV cache — not the weights — is what grows
with context length, and it differs ~10× across these models because of their
attention design. GLM-4.7-Flash uses no GQA, so its KV cache is by far the
heaviest (~1.2 GB per +10k tokens); keep its context modest. The others use
GQA / sliding-window / linear attention and are far cheaper at long context.
Each `.env` block notes its own KV cost. The floor is 100 000 tokens —
Claude Code's auto-compaction window is derived from `LLAMA_CTX_SIZE` and is
silently disabled below 100k.

## Benchmarks

Full **speed**, **context-scaling**, and **accuracy** results — plus a
model-architecture primer that explains *why* the six models behave so differently
— live in **[BENCHMARKS.md](BENCHMARKS.md)**. The short version:

- **Speed:** ~13–43 tok/s decode at a shallow context; it falls as the context
  fills, and how far depends on attention design (SSM/linear stay near-flat,
  full-attention models like GLM and Qwen3-Coder lose throughput — Qwen3-Coder
  sharply so past ~150k).
- **Context:** 128K (`131072`) is the standard; only Qwen3.6 is worth pushing to 200K.
  Memory isn't the limit — speed at depth is.
- **Accuracy:** the top five are a tight tie (88–91 composite), each with a
  *different* weak spot (Qwen3.6 hard code, gpt-oss hard reasoning, GLM/Coder/
  Nemotron tool-calling). Gemma-4 is the outlier — weak hard math and can't
  retrieve from deep context.
- **Perceived speed (real tasks):** raw tok/s is misleading. On everyday tasks
  every model is accurate, so what dominates is verbosity — the no-think models
  (Qwen3.6, GLM, Qwen3-Coder) finish 3–11× faster than the thinking ones. Gemma-4
  has decent tok/s yet is the slowest to *finish* (it emits 5–60× more tokens).
- **General / web research:** the Qwen models are *not* too code-specialized — all
  six ace general knowledge, and on end-to-end web research (real SearXNG/Tor)
  Qwen3-Coder is the *best* (8/8, one search/question, fastest).

The benchmark harnesses are in [`bench/`](bench/).

## First-time setup

```bash
# 1. Create the model volume once. It's declared `external` so that
#    `docker compose down -v` can never wipe an expensive model download.
docker volume create lair_llama-models

# 2. Create your local config from the template (.env is gitignored).
cp .env.example .env

# 3. Configure. Qwen3.6-35B-A3B (block [2]) is already uncommented as the
#    default, so you can skip ahead for a first run; to pick another model,
#    uncomment exactly one other block (see the model menu above).
$EDITOR .env

# 4. Build images, auto-download the model, start the stack.
docker compose up -d --build

# 5. Confirm the network boundary holds.
./verify-isolation.sh
```

The model is fetched automatically on first start by the `model-downloader` init
container (it lives on `external_net`; the llama server stays isolated on
`private_net`). Subsequent starts skip the download if the file is already in the
volume.

## Day-to-day commands

```bash
docker compose up -d                            # start stack
docker compose down                             # stop stack (keeps volumes)
docker compose exec -it claude \
  claude --dangerously-skip-permissions         # interactive Claude session
```

The claude container has a statusline (model, context %, git branch, timing) that
activates automatically — no configuration needed.

`sudo apt-get` works inside the container: the entrypoint configures apt to route
through haproxy → Tor on every start.

SearXNG is available in a browser at **http://localhost:8888** once the stack is up.

## Choosing or swapping models

Open `.env`, comment out the currently-active block, and uncomment exactly one
other block. Each block is self-contained — HF coordinates, samplers, context
size, and chat-template flags are a matched set tuned per model, so don't
mix knobs across blocks. Then:

```bash
docker compose up -d model-downloader           # downloads new model if not cached
docker compose up -d llama claude               # restart with new model + context
```

`claude` is restarted too because its context window (`CLAUDE_CODE_*`) is derived
from `LLAMA_CTX_SIZE`. The model alias and the local filename derive automatically
from `LLAMA_HF_REPO` / `LLAMA_HF_FILE` — no other variables to touch.

The old model file stays in the volume. Delete one manually if needed:

```bash
docker run --rm \
  -v lair_llama-models:/models \
  alpine \
  rm /models/<old-filename>.gguf
```

Wipe the entire models volume (forces re-download next start):

```bash
docker volume rm lair_llama-models
```

## Logs

```bash
docker compose logs -f llama                    # llama.cpp inference server
docker compose logs -f model-downloader         # HuggingFace download progress
docker compose logs -f searxng                  # search engine errors, engine blocks
docker compose logs -f haproxy | grep tor-      # which tor-N handled each request
docker compose logs -f tor-proxy-1              # circuit building, exit nodes
```

Exit nodes show up in Tor logs as: `exit circ ... SomeRelay at 185.x.x.x`

## Rebuild after upstream changes

```bash
docker compose build --no-cache llama   && docker compose up -d llama
docker compose build --no-cache claude  && docker compose up -d claude
```

Entrypoint and asset changes require a rebuild — `docker compose up -d` alone
won't pick them up. Affected files: `claude/entrypoint.sh`, `claude/CLAUDE.md`,
`claude/statusline-command.sh`, `llama/entrypoint.sh`, `downloader/download.sh`.

## Pinned versions

Every external component is pinned to the exact build verified on this stack, so
a rebuild reproduces the current behaviour instead of pulling whatever is latest.
This matters most for **llama.cpp**, whose tool-call parser has a history of
regressions (see the tool-calling notes in [BENCHMARKS.md](BENCHMARKS.md) and the
`.env` block comments).

### Versioned components

The **Latest seen** column is a dated snapshot so a from-scratch builder can tell
how far behind a pin is at a glance — click **Check** for live status. (There is
deliberately no build-time drift check: a `RUN` step in the Dockerfile would be
cached and silently go stale, which is worse than nothing.)

| Component | Pinned | Released | Latest seen (2026-06-07) | Pinned in | Check |
|---|---|---|---|---|---|
| **llama.cpp** | `b9354` (commit `9777256`) | 2026-05-27 | `b9553` — ~199 builds ahead | `llama/Dockerfile` | [releases](https://github.com/ggml-org/llama.cpp/releases) |
| **Claude Code** | 2.1.158 | 2026-05-30 | 2.1.168 — 10 releases ahead | `claude/Dockerfile` | [npm](https://www.npmjs.com/package/@anthropic-ai/claude-code) |
| **mcp-searxng** | 1.0.5 | 2026-05-25 | 1.2.1 — 2 minors ahead | `claude/Dockerfile` | [npm](https://www.npmjs.com/package/mcp-searxng) |
| **@playwright/mcp** | 0.0.75 | 2026-05-07 | 0.0.75 — current | `playwright/Dockerfile` | [npm](https://www.npmjs.com/package/@playwright/mcp) |
| **node** (playwright base) | 22.22.3 | 2026-05-13 | 22.22.3 — current | `playwright/Dockerfile` | [releases](https://nodejs.org/en/about/previous-releases) |

### Digest-pinned images

These track rolling upstream tags, so they have no semantic version — pinned by
content digest (captured 2026-06-07) in `docker-compose.yml` / the `Dockerfile`s:
`searxng/searxng:latest`, `haproxy:lts-alpine`, `dperson/torproxy`, `alpine/socat`,
`ubuntu:24.04`, `alpine:latest`. To see drift, compare against the current tag on
the registry (`docker manifest inspect <image>:<tag>`).

**Soft pins (not byte-exact):** the NodeSource `setup_22.x` apt source tracks the
latest Node 22.x patch (NodeSource prunes old patches, so it can't be patch-pinned
— verified on 22.22.2), and `nvidia/cuda` is pinned by its version tag
(`13.1.2-…-ubuntu24.04`) rather than digest. Both are stable in practice.

**To bump a pin:** change the value, rebuild that one service, and — for llama.cpp
especially — re-run the [benchmarks](BENCHMARKS.md) (`bench/*.py`) and the
tool-call checks before trusting it. Don't bump everything at once; that defeats
the point of knowing what changed.

## `.env` reference

`.env` has two sections: **GLOBAL** knobs (same for every model) and a **MODEL**
menu of commented blocks. Sampler / context / chat-template values live inside
each model block, not here — they're model-specific.

### Global knobs

| Variable | Required | Description |
|---|---|---|
| `HF_TOKEN` | no | HuggingFace token — only for private/gated repos |
| `LLAMA_BATCH_SIZE` / `LLAMA_UBATCH_SIZE` | no | Prompt-processing batch sizes |
| `LLAMA_CACHE_TYPE_K` / `LLAMA_CACHE_TYPE_V` | no | KV cache quantization (default `q8_0`) |
| `LLAMA_FLASH_ATTN` | no | Flash attention (default `on`; required for q8_0 KV) |
| `LLAMA_SLEEP_IDLE_SECONDS` | no | Unload model from VRAM after N idle seconds |
| `ANTHROPIC_AUTH_TOKEN` | yes | Any non-empty string (llama-server doesn't validate) |

### Per-model block (set inside one uncommented MODEL block)

| Variable | Description |
|---|---|
| `LLAMA_HF_REPO` | HuggingFace repo, e.g. `unsloth/GLM-4.7-Flash-GGUF` |
| `LLAMA_HF_FILE` | Filename within the repo, e.g. `GLM-4.7-Flash-UD-Q4_K_XL.gguf` |
| `LLAMA_CTX_SIZE` | Context window (**min 100000** to keep compaction working) |
| `LLAMA_TEMP` / `LLAMA_TOP_P` / `LLAMA_MIN_P` | Samplers (Unsloth-recommended per model) |
| `LLAMA_EXTRA_ARGS` | Verbatim extra flags: `--fit on --jinja`, top-k, chat template, etc. Single-quote when it contains JSON braces. |

`LLAMA_MODEL` is an optional override for the derived local filename
(`{HF_ORG}_{HF_REPO}_{HF_FILE}`, slashes → underscores); leave it unset to derive
automatically.

## Search result filtering

Many SearXNG engines are enabled for redundancy (so one engine getting blocked
doesn't break a query) — but that means a single search returns 100+ results, most
of them low-relevance noise the model would otherwise have to sift through. `mcp-searxng`
returns *all* of them by default, so we patch the copy we install
(`claude/patch-searxng.js`) to filter on SearXNG's own per-result relevance score:

- **`SEARXNG_MAX_RESULTS`** (default `8`) — keep only the top-N results by score.
- **`SEARXNG_MIN_SCORE`** (default `0.3`) — drop results below this relevance score.

A fallback keeps the top 5 if filtering would return nothing, so a search never
comes back empty. The defaults take a typical ~100-result response down to the ~8
relevant ones. **Both are set in `.env`** and synced into the mcp by the claude
entrypoint, so tuning is just `docker compose up -d claude` — no rebuild.

## Tor pool

3 proxies (`tor-proxy-1` through `tor-proxy-3`), each with an independent Tor
circuit. Circuits rotate every 10 minutes. HAProxy round-robins requests across
all 3. SearXNG talks directly to each proxy's SOCKS5 port.

Each proxy has its own named Docker volume (`tor-state-1/2/3`) that persists the
network consensus, relay descriptors, and guard state across restarts. The first
cold start (empty volumes) still bootstraps from scratch (~1 min); subsequent
restarts take seconds. `docker compose down -v` wipes the state volumes for a
true clean start.

To resize the pool: edit `docker-compose.yml`, `searxng/config/settings.yml`, and
`haproxy/haproxy.cfg` in lockstep, then `docker compose up -d`.

## Verification scripts

```bash
./verify-isolation.sh     # proves claude has no direct internet egress
./verify-compaction.sh    # proves context auto-compaction fires (needs ctx ≥100k)
./verify-e2e.sh           # end-to-end: search, URL fetch, and Playwright all
                          #   work through the Claude Code CLI (--infra-only
                          #   skips the slow model-driven checks)
```

`verify-e2e.sh` runs two layers: a deterministic infrastructure check
(llama health, SearXNG search, a URL fetch through haproxy → Tor, both MCP
servers connected) and an agent check that drives each MCP tool through the
`claude` CLI and asserts the tool was actually called. The agent layer runs
real model turns, so it is slow; pass `--infra-only` to skip it.

## Gotchas

- **Cold start is slow (first time only)** — on the first `up` with empty `tor-state`
  volumes, all 3 Tor proxies bootstrap from scratch (~1 min). `docker compose ps`
  shows them as `(health: starting)` during this window; searxng and haproxy wait for
  all 3. Subsequent restarts use the cached consensus and are fast.

- **`docker compose up` fails with "external volume not found"** — the
  `llama-models` volume doesn't exist yet. Create it once:
  `docker volume create lair_llama-models` (see First-time setup).

- **Download fails on first start** — check `docker compose logs model-downloader`.
  Common causes: wrong `LLAMA_HF_REPO`/`LLAMA_HF_FILE`, gated repo without `HF_TOKEN`,
  or a transient network error. Fix `.env` and run `docker compose up model-downloader`
  to retry.

- **`llama` keeps restarting** — model file missing despite downloader succeeding.
  Check the derived filename: `{HF_ORG}_{HF_REPO}_{HF_FILE}` (slashes → underscores).
  If you set a custom `LLAMA_MODEL`, ensure it matches the actual file in the volume.

- **Out of memory at long context** — the KV cache scales with `LLAMA_CTX_SIZE`.
  GLM-4.7-Flash is the heaviest (no GQA): ~24 GB of KV alone at 200K. Lower
  `LLAMA_CTX_SIZE` (stay ≥100000) or switch to a GQA / sliding-window model.

- **Slow decode on MoE models (~8 t/s instead of ~20 t/s)** — do not set
  `--n-gpu-layers` explicitly. Use `--fit on` in `LLAMA_EXTRA_ARGS`: it keeps the
  large expert weights CPU-mapped and puts only dense attention on GPU, leaving room
  for the KV cache. Combining `--fit on` with explicit `--n-gpu-layers` causes a
  conflict and silently falls back to CPU inference.

- **New MCP servers not showing after claude image rebuild** — the `~/.claude` state
  lives in the `lair_claude-state` volume and survives rebuilds. Wipe it to
  pick up the new config: `docker volume rm lair_claude-state` (also erases
  session history).

- **DNS leak caveat** — external names resolve from inside private-net containers
  because Docker's resolver at `127.0.0.11` forwards queries through the daemon. The
  data path (TCP/HTTP) is still sealed — `internal: true` blocks actual connections.

- **`verify-isolation.sh` check #1 fails** — claude can reach the internet directly.
  Inspect: `docker network inspect lair_private_net` and confirm
  `"Internal": true`.

## License

[Apache License 2.0](LICENSE). Copyright © 2026 Nitesh Mor.

The pinned upstream components LAiR builds on — llama.cpp, SearXNG, HAProxy,
Tor, Playwright, and Claude Code — are each distributed under their own licenses.

## Naming & ownership

*LAiR* is just the name of this integration project. It bundles and depends on
independent third-party projects — including
[llama.cpp](https://github.com/ggml-org/llama.cpp),
[SearXNG](https://github.com/searxng/searxng),
[HAProxy](https://www.haproxy.org/), [Tor](https://www.torproject.org/),
[Playwright](https://playwright.dev/),
[Claude Code](https://github.com/anthropics/claude-code), and the bundled
models — each owned by its respective authors and distributed under its own license
and terms. All product names, trademarks, and registered trademarks (including
"Claude" and "Claude Code", which are trademarks of Anthropic) are the property of
their respective owners. Their use here is purely descriptive — to identify the
components LAiR integrates with — and does **not** imply any affiliation with,
endorsement by, or ownership of those projects. LAiR claims no rights in them; it
only provides the glue that wires them together.
