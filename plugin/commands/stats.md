---
description: 看本人在 Claude Code 打了多少字：每 100 則 AI 回覆打幾則、有幾則是在別段對話講過的、代答被推翻幾次
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_stats.py" --days <N> --plugin-data "${CLAUDE_PLUGIN_DATA}"` (N is `$ARGUMENTS`; 30 when he gave none) and show the table exactly as printed, then its last line as the one-sentence answer. It reads only the conversations Claude Code keeps on this machine, prints counts and no text, and sends nothing anywhere. Only the system's `python3` is needed.

When he asks to send his Claude Code or Codex conversations to Apersona (to read his repeated sayings into long-standing rules), that is `/apersona:read` with no folder: it lists them and asks him once.

For the dev website’s typing rate, he can paste the printed daily table into its local calculator. No transcript or project name belongs in that table.
