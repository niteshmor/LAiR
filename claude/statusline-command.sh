#!/bin/bash
input=$(cat)

CACHE_DIR="$HOME/.claude/.statusline_cache"
mkdir -p "$CACHE_DIR"
echo "$input" > "$CACHE_DIR/last_input.json"

# Garbage-collect stale cache files older than 48h
find "$CACHE_DIR" -mmin +2880 -delete 2>/dev/null

# ── Color palette ─────────────────────────────────────────────────────────────
# Semantic colors used throughout:
#   green   = good / local / safe        \033[32m
#   yellow  = caution / mid              \033[33m
#   red     = danger / error / high-use  \033[31m
#   cyan    = anthropic / primary info   \033[36m
#   magenta = custom/unknown provider    \033[35m
#   blue    = git branch                 \033[34m
#   bold    = emphasis                   \033[1m
#   dim     = separator / secondary      \033[2m
#   reset                                \033[0m

# ── Helpers ───────────────────────────────────────────────────────────────────
fmt_k() {
  local n="$1"
  if [ -z "$n" ] || [ "$n" = "null" ] || [ "$n" = "0" ]; then echo "0"; return; fi
  if [ "$n" -ge 1000 ] 2>/dev/null; then
    printf "%.1fk" "$(echo "scale=1; $n / 1000" | bc)"
  else
    echo "$n"
  fi
}

fmt_duration() {
  local ms="$1"
  local s=$(( ms / 1000 ))
  local h=$(( s / 3600 ))
  local m=$(( (s % 3600) / 60 ))
  local sec=$(( s % 60 ))
  if [ "$h" -gt 0 ]; then printf '%d:%02d:%02d' "$h" "$m" "$sec"
  else                    printf '%d:%02d'        "$m" "$sec"
  fi
}

# ── 1. Working directory ──────────────────────────────────────────────────────
cwd=$(echo "$input" | jq -r '.cwd // empty')
project="PWD=${cwd/$HOME/\~}"

# ── 2. Provider + Model ────────────────────────────────────────────────────────
model=$(echo "$input" | jq -r '.model.display_name // empty')
model_id=$(echo "$input" | jq -r '.model.id // empty')
effort=$(echo "$input" | jq -r '.effort.level // empty')
fast=$(echo "$input" | jq -r '.fast_mode // false')
thinking=$(echo "$input" | jq -r '.thinking.enabled // false')

# Provider detection: env vars take precedence over model-ID guessing.
base_url="${ANTHROPIC_BASE_URL:-}"
oai_url="${OPENAI_BASE_URL:-}"
oai_key="${OPENAI_API_KEY:-}"

provider=""
provider_color=""

_is_local() { echo "$1" | grep -qE "localhost|127\.0\.0\.1|::1|0\.0\.0\.0"; }

if echo "$model_id" | grep -q "^claude-"; then
  if [ -n "$base_url" ] && ! echo "$base_url" | grep -qE "api\.anthropic\.com"; then
    if _is_local "$base_url"; then
      provider="local"
      provider_color="\033[32m"    # green — local is always "safe/cheap"
    else
      provider="custom"
      provider_color="\033[35m"    # magenta — unknown third-party relay
    fi
  else
    provider="anthropic"
    provider_color="\033[36m"      # cyan — Anthropic's own infra
  fi
elif [ -n "$oai_url" ] || [ -n "$oai_key" ]; then
  if _is_local "${oai_url:-}"; then
    provider="local"
    provider_color="\033[32m"
  elif echo "$model_id" | grep -qE "^gpt-|^o[0-9]"; then
    provider="openai"
    provider_color="\033[33m"      # yellow — other cloud
  elif echo "$model_id" | grep -q "^gemini"; then
    provider="google"
    provider_color="\033[33m"
  else
    # Extract first segment as a best-effort provider name
    provider=$(echo "$model_id" | cut -d- -f1 | cut -d/ -f1)
    provider_color="\033[33m"
  fi
