---
description: 看本人在 Claude Code 打了多少字：每 100 則 AI 回覆打幾則、有幾則是在別段對話講過的、代答被推翻幾次
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_stats.py" --days <N> --plugin-data "${CLAUDE_PLUGIN_DATA}"` (N is `$ARGUMENTS`; 30 when he gave none) and show the table exactly as printed, then its last line as the one-sentence answer. It reads only the conversations Claude Code keeps on this machine, prints counts and no text, and sends nothing anywhere. Only the system's `python3` is needed.

When he asks to turn his Claude Code conversations into a 對話包 (to read his repeated sayings into long-standing rules), ask which folder, run the same script with `--export <folder>` added, and show the one line it prints. It writes one JSONL there (his typed messages and the replies he saw, secrets dropped) and sends nothing; `/apersona:read` on that folder is what sends it, after he says yes there.
