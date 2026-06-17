# Research and Web Search Instructions

## When to Search

If a web search tool is available — whether via MCP, a native tool, or any
other mechanism — **treat it as a first-class resource, not a last resort.**
Do not answer from training data when the query falls under the trigger rules
below. The tool exists to be used; use it without being asked.

### Hard rules: you must search before answering if any of these are true

1. The query involves a **named person** (public figure, professional, etc.)
2. The query involves a **named company, product, service, or brand**
3. The query involves a **current event, ongoing situation, or recent
   development**
4. The query contains signals like: *latest, current, recent, now, today,
   this year, who is, what is [proper noun], how is [person/company] doing*
5. The answer **could have changed since your training cutoff** — versions,
   prices, availability, status, relationships, roles, rankings
6. You are being asked to **analyze, profile, or summarize** a real person,
   organization, or creative work
7. The subject is **niche enough** that training coverage is uncertain — when
   in doubt, search rather than answer with false confidence

### When not to search

- Well-established technical concepts: algorithms, language specs, math, CS
  fundamentals
- Code generation, review, editing, or debugging tasks
- Reasoning or analysis tasks where all required information is already in
  context
- Tasks operating entirely within local files or the codebase

---

## Web Access in This Environment

This container's private network has no direct internet egress. All outbound
traffic routes through a pool of Tor proxies via haproxy.

| Task                             | Tool to use                               |
|----------------------------------|-------------------------------------------|
| Search the web                   | `searxng` MCP — search tool               |
| Fetch a known URL (plain HTML)   | `searxng` MCP — URL-read tool             |
| Render a JS-heavy page (SPA etc) | `playwright` MCP — navigate + text        |
| Download a file / call an API    | `curl` or `wget` (see proxy flags below)  |

Do **NOT** use Claude Code's built-in `WebSearch` or `WebFetch` tools —
they bypass Tor routing.

### curl / wget via Tor

`HTTP_PROXY` and `HTTPS_PROXY` are pre-set in the environment, so a plain
`curl https://…` or `wget https://…` already routes through haproxy → Tor.
When you want to be explicit (scripts, one-liners):

```bash
# curl — explicit proxy
curl -x http://haproxy:8118 https://example.com

# wget — explicit proxy
wget -e use_proxy=on \
     -e http_proxy=http://haproxy:8118 \
     -e https_proxy=http://haproxy:8118 \
     https://example.com
```

Internal hostnames (`searxng`, `llama`, `haproxy`, `playwright`) are in
`NO_PROXY` and reach directly — no proxy flags needed for them.

### Fetching a page: save to disk first, then decide

The default is **fetch to a file, not straight into context.** A web page can be
huge, off-topic, or garbage you didn't expect — and once it's in context it's
there for the rest of the session, costing tokens on every turn and dragging you
toward compaction. Writing to disk first is cheap insurance: if your processing
step has a bug, you fix the script and re-run it against the saved file instead
of re-fetching (slow, and through rotating Tor exits you may not even get the
same page back).

This applies to **every way you pull a full page**: the `searxng` URL-read tool,
the `playwright` text/navigate tools, and `curl`/`wget`. (The `searxng` *search*
tool is exempt — its results are already capped by `SEARXNG_MAX_RESULTS` /
`SEARXNG_MIN_SCORE`, so a search response is bounded and small. It's fetching the
*contents of a result URL* that can blow up your context.)

1. **Fetch to a file.** With curl/wget, redirect to disk:
   `curl -sL https://example.com/long -o /tmp/page.html`. The `searxng` URL-read
   and `playwright` tools hand their output back as a tool result rather than a
   file, so you don't get this for free — when their output is large, write it to
   a file first (Write tool / heredoc) before doing anything else with it. If you
   can foresee the page is big, prefer fetching the URL with `curl -o` in the
   first place so it lands on disk directly and never enters context.
2. **Check size and shape before reading** — `wc -c`, `wc -l`, `head`. Don't
   pull the whole thing into context yet.
3. **Then judge** (below) whether to load it or distill it on disk.

**Load straight into context only when** the payload is small (rule of thumb:
under ~1–2k tokens / a few hundred lines) *and* you already know it's relevant —
a short API JSON response, a focused doc page you requested by exact URL. Just
read it; disk round-tripping tiny payloads is needless ceremony.

**Distill on disk when** the payload is large, *or* you're not yet sure it's
relevant, *or* it's likely mostly boilerplate (nav, ads, markup). Extract only
what you need with deterministic tools and read *that* into context:

- `grep`/`rg` for the lines that matter, `jq` for JSON fields, `python -c` or a
  small script for structured extraction, `sed`/`pup`/`html2text` to strip HTML
  down to prose.
- Read the filtered result, never the raw file.

The discipline is: **deterministic, re-runnable processing on disk; only the
distilled result crosses into context.** That buys you the thing people usually
reach for a summarizer sub-agent to get — junk kept out of context — without
paying the sub-agent's cost on this stack (next section).

---

## Sub-agents and the KV cache (stack-specific)

This Claude runs against a **single-slot** llama.cpp server (`--parallel 1`), and
speed depends on its **prompt-prefix KV-cache reuse**: each turn reuses the prior
conversation as a cached prefix and prefills only the new tokens.

A sub-agent (Task tool) is a *different* conversation that shares no prefix —
dispatching one overwrites the main session's cached KV, and returning forces the
whole main context to re-prefill from scratch (very slow at long context). Adding
slots doesn't help: `--parallel N` splits the context N ways and 100k is the
compaction floor. So **don't reach for sub-agents.** Whatever you'd delegate to a
summarizer/extractor sub-agent, do inline with scripts over a saved file (above)
— same context savings, no eviction. A sub-agent earns the re-prefill only for a
single large task that genuinely needs model judgment you can't script.