else
  # No env vars set — guess from model ID alone
  if echo "$model_id" | grep -qE "^gpt-|^o[0-9]"; then
    provider="openai"
    provider_color="\033[33m"
  elif echo "$model_id" | grep -q "^gemini"; then
    provider="google"
    provider_color="\033[33m"
  elif echo "$model_id" | grep -qiE "llama|mistral|phi|qwen|deepseek|ollama"; then
    provider="local"
    provider_color="\033[32m"
  else
    provider="unknown"
    provider_color="\033[37m"
  fi
fi

# Build model string: "provider // Model Name [flags]"
sep="\033[2m//\033[0m"
model_str="${provider_color}${provider}\033[0m ${sep} \033[1m${model}\033[0m"
[ -n "$effort" ] && model_str="${model_str} (${effort^})"
[ "$fast" = "true" ] && model_str="${model_str} *"
[ "$thinking" = "true" ] && model_str="${model_str} T"

# ── 3. Prompt cache tokens (read / write) ─────────────────────────────────────
cache_read=$(echo "$input"  | jq -r '.context_window.current_usage.cache_read_input_tokens     // 0')
cache_write=$(echo "$input" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0')
cache_str="$(fmt_k "$cache_read")/$(fmt_k "$cache_write")"

# ── 4. KV-cache countdown timer (llama.cpp idle-sleep window) ─────────────────
# This Claude runs against a local llama.cpp server that unloads the model (and
# drops its prompt-prefix KV cache) after LLAMA_SLEEP_IDLE_SECONDS of inactivity.
# That idle window — not a fixed Anthropic prompt-cache TTL — is when the cached
# prefix goes cold and the next turn pays a full re-prefill. Default mirrors the
# compose default if the var isn't passed into the container.
CACHE_TTL_SECONDS="${LLAMA_SLEEP_IDLE_SECONDS:-1800}"
session_id=$(echo "$input" | jq -r '.session_id // empty')
cache_timer_str=""
if [ -n "$session_id" ]; then
  total_tokens=$(echo "$input" | jq -r \
    '((.context_window.total_input_tokens // 0) + (.context_window.total_output_tokens // 0))')

  token_file="$CACHE_DIR/tokens_${session_id}"
  timer_file="$CACHE_DIR/timer_${session_id}"

  prev_tokens=0
  [ -f "$token_file" ] && prev_tokens=$(cat "$token_file")
  echo "$total_tokens" > "$token_file"

  now=$(date +%s)

  if [ "$total_tokens" -gt "$prev_tokens" ] 2>/dev/null; then
    echo "$now" > "$timer_file"
  fi

  if [ -f "$timer_file" ]; then
    last_call=$(cat "$timer_file")
    elapsed=$(( now - last_call ))
    remaining_secs=$(( CACHE_TTL_SECONDS - elapsed ))
    if [ "$remaining_secs" -le 0 ]; then
      cache_timer_str="\033[31mExpired\033[0m"
    else
      mins=$(( (remaining_secs + 59) / 60 ))
      # Thresholds scale with the configured window: green in the top half,
      # yellow in the next fifth, red as it runs out.
      if   [ "$remaining_secs" -gt $(( CACHE_TTL_SECONDS / 2 )) ]; then timer_color="\033[32m"
      elif [ "$remaining_secs" -gt $(( CACHE_TTL_SECONDS / 5 )) ]; then timer_color="\033[33m"
      else                                                              timer_color="\033[31m"
      fi
      cache_timer_str="${timer_color}$(printf '%-3s' "${mins}m")\033[0m"
    fi
  fi
fi

# ── 5. Git branch + dirty + ahead/behind (cached 10 min per directory) ────────
git_str=""
if [ -n "$cwd" ] && git -C "$cwd" rev-parse --git-dir > /dev/null 2>&1; then
  git_key=$(echo "$cwd" | md5sum | cut -c1-8)
  git_cache="$CACHE_DIR/git_${git_key}"
  now_git=$(date +%s)

  should_refresh=1
  if [ -f "$git_cache" ]; then
    cache_age=$(( now_git - $(stat -c %Y "$git_cache") ))
    [ "$cache_age" -lt 600 ] && should_refresh=0
  fi

  if [ "$should_refresh" = "1" ]; then
    branch=$(git -C "$cwd" symbolic-ref --short HEAD 2>/dev/null \
             || git -C "$cwd" rev-parse --short HEAD 2>/dev/null)
    dirty=""
    if ! git -C "$cwd" diff --quiet 2>/dev/null \
    || ! git -C "$cwd" diff --cached --quiet 2>/dev/null; then
      dirty="\033[33m*\033[0m"
    fi
    ahead_count=$(git -C "$cwd" rev-list --count @{u}..HEAD 2>/dev/null || echo "0")
    behind_count=$(git -C "$cwd" rev-list --count HEAD..@{u} 2>/dev/null || echo "0")

    indicators=""
    [ "$ahead_count" -gt 0 ] 2>/dev/null && indicators="${indicators}\033[32m^${ahead_count}\033[0m"
    [ "$behind_count" -gt 0 ] 2>/dev/null && indicators="${indicators}\033[31mv${behind_count}\033[0m"

    echo "\033[34m${branch}\033[0m${dirty}${indicators}" > "$git_cache"
  fi

  git_str=$(cat "$git_cache")
fi

# ── 6. Context remaining % (color-coded) ──────────────────────────────────────
remaining=$(echo "$input" | jq -r '.context_window.remaining_percentage // empty')
ctx_str=""
if [ -n "$remaining" ]; then
  pct=$(printf '%.0f' "$remaining")
  if   [ "$pct" -gt 50 ]; then color="\033[32m"
  elif [ "$pct" -gt 20 ]; then color="\033[33m"
  else                          color="\033[31m"
  fi
  ctx_str="${color}${pct}% Remaining\033[0m"
fi

# ── 7. Session duration / API time ───────────────────────────────────────────
duration_ms=$(echo "$input"     | jq -r '.cost.total_duration_ms     // 0')
api_duration_ms=$(echo "$input" | jq -r '.cost.total_api_duration_ms // 0')
duration_str=""
[ "$duration_ms" -gt 0 ]     2>/dev/null && duration_str="$(fmt_duration "$duration_ms")"
api_str=""
[ "$api_duration_ms" -gt 0 ] 2>/dev/null && api_str="API $(fmt_duration "$api_duration_ms")"

# ── 8. Cumulative tokens (total input / output) ───────────────────────────────
total_in=$(echo "$input"  | jq -r '.context_window.total_input_tokens  // 0')
total_out=$(echo "$input" | jq -r '.context_window.total_output_tokens // 0')
total_tokens_str=""
if [ "$total_in" -gt 0 ] 2>/dev/null || [ "$total_out" -gt 0 ] 2>/dev/null; then
  total_tokens_str="$(fmt_k "$total_in")/$(fmt_k "$total_out")"
fi

# ── Assemble: fixed 3-line layout ─────────────────────────────────────────────
# No rate-limit / cost line: those are Anthropic-subscription concepts (5h/7d
# quotas, per-token billing) that don't apply to a local llama.cpp backend.
line1=(); line2=(); line3=()
[ -n "$project" ]          && line1+=("${project}")
[ -n "$git_str" ]          && line1+=("${git_str}")
[ -n "$model_str" ]        && line1+=("${model_str}")

[ -n "$ctx_str" ]          && line2+=("$ctx_str")
[ -n "$duration_str" ]     && line2+=("Wall ${duration_str}")
[ -n "$api_str" ]          && line2+=("${api_str}")

[ -n "$cache_timer_str" ]  && line3+=("TTL ${cache_timer_str}")
[ "$cache_str" != "0/0" ]  && line3+=("Cache r/w ${cache_str}")
[ -n "$total_tokens_str" ] && line3+=("Tokens in/out ${total_tokens_str}")

print_line() {
  [ "$#" -eq 0 ] && return
  local first=1
  for part in "$@"; do
    [ "$first" = "1" ] && first=0 || printf '%b' ' | '
    printf '%b' "$part"
  done
  printf '\n'
}

print_labeled_line() {
  local label="$1"; shift
  [ "$#" -eq 0 ] && return
  printf '%-6s : ' "$label"
  local first=1
  for part in "$@"; do
    [ "$first" = "1" ] && first=0 || printf '%b' ' | '
    printf '%b' "$part"
  done
  printf '\n'
}

print_line          "${line1[@]}"
print_labeled_line "Ctx"    "${line2[@]}"
print_labeled_line "Cache"  "${line3[@]}"
