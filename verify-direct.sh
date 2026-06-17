#!/bin/bash
# Prove the direct-exit (no-Tor) routing works:
#   (1) the direct-proxy container is dual-homed and CAN reach the internet.
#   (2) haproxy loaded a routing map matching DIRECT_DOMAINS from .env.
#   (3) a domain that is NOT listed is routed through the Tor pool.
#   (4) a domain that IS listed (if any) is routed through the direct pool.
# Routing is read straight from haproxy's own access log (the %b backend field),
# so this asserts where each request actually went, not just that it succeeded.
set -uo pipefail

cd "$(dirname "$0")"

PROJECT="lair"
PASS=0
FAIL=0

assert() {
    local label="$1"
    local expected="$2"  # "ok" if the command should succeed; "fail" if it must fail
    shift 2
    local actual
    if "$@" >/dev/null 2>&1; then actual="ok"; else actual="fail"; fi
    if [[ "$actual" == "$expected" ]]; then
        echo "  PASS  $label  (got: $actual)"
        PASS=$((PASS+1))
    else
        echo "  FAIL  $label  (got: $actual, expected: $expected)"
        FAIL=$((FAIL+1))
    fi
}

# Fetch a host through haproxy, then report which backend haproxy logged for it.
# Echoes "direct_pool", "tor_http_pool", or nothing if the request never landed.
routed_backend() {
    local host="$1"
    docker compose exec -T claude \
        curl --max-time 45 -sfo /dev/null -x http://haproxy:8118 "https://${host}" >/dev/null 2>&1
    sleep 1
    docker compose logs --since 15s haproxy 2>/dev/null \
        | grep -F "CONNECT ${host}:443" | tail -1 \
        | grep -oE 'direct_pool|tor_http_pool' | head -1
}

assert_backend() {
    local label="$1" host="$2" expected="$3"
    local got; got="$(routed_backend "$host")"
    if [[ "$got" == "$expected" ]]; then
        echo "  PASS  $label  (got: $got)"
        PASS=$((PASS+1))
    else
        echo "  FAIL  $label  (got: ${got:-<none>}, expected: $expected)"
        FAIL=$((FAIL+1))
    fi
}

echo "=== Direct-exit container ==="
echo
echo "direct-proxy attached networks (should list BOTH private_net and external_net):"
docker inspect "${PROJECT}-direct-proxy" \
    --format '{{range $k,$_ := .NetworkSettings.Networks}}  - {{$k}}{{"\n"}}{{end}}' 2>/dev/null \
    || echo "  (container not found — is the stack up?)"
echo

assert "direct-proxy CAN reach internet directly (egress side)" "ok" \
    docker compose exec -T direct-proxy sh -c 'nc -z -w 5 1.1.1.1 443'

echo
echo "=== Routing map ==="
echo
# DIRECT_DOMAINS as configured in .env (comma/space list -> count of tokens).
DIRECT_DOMAINS="$(grep -E '^DIRECT_DOMAINS=' .env | head -1 | cut -d= -f2-)"
read -ra DIRECT_LIST <<< "${DIRECT_DOMAINS//,/ }"
WANT=${#DIRECT_LIST[@]}
GOT="$(docker compose exec -T haproxy sh -c 'wc -l < /tmp/direct-domains.map' 2>/dev/null | tr -d '[:space:]')"
echo "  .env lists ${WANT} domain(s); haproxy map has ${GOT:-?} entry(ies)"
if [[ "${GOT:-x}" == "$WANT" ]]; then
    echo "  PASS  haproxy map matches DIRECT_DOMAINS"
    PASS=$((PASS+1))
else
    echo "  FAIL  haproxy map matches DIRECT_DOMAINS"
    FAIL=$((FAIL+1))
fi

echo
echo "=== Routing decisions (read from haproxy's backend log) ==="
echo
# example.com is the unlisted sentinel — must always go through Tor. (If someone
# actually put example.com in DIRECT_DOMAINS, skip rather than report a false fail.)
if [[ " ${DIRECT_LIST[*]} " == *" example.com "* ]]; then
    echo "  SKIP  example.com is in DIRECT_DOMAINS; no unlisted sentinel to test"
else
    assert_backend "unlisted domain (example.com) -> Tor pool" "example.com" "tor_http_pool"
fi

if [[ $WANT -gt 0 ]]; then
    assert_backend "listed domain (${DIRECT_LIST[0]}) -> direct pool" "${DIRECT_LIST[0]}" "direct_pool"
else
    echo "  SKIP  DIRECT_DOMAINS is empty; nothing to route direct"
fi

echo
echo "=== Summary: ${PASS} pass, ${FAIL} fail ==="
[[ $FAIL -eq 0 ]]
