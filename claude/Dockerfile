# Pinned by digest to the ubuntu:24.04 build this image was last built on.
FROM ubuntu:24.04@sha256:cd1dba651b3080c3686ecf4e3c4220f026b521fb76978881737d24f200828b2b

# Node 22 from NodeSource. The apt `nodejs` on Ubuntu 24.04 is Node 18,
# which is too old: mcp-searxng's undici dep references `File` as a
# global (added in Node 20), and crashes at module load on 18 — surfacing
# to Claude Code as MCP error -32000.
# NodeSource setup_22.x tracks the latest 22.x; the build verified here was
# Node 22.22.2. (NodeSource prunes old patch releases, so the apt package isn't
# patch-pinned; the major line is what matters for the `File` global above.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        sudo curl git ca-certificates net-tools dnsutils \
        python3 \
        jq \
        bc \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# mcp-searxng — single MCP for both search and URL fetching. The URL-read
# tool fetches arbitrary user-requested URLs and respects URL_READER_*
# proxies, so we point those at haproxy (→ Tor). Search talks to internal
# SearXNG directly with no proxy. Pinned: this is the tool whose schema feeds
# the tool-call parser (see the tool-calling notes in BENCHMARKS.md and the
# .env block comments).
RUN npm install -g mcp-searxng@1.0.5

# Cut search-result noise: with many engines enabled a query returns ~100+
# results and mcp-searxng dumps them all to the LLM. This patch makes it honour
# SEARXNG_MIN_SCORE (relevance threshold) and SEARXNG_MAX_RESULTS (top-N cap),
# set on the mcp registration below. Runs as root before USER switch. See
# patch-searxng.js for the rationale and the safe-fallback behaviour.
COPY patch-searxng.js /tmp/patch-searxng.js
RUN node /tmp/patch-searxng.js

# Entrypoint maintains required keys in ~/.claude/settings.json and
# force-installs the default CLAUDE.md on every container start.
COPY entrypoint.sh           /usr/local/bin/claude-entrypoint.sh
COPY CLAUDE.md               /etc/lair/CLAUDE.md
COPY statusline-command.sh   /etc/lair/statusline-command.sh
RUN chmod +x /usr/local/bin/claude-entrypoint.sh

# Lower-privileged user with a sudo escape hatch (matches existing setup)
RUN usermod -aG sudo ubuntu \
    && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
USER ubuntu

# Pinned to Claude Code 2.1.158 (the installer takes a version as a positional
# arg; with no arg it always installs latest). Bump deliberately.
RUN curl -fsSL https://claude.ai/install.sh | bash -s -- 2.1.158
ENV PATH="/home/ubuntu/.local/bin:$PATH"

RUN mkdir -p /home/ubuntu/claude
WORKDIR /home/ubuntu/claude

# Register mcp-searxng at user scope. URL_READER_* points the URL-fetch
# tool's HTTP client at haproxy (→ Tor pool); the search path stays direct
# to internal SearXNG (no proxy env vars that would mis-route it).
# The noise-filter knobs (SEARXNG_MAX_RESULTS / SEARXNG_MIN_SCORE, read by
# patch-searxng.js) are injected into this registration at startup by the
# entrypoint, from the container env (set in .env) — so they're tunable with a
# restart, no rebuild. Seeded here so the env object exists.
RUN claude mcp add -s user searxng \
        -e SEARXNG_URL=http://searxng:8080 \
        -e SEARXNG_MAX_RESULTS=8 \
        -e SEARXNG_MIN_SCORE=0.3 \
        -e URL_READER_HTTP_PROXY=http://haproxy:8118 \
        -e URL_READER_HTTPS_PROXY=http://haproxy:8118 \
        -- mcp-searxng

# Playwright headless browser — streamable HTTP transport, connects to the
# playwright service on private_net. Lets Claude render JS-heavy pages via Tor.
RUN claude mcp add -s user playwright \
        --transport http \
        http://playwright:3000/mcp

# The container stays up as a long-lived sandbox; launch Claude Code on
# demand from the host:
#   docker compose exec -it claude claude --dangerously-skip-permissions
# The entrypoint refreshes ~/.claude/settings.json on every start so the
# required env keys are always present.
ENTRYPOINT ["/usr/local/bin/claude-entrypoint.sh"]
CMD ["sleep", "infinity"]