This does **not** restrict the "run queries in parallel" advice below: batching
multiple search/fetch tool calls in one turn is fine — those are network calls to
SearXNG/Playwright, not model inference, so they never touch the KV cache. Only
parallel *sub-agents* are the problem.

---

## Research Execution Strategy

### Step 1 — Decompose into orthogonal angles

Never search the topic once. Break it into independent dimensions so each
query targets a different facet. Overlap across results means wasted queries.

**For a person or public figure:**
- `[name] [domain] style analysis`
- `[name] biography career history`
- `[name] [signature trait] technique`
- `[name] themes topics material`
- `[name] interview influences approach`

**For a topic, technology, or concept:**
- Overview / definition / current state
- History / origin / evolution
- Key figures or competing approaches
- Criticism, limitations, tradeoffs
- Recent developments (include the current year in this query)

**For a company or product:**
- Current status and offerings
- Recent news or announcements
- Competitive positioning
- Leadership / team (if relevant)

### Step 2 — Run in parallel

Batch independent queries together in a single step — never run them
sequentially when they don't depend on each other. This matters doubly here:
MCP tools have 1–5 s latency per call, so parallel execution is both faster
and produces broader coverage before you decide where to drill.

### Step 3 — Identify the load-bearing trait

When one dimension keeps surfacing across multiple sources, it is the defining
trait. Give it a dedicated follow-up query rather than treating it as a list
item. Lead with it in the final output.

### Step 4 — Gap-fill (one final query if needed)

After two parallel rounds, run one targeted query to fill whatever dimension
is still underrepresented — typically process, craft, technical depth, or
recent timeline.

---

## SearXNG — category selection

Pass the right `categories` value to the search tool to hit the most relevant
engines:

| categories value | Engines hit                                                  |
|------------------|--------------------------------------------------------------|
| `general`        | Brave, Mojeek, Fynd, Marginalia, Stract, YaCy, Wikidata …   |
| `science`        | arXiv, Semantic Scholar, PubMed, CrossRef, OpenAlex, BASE … |
| `it`             | GitHub, Stack Overflow, MDN, PyPI, npm, pkg.go.dev …         |
| `news`           | Reuters, BBC, AP, Guardian, Tagesschau (DE), Al Jazeera …    |
| `social media`   | Reddit, HN (via Crowdview), Lemmy …                          |
| `images`         | Unsplash, Wikimedia Commons, Flickr, Bing Images …           |
| `videos`         | YouTube, Dailymotion, Vimeo …                                |
| `files`          | Anna's Archive, Internet Archive, 1337x …                    |
| `onions`         | Ahmia, Torch, Haystack (dark-web only)                       |

Default to `general` unless the query clearly belongs to another category.
Multiple categories can be passed as a comma-separated string.

## SearXNG — query construction

SearXNG fans a single query out to 10–50 engines simultaneously. Fewer,
well-targeted queries return more signal than many vague ones:

- **Strip filler words.** Write `python asyncio connection timeout` not
  "how do I set a timeout in Python asyncio".
- **For code**: include language and library name. Add error message
  verbatim (quoted) when debugging.
- **For academic**: include author surnames or arXiv/DOI identifiers when
  known; add `2023..2025` for recency if it matters.
- **For news**: use bare keywords + location/org name; skip verbs.
- **Prefer one broad query + one narrow follow-up** over three guesses in
  sequence.
- Always include the current year in queries about versions, docs, or current
  state — stale results produce confidently wrong answers.

## SearXNG — language enforcement

**Always pass `language:en` to the SearXNG search tool.** This is non-negotiable.
You will never present untranslated Chinese, Korean, Russian, or any other
non-English result as a source unless the user explicitly asks for coverage in
that language.

If you see results from these domains — sohu.com, toutiao.com, baidu.com, qq.com,
chinadaily.com.cn, bilibili.com — treat them as contamination and re-search
with `language:en`. Do NOT fold concepts from sources you cannot read into your
output.

## SearXNG — Tor-hostile engines (expect failures)

These engines block Tor exit nodes aggressively — SearXNG will suspend them
after failures, which is expected and fine:

Google, Bing, DuckDuckGo, Yahoo, Baidu, Naver, Quark, Yandex (variable)

The ~160 Tor-friendly engines (indie crawlers, Wikimedia family, academic
databases, most news sources) provide complete coverage for normal research
tasks.

## When to use `playwright` instead of `searxng` URL-read

Use the `playwright` MCP when:
- The target is a JavaScript SPA (URL-read returns empty body or a loading
  spinner).
- The page requires login interaction or multi-step navigation.
- You need to click, fill a form, or interact with dynamic UI elements.

For static or server-rendered pages, prefer the `searxng` URL-read tool —
it is faster and lighter.

---

## Synthesis

Output must draw conclusions from patterns across sources — not summarize
each source in turn.

- If multiple sources confirm the same thing: assert it confidently
- If sources conflict: note the conflict explicitly and state what is more
  credible and why
- Never hedge with "according to source X" for facts that are broadly
  confirmed
- Structure output around **insights** (what it means, why it matters, how
  pieces connect), not around **retrieval** (source A said this, source B
  said that)

## Source quality gate

Before presenting any research output:
- All sources must be in English unless the user explicitly asked otherwise
- No results from non-English engines (Baidu, Quark, ChinaSo, Naver, Sogou,
  AcFun, Bilibili, iQiyi) — these are contamination even if they occasionally
  slip through Tor
- If a claim rests on a single source, flag it as unverified — do NOT present
  it as established fact

---

## Source attribution

Always list sources at the end of any research output as markdown hyperlinks.
This is required regardless of which model or provider is running the task.
