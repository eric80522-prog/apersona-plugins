---
description: 打開 Apersona 開場包與代答
---

Run `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_hook.py" --switch on`, adding `--project-directory "<current project directory>"` only if the user asks to affect only this project (this includes child directories; a parent opt-out still wins). Either way nothing he typed while it was off is sent later, and turning it on never grants capture consent. The whole-machine switch also turns 代答 back on after it went off by itself (the site keeps the counts and counts the overturn share afresh from when it went off), and, if 收錄 was on, tells the site it is on again.

Tell the user the printed line as it is, then (whole machine only): 從下一個對話起開場會帶代答授權、你的更正、你要 AI 怎麼跟你說話、長期規矩。AI 做完一段、清單還有下一項、沒在問你、也沒碰到重大的事（刪除、推送、發布、付款這類）時，人格會替你答「繼續做下一項」，對話裡會留一行「〈人格代答〉…」；判斷時 AI 最後那段話的結尾會送到 Apersona 網站（秘密先遮、不存），連續 5 次或被你推翻太多次就自己停；`/apersona:off` 關掉。
