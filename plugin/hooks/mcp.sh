#!/usr/bin/env bash
# MCP stdio must contain JSON only. Credentials are loaded by the Python client.
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
  printf '%s\n' 'APersona needs Python 3.9 or newer.' >&2
  exit 1
fi
client="$(cd "$(dirname "$0")/.." && pwd)/scripts/apersona_mcp.py"
exec "$py" "$client"
