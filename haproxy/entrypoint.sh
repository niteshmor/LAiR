#!/bin/sh
# Expand DIRECT_DOMAINS from .env into a HAProxy map file, then start HAProxy.
#
# The map drives the per-request routing in haproxy.cfg: a destination host that
# matches an entry goes to the direct-exit pool (straight to the internet), and
# everything else falls through to the Tor pool. Domains match with their
# subdomains (map_dom), so "anthropic.com" also covers "api.anthropic.com".
#
# DIRECT_DOMAINS is a space- and/or comma-separated list, e.g.
#   DIRECT_DOMAINS="anthropic.com, github.com"
# Empty/unset means the map is empty and every request goes to Tor — i.e. the
# feature is opt-in and the default behaviour is unchanged.
#
# We template here (rather than ship a static map) so the list lives in .env
# alongside everything else and a change is just `docker compose up -d haproxy`,
# no rebuild — the same pattern searxng/entrypoint.sh uses for its secret.
set -u

MAP=/tmp/direct-domains.map
: > "$MAP"

# Split on commas and whitespace; one "<domain> direct_pool" line per entry.
for domain in $(printf '%s' "${DIRECT_DOMAINS:-}" | tr ',' ' '); do
    [ -z "$domain" ] && continue
    printf '%s direct_pool\n' "$domain" >> "$MAP"
done

echo "haproxy: $(wc -l < "$MAP") domain(s) routed direct (no Tor); rest via Tor pool"

exec haproxy -f /usr/local/etc/haproxy/haproxy.cfg "$@"
