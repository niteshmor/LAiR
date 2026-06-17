#!/bin/bash
# verify-e2e.sh — end-to-end smoke test for LAiR.
#
# Proves the full agent toolchain works, in two layers:
#
#   Layer 1 — Infrastructure (deterministic, no model). Fast and reliable:
#     - llama.cpp server is healthy
#     - SearXNG search returns results
#     - a URL fetch routes through haproxy -> Tor (verified via the Tor
#       project's own IsTor endpoint)
#     - both MCP servers (searxng, playwright) report Connected
#
#   Layer 2 — Agent (the Claude Code CLI actually invoking each MCP tool):
#     - SearXNG search        (mcp__searxng__searxng_web_search)
#     - SearXNG URL-read      (mcp__searxng__web_url_read)
#     - Playwright navigation (mcp__playwright__browser_*)
#     Each agent check asserts the expected MCP tool was *called* — parsed
#     from the CLI's stream-json output — not merely that the model produced
#     a plausible answer. A local model will happily answer "torproject.org"
#     from memory without searching, so prompts force the tool with an
#     unknowable target and success is keyed on the tool-call event.
#
# Usage:
#   ./verify-e2e.sh               # full suite (infra + agent; agent calls are slow)
#   ./verify-e2e.sh --infra-only  # skip the slow model-driven agent checks
#
# Env:
#   AGENT_TIMEOUT  per-agent-check timeout in seconds (default 300)
set -uo pipefail
cd "$(dirname "$0")"

AGENT_TIMEOUT="${AGENT_TIMEOUT:-300}"
RUN_AGENT=1
[[ "${1:-}" == "--infra-only" ]] && RUN_AGENT=0

PASS=0
FAIL=0
pass() { echo "  PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }

# Run a command inside the claude container (non-interactive login shell).
dexec() { docker compose exec -T claude bash -lc "$1"; }

# -------------------------------------------------------------------------
echo "=== Layer 1: infrastructure (deterministic) ==="

# I1 — llama.cpp server health
if dexec 'curl -sf --max-time 10 http://llama:8080/health' 2>/dev/null | grep -q '"status":"ok"'; then
    pass "llama.cpp server healthy"
else
    fail "llama.cpp server healthy"
fi

# I2 — SearXNG returns search results
n=$(dexec 'curl -sf --max-time 20 "http://searxng:8080/search?q=tor%20project&format=json"' 2>/dev/null \
      | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("results",[])))' 2>/dev/null) || n=0
if [[ "${n:-0}" -gt 0 ]]; then
    pass "SearXNG search returns results (count: $n)"
else
    fail "SearXNG search returns results"
fi

# I3 — URL fetch routes through haproxy -> Tor (Tor's own IsTor check)
if dexec 'curl -x http://haproxy:8118 -sf --max-time 45 https://check.torproject.org/api/ip' 2>/dev/null \
     | grep -q '"IsTor":true'; then
    pass "URL fetch via haproxy -> Tor (IsTor: true)"
else
    fail "URL fetch via haproxy -> Tor"
fi

# I4 — both MCP servers connected
mcp="$(dexec 'claude mcp list 2>/dev/null')"
if grep -q 'searxng:.*Connected' <<<"$mcp" && grep -q 'playwright:.*Connected' <<<"$mcp"; then
    pass "MCP servers connected (searxng + playwright)"
else
    fail "MCP servers connected (searxng + playwright)"
fi

# -------------------------------------------------------------------------
# Agent layer.
#
# agent_check <label> <prompt> <tool-name-regex> <needle>
#   Runs the prompt through `claude -p` with stream-json, then asserts:
#     (1) a tool_use whose name matches <tool-name-regex> was emitted,
#     (2) no tool_result came back with is_error=true, and
#     (3) <needle> (case-insensitive, "" to skip) appears in the model's text.
agent_check() {
    local label="$1" prompt="$2" tool_re="$3" needle="$4"
    local out; out="$(mktemp)"
    timeout "$AGENT_TIMEOUT" docker compose exec -T claude bash -lc \
        "claude -p \"$prompt\" --output-format stream-json --verbose --dangerously-skip-permissions" \
        > "$out" 2>/dev/null
    # Parse the captured stream-json. Python prints a one-line tool summary
    # to stdout and exits 0 (checks passed) / 1 (failed); `tools=$(...)`
    # leaves that exit status in $? for the caller.
    local tools rc
    tools=$(python3 - "$out" "$tool_re" "$needle" <<'PY'
import sys, json, re
path, tool_re, needle = sys.argv[1], sys.argv[2], sys.argv[3]
tools, err, text = [], False, []
for line in open(path, encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line:
        continue
    try:
        o = json.loads(line)
    except ValueError:
        continue
    t = o.get("type")
    if t == "assistant":
        for c in o.get("message", {}).get("content", []):
            if c.get("type") == "tool_use":
                tools.append(c.get("name", ""))
            elif c.get("type") == "text":
                text.append(c.get("text", ""))
    elif t == "user":
        for c in o.get("message", {}).get("content", []):
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                err = True
    elif t == "result" and o.get("result"):
        text.append(str(o.get("result")))
called = any(re.search(tool_re, x) for x in tools)
needle_ok = (needle == "") or (needle.lower() in " ".join(text).lower())
sys.stdout.write("tools=[%s]" % ", ".join(t for t in tools if t))
sys.exit(0 if (called and not err and needle_ok) else 1)
PY
)
    rc=$?
    rm -f "$out"
    if [[ $rc -eq 0 ]]; then
        pass "$label   ($tools)"
    else
        fail "$label   ($tools)"
    fi
}

if [[ "$RUN_AGENT" -eq 1 ]]; then
    echo
    echo "=== Layer 2: agent (Claude Code CLI driving the MCP tools) ==="
    echo "    (each check runs a real model turn; this is slow)"

    agent_check "CLI -> SearXNG search" \
        "Use the searxng web search tool to find pages about the Tor Project, then tell me the exact title of the first search result. You must call the search tool; do not answer from memory." \
        "mcp__searxng__.*search" \
        ""

    agent_check "CLI -> SearXNG URL-read" \
        "Use the searxng URL reader tool to fetch the page at https://check.torproject.org/api/ip and report the exact JSON body it returns. You must fetch it with the tool." \
        "mcp__searxng__.*(url|read)" \
        "istor"

    agent_check "CLI -> Playwright navigate" \
        "Use the playwright browser tool to navigate to https://example.com and report the text of the page's main heading. You must use the browser tool." \
        "mcp__playwright__" \
        "example domain"
fi

# -------------------------------------------------------------------------
echo
echo "=== Summary: ${PASS} pass, ${FAIL} fail ==="
[[ $FAIL -eq 0 ]]
