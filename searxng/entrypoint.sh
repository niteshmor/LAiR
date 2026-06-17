#!/bin/sh
# Wrapper around SearXNG's stock entrypoint that guarantees a real secret_key.
#
# SearXNG reads server.secret_key from the SEARXNG_SECRET env var
# (searx/settings_defaults.py: SettingsValue(str, environ_name='SEARXNG_SECRET')),
# which overrides the "ultrasecretkey" placeholder shipped in settings.yml. The
# stock image only auto-generates a key when it *creates* the settings file; we
# mount our own read-only, so it never does — hence this wrapper.
#
#   - SEARXNG_SECRET set in .env  -> passed straight through (stable, explicit).
#   - SEARXNG_SECRET blank/unset  -> mint a random one per start, so the instance
#                                    never runs on the public placeholder value.
#
# The key only signs preference cookies for this localhost-only instance, so a
# fresh value on each start is harmless. Set SEARXNG_SECRET in .env if you want
# it stable across restarts.
set -u

if [ -z "${SEARXNG_SECRET:-}" ]; then
    SEARXNG_SECRET="$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9')"
    export SEARXNG_SECRET
fi

exec /usr/local/searxng/entrypoint.sh "$@"
