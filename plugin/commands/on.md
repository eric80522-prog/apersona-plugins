---
description: 打開 Apersona 開場包與每輪記憶
---

If the user asks to affect only this project, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_hook.py" --project-state on --project-directory "<current project directory>"` and report the result. This includes child directories; a parent opt-out still wins. Re-enabling does not upload older messages and does not grant capture consent. Otherwise use the global switch below.

Use the Write tool to write `${CLAUDE_PLUGIN_DATA}/apersona.json` as `{"on": true}`.
Then tell the user in one line: Apersona 已開（裝好本來就是開的），從下一個對話起開場會帶本人規矩、記憶目錄與判讀摘要，每打一句有相關記憶時會提示可叫 recall；`/apersona:off` 關掉。
