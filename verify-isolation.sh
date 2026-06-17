#!/bin/bash
# Prove the network-isolation boundary:
#   (1) claude has no path to the internet without going through Tor.
#   (2) claude reaches the internet when it does go through haproxy/Tor.
#   (3) tor-proxy-1 is dual-homed and CAN reach the internet directly
#       (that's how it carries Tor circuits out).
set -uo pipefail

cd "$(dirname "$0")"

# Project name is set via `name: lair` in docker-compose.yml.
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

echo "=== Docker network configuration ==="
echo
echo "private_net (should show Internal: true):"
docker network inspect "${PROJECT}_private_net" \
    --format '  Internal: {{.Internal}}   Name: {{.Name}}' 2>/dev/null \
    || echo "  (network not found — is the stack up?)"
echo
echo "tor-proxy-1 attached networks (should list BOTH private_net and external_net):"
docker inspect "${PROJECT}-tor-proxy-1" \
    --format '{{range $k,$_ := .NetworkSettings.Networks}}  - {{$k}}{{"\n"}}{{end}}' 2>/dev/null \
    || echo "  (container not found — is the stack up?)"
echo
echo "claude attached networks (should list ONLY private_net):"
docker inspect "${PROJECT}-claude" \
    --format '{{range $k,$_ := .NetworkSettings.Networks}}  - {{$k}}{{"\n"}}{{end}}' 2>/dev/null \
    || echo "  (container not found — is the stack up?)"

echo
echo "=== Functional checks ==="
echo
# Belt-and-suspenders: unset every proxy env var AND tell curl to ignore
# whatever's left, so this check is actually unmediated. Without this,
# `curl` silently honors HTTPS_PROXY (which we set on the claude container
# for the fetch MCP) and reports a false "direct egress works" result.
assert "claude CANNOT reach internet directly" "fail" \
    docker compose exec -T claude \
        env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u NO_PROXY -u no_proxy \
        curl --noproxy '*' --max-time 5 -sfo /dev/null https://example.com

assert "claude CAN reach internet via haproxy/Tor" "ok" \
    docker compose exec -T claude curl --max-time 45 -sfo /dev/null \
        -x http://haproxy:8118 https://example.com

assert "tor-proxy-1 CAN reach internet directly (egress side)" "ok" \
    docker compose exec -T tor-proxy-1 sh -c 'nc -z -w 5 1.1.1.1 443'

echo
echo "=== Summary: ${PASS} pass, ${FAIL} fail ==="
[[ $FAIL -eq 0 ]]
