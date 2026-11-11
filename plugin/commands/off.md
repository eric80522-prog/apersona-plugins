---
description: 關掉 Apersona 開場包與代答
---

If the user asks to affect only this project, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_hook.py" --project-state off --project-directory "<current project directory>"` and report the result. This includes child directories; a parent opt-out still wins. Re-enabling does not upload older messages and does not grant capture consent. Otherwise use the global switch below.

Use the Write tool to write `${CLAUDE_PLUGIN_DATA}/apersona.json` as `{"on": false}`.
Then tell the user in one line: Apersona 已關，開場不再帶任何東西，也不再代答；`/apersona:on` 再打開。
