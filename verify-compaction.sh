#!/bin/bash
# Verify that Claude Code auto-compaction fires in the claude container.
#
# Instead of a hardcoded fixture, this queries the effective compaction window
# (CLAUDE_CODE_AUTO_COMPACT_WINDOW, derived from LLAMA_CTX_SIZE) and grows a
# conversation, chunk by chunk, until compaction is observed — so it adapts to
# whatever window the loaded model is configured for.
#
# Robustness notes:
#   - Context size is the SUM of the result usage fields (input + cache_creation +
#     cache_read). Raw input_tokens alone is confounded by prompt caching.
#   - Compaction is detected when that sum COLLAPSES between turns (history replaced
#     by a summary), or when a `compact` system event appears in the stream.
#   - We never depend on the model replying cleanly; only the usage accounting.
#   - Claude Code disables compaction below a 100k window, so that's the floor.
#     Testing at the full window (e.g. 200k) means prefilling ~that many tokens,
#     which takes minutes — set COMPACT_TEST_WINDOW=100000 for a faster check.
#
# Usage:
#   ./verify-compaction.sh                              # test at the configured window
#   COMPACT_TEST_WINDOW=100000 ./verify-compaction.sh   # faster, fixed test window
#   ./verify-compaction.sh --debug                      # verbose per-turn accounting
#
# Exit codes:
#   0  compaction fired
#   1  compaction did NOT fire before the context filled
#   2  setup error (container not running, window < 100k, etc.)

set -uo pipefail

DEBUG=0
[[ "${1:-}" == "--debug" ]] && DEBUG=1

SERVICE=claude                  # compose service name (for `docker compose exec`)
CONTAINER=lair-claude   # container name (for `docker compose ps`)
CHUNK_WORDS=18000               # filler words added per turn (~22k tokens)

# ---- preflight -------------------------------------------------------
if ! docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null \
     | grep -qx "$CONTAINER running"; then
    echo "ERROR: $CONTAINER is not running. Run: docker compose up -d claude" >&2
    exit 2
fi

# ---- resolve the test window -----------------------------------------
REAL_WINDOW=$(docker compose exec -T "$SERVICE" \
    bash -c 'echo ${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-0}' 2>/dev/null | tr -d '\r')
TEST_WINDOW=${COMPACT_TEST_WINDOW:-$REAL_WINDOW}
case "$TEST_WINDOW" in ''|*[!0-9]*) TEST_WINDOW=0 ;; esac

if [ "$TEST_WINDOW" -lt 100000 ]; then
    echo "ERROR: test window ${TEST_WINDOW} < 100000 — Claude Code disables compaction below 100k." >&2
    echo "       Set CLAUDE_CODE_AUTO_COMPACT_WINDOW >= 100000 (or COMPACT_TEST_WINDOW) and retry." >&2
    exit 2
fi
# Hard cap so we never try to exceed the model's real context window.
MAXCTX=$REAL_WINDOW
[ "$MAXCTX" -lt "$TEST_WINDOW" ] && MAXCTX=$TEST_WINDOW

echo "configured window: ${REAL_WINDOW} | testing at: ${TEST_WINDOW} | chunk: ${CHUNK_WORDS} words/turn"

# ---- one conversation turn -------------------------------------------
# Args: <continue:0|1> <filler-word-count>
# Generates a filler message in-container, sends it with the pinned test window,
# and prints "<context_sum> <compacted:yes|no>".
turn() {
    local cont="$1" words="$2"
    local flags="--dangerously-skip-permissions --print --verbose --output-format stream-json"
    [ "$cont" = "1" ] && flags="$flags --continue"
    timeout 600 docker compose exec -T "$SERVICE" bash -c "
        export CLAUDE_CODE_AUTO_COMPACT_WINDOW=$TEST_WINDOW
        python3 - <<'PYG' > /tmp/cc_chunk.txt
import random
random.seed()
vocab=('the of and to in is that for it as was with be by on not this are or from at which '
       'but have an they one all there when what your about would there here time data file').split()
print('Acknowledge this text. Reply with only the word OK and nothing else.')
print(' '.join(random.choice(vocab) for _ in range($words)))
PYG
        cat /tmp/cc_chunk.txt | claude $flags
    " 2>/dev/null | python3 -c '
import sys, json
total=None; compacted="no"
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    try: d=json.loads(line)
    except Exception: continue
    if d.get("type")=="system" and "compact" in str(d.get("subtype","")).lower():
        compacted="yes"
    if d.get("type")=="result":
        u=d.get("usage",{}) or {}
        total=((u.get("input_tokens") or 0)
               +(u.get("cache_creation_input_tokens") or 0)
               +(u.get("cache_read_input_tokens") or 0))
print(total if total is not None else -1, compacted)'
}

# ---- baseline --------------------------------------------------------
echo ""
echo "=== baseline (empty session) ==="
read -r BASE _ < <(turn 0 5)
echo "  context: ${BASE} tokens (system + tools + CLAUDE.md)"
if [ "${BASE:-0}" -lt 0 ] 2>/dev/null || [ -z "${BASE:-}" ]; then
    echo "INCONCLUSIVE: could not read baseline context (claude turn produced no result)" >&2
    exit 2
fi

# ---- grow until compaction fires -------------------------------------
PREV=$BASE
FIRED=0
LASTSUM=$BASE
# enough iterations to fill the window with margin
MAXITERS=$(( MAXCTX / (CHUNK_WORDS) + 4 ))

echo ""
echo "=== growing conversation until compaction (sum collapses) ==="
for i in $(seq 1 "$MAXITERS"); do
    read -r SUM COMP < <(turn 1 "$CHUNK_WORDS")
    [ -z "${SUM:-}" ] && SUM=-1
    pct=$(( MAXCTX>0 ? SUM*100/MAXCTX : 0 ))
    echo "  turn $i: context=${SUM} tokens (~${pct}% of ${MAXCTX})  compacted=${COMP}"

    if [ "$COMP" = "yes" ]; then FIRED=1; LASTSUM=$SUM; break; fi
    # collapse: a turn's context fell to <50% of the previous, after real growth
    if [ "$SUM" -gt 0 ] && [ "$PREV" -gt 60000 ] && [ "$SUM" -lt $(( PREV/2 )) ]; then
        FIRED=1; LASTSUM=$SUM; break
    fi
    # safety: stop before exceeding the real context window
    if [ "$SUM" -gt $(( MAXCTX*90/100 )) ]; then LASTSUM=$SUM; break; fi
    [ "$SUM" -gt 0 ] && PREV=$SUM
done

# ---- verdict ---------------------------------------------------------
echo ""
if [ "$FIRED" = "1" ]; then
    echo "PASS: compaction fired — context collapsed to ${LASTSUM} from a peak of ${PREV} tokens"
    exit 0
else
    echo "FAIL: compaction did NOT fire — context reached ${LASTSUM} tokens (~$(( MAXCTX>0 ? LASTSUM*100/MAXCTX : 0 ))% of ${MAXCTX}) without collapsing"
    echo "      Check CLAUDE_CODE_AUTO_COMPACT_WINDOW (must be >= 100000)."
    exit 1
fi
