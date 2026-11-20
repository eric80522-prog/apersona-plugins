---
description: 關掉 Apersona，或只停代答、保留開場包
---

If the user asks to stop only 代答 while keeping 開場包, run `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_hook.py" --stand-in off`. Relay its printed result and stop here. This preserves capture consent and the global switch. `/apersona:on 只開代答` resumes only that function.

Run `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_hook.py" --switch off`, adding `--project-directory "<current project directory>"` only if the user asks to affect only this project (this includes child directories; a parent opt-out still wins). The whole-machine switch also pauses 收錄 if it was on and tells the site, so the website's device page shows it off; what was already taken in is kept.

Tell the user the printed line as it is, then: 開場不再帶任何東西，也不再代答；`/apersona:on` 再打開。
