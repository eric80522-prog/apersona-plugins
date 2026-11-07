#!/usr/bin/env bash
# `hook.sh opening` at SessionStart (開場包), `hook.sh turn` at UserPromptSubmit (每輪記憶, the hook's JSON on stdin).
# `hook.sh stop` at Stop (收錄, off until he consents at his own terminal).
# Never block a prompt, including when logging or the interpreter fails.
exec 2>/dev/null

plugin_data="${CLAUDE_PLUGIN_DATA:-}"
case "${1:-opening}" in
  turn) kind=turn ;;
  stop) kind=stop ;;
  *) kind=opening; exec </dev/null ;;
esac
# On from install; only an explicit /apersona:off turns it off.
if [ -z "$plugin_data" ] || grep -Eq '"on"[[:space:]]*:[[:space:]]*false' "$plugin_data/apersona.json"; then
  exit 0
fi

started_ms="$(date +%s%3N)"
# BSD date (macOS) has no %3N; whole seconds would take up to 1 s off the 3-second budget, so ask perl first.
case "$started_ms" in
  ''|*[!0-9]*) started_ms="$(perl -MTime::HiRes=time -e 'printf "%d", time() * 1000')" ;;
esac
case "$started_ms" in
  ''|*[!0-9]*) started_ms=$(( $(date +%s) * 1000 )) ;;
esac
event_id="ev-${started_ms}-$$-${RANDOM}"
log_dir="$plugin_data/apersona"
log="$log_dir/events.jsonl"
mkdir -p "$log_dir" || exit 0
printf '{"event_id":"%s","kind":"%s","phase":"start","ts_ms":%s}\n' \
  "$event_id" "$kind" "$started_ms" >> "$log" || exit 0

# Any python3 will do: the hook client is standard library only. A Microsoft Store alias (WindowsApps) may be an
# install stub, and `timeout` kills only the alias while the real interpreter keeps the pipe open; use it only when
# nothing else exists.
py=
store_py=
for candidate in python3 python; do
  found="$(command -v "$candidate")" || continue
  case "$found" in
    */[Ww]indows[Aa]pps/*) [ -n "$store_py" ] || store_py="$found" ;;
    *) py="$found"; break ;;
  esac
done
[ -n "$py" ] || py="$store_py"
if [ -z "$py" ]; then
  printf '{"event_id":"%s","kind":"%s","phase":"end","result":"miss","error":"interpreter_missing"}\n' \
    "$event_id" "$kind" >> "$log"
  exit 0
fi

client="$(cd "$(dirname "$0")/.." && pwd)/scripts/apersona_hook.py"
if command -v timeout >/dev/null 2>&1; then
  output="$(timeout 3 "$py" "$client" --kind "$kind" --event-id "$event_id" --started-ms "$started_ms")"
  status=$?
else
  output="$("$py" "$client" --kind "$kind" --event-id "$event_id" --started-ms "$started_ms")"
  status=$?
fi

if [ "$status" -eq 0 ]; then
  [ -z "$output" ] || printf '%s\n' "$output"
else
  if ! grep -F "\"event_id\":\"$event_id\"" "$log" | grep -Fq '"phase":"end"'; then
    if [ "$status" -eq 124 ]; then error=timeout; else error="exit_$status"; fi
    printf '{"event_id":"%s","kind":"%s","phase":"end","result":"miss","error":"%s"}\n' \
      "$event_id" "$kind" "$error" >> "$log"
  fi
fi
exit 0
