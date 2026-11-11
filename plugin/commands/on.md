---
description: 打開 Apersona 開場包與代答
---

If the user asks to affect only this project, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_hook.py" --project-state on --project-directory "<current project directory>"` and report the result. This includes child directories; a parent opt-out still wins. Re-enabling does not upload older messages and does not grant capture consent. Otherwise use the global switch below.

Use the Write tool to write `${CLAUDE_PLUGIN_DATA}/apersona.json` as `{"on": true}`, and `${CLAUDE_PLUGIN_DATA}/apersona/stand_in.json` as `{}` (代答 starts counting again; this is also how he turns 代答 back on after it went off by itself).
Then tell the user in two lines: Apersona 已開（裝好本來就是開的），從下一個對話起開場會帶你的代答授權與長期規矩。AI 做完一段、清單還有下一項、沒在問你、也沒碰到重大的事（刪除、推送、發布、付款這類）時，人格會替你答「繼續做下一項」，對話裡會留一行「〈人格代答〉…」；判斷時 AI 最後那段話的結尾會送到 Apersona 網站（秘密先遮、不存），連續 5 次或被你推翻太多次就自己停；`/apersona:off` 關掉。
