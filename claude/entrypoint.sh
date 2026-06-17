#!/bin/bash
# Maintains the user-level Claude Code defaults inside the container:
#   - Required env keys in ~/.claude/settings.json (Claude Code-specific
#     vars that aren't picked up from the shell environment).
#   - ~/.claude/CLAUDE.md from the image-baked default (force-overwritten;
#     project-specific instructions belong in the workspace's CLAUDE.md).
# Idempotent — runs on every container start.
set -e

mkdir -p "$HOME/.claude"

# --- APT proxy: route apt-get through haproxy → Tor at runtime ---
# (build-time apt runs before the proxy network exists; this only matters
#  when the user runs apt-get inside the live container)
sudo tee /etc/apt/apt.conf.d/01proxy > /dev/null <<'EOF'
Acquire::http::Proxy "http://haproxy:8118";
Acquire::https::Proxy "http://haproxy:8118";
EOF

# --- settings.json: force the required env keys, preserve other keys ---
SETTINGS="$HOME/.claude/settings.json"
[[ -f "$SETTINGS" ]] || echo '{}' > "$SETTINGS"

REQUIRED_ENV='{
  "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
  "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
  "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"
}'

STATUSLINE='{"type":"command","command":"bash ~/.claude/statusline-command.sh","refreshInterval":60}'

tmp="$(mktemp)"
jq --argjson req "$REQUIRED_ENV" \
   --argjson sl "$STATUSLINE" \
   '.env = ((.env // {}) + $req)
    | .statusLine = $sl
    | .theme = "light"
    | .skipDangerousModePermissionPrompt = true' \
   "$SETTINGS" > "$tmp"
mv "$tmp" "$SETTINGS"

# --- CLAUDE.md: force-overwrite with the baked-in default ---
cp /etc/lair/CLAUDE.md "$HOME/.claude/CLAUDE.md"

# --- Statusline script: deploy from image into the claude-state volume ---
cp /etc/lair/statusline-command.sh "$HOME/.claude/statusline-command.sh"
chmod +x "$HOME/.claude/statusline-command.sh"

# --- ~/.claude.json: onboarding skip + SearXNG noise-filter knobs ---
# hasCompletedOnboarding / hasTrustDialogAccepted suppress the first-run
# wizard and the per-project trust dialog. lastOnboardingVersion and
# lastReleaseNotesSeen are version-matched to suppress the release-notes
# popup; both are derived from the installed binary so they stay correct
# when the version pin is bumped.
CJSON="$HOME/.claude.json"
if [[ -f "$CJSON" ]]; then
    CLAUDE_VER=$(claude --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    tmp="$(mktemp)"
    jq --arg max "${SEARXNG_MAX_RESULTS:-8}" \
       --arg min "${SEARXNG_MIN_SCORE:-0.3}" \
       --arg ver "${CLAUDE_VER:-}" \
       '.mcpServers.searxng.env.SEARXNG_MAX_RESULTS = $max
        | .mcpServers.searxng.env.SEARXNG_MIN_SCORE = $min
        | .hasCompletedOnboarding = true
        | (if $ver != "" then .lastOnboardingVersion = $ver | .lastReleaseNotesSeen = $ver else . end)
        | .projects["/home/ubuntu/claude"].hasTrustDialogAccepted = true
        | .projects["/home/ubuntu/claude"].projectOnboardingSeenCount = (.projects["/home/ubuntu/claude"].projectOnboardingSeenCount // 1)' \
       "$CJSON" > "$tmp" && mv "$tmp" "$CJSON"
fi

exec "$@"
