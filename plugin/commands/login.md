---
description: 登入 Apersona：開場包與每輪記憶、送原始檔都用這個登入，不用貼金鑰
---

Log the owner in once on this machine. Only the system's `python3` is needed; nothing else is installed.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/bin/apersona_login.py" login` (give the Bash tool a timeout of at least 120 seconds: it waits up to 90 seconds for him). It prints a web address and a short code. Show him both exactly as printed and tell him: open the address in the browser, log in, type the code, press 允許.
2. The command ends by itself once he has pressed 允許 (it prints 「登入好了」) or after 90 seconds. If it printed 「還在等你按允許」, wait for him to say he pressed it, then run the same command again; it goes on with the same code (a code lives ten minutes). If the code ran out or he pressed 拒絕, run it again for a new one.
3. `python3 "${CLAUDE_PLUGIN_ROOT}/bin/apersona_login.py" status` says whether this machine is logged in. Never print or read the credentials file (`~/.apersona/plugin-credentials.json`) back to him.
4. Tell him in one line: 登入好了；下一個對話起，開場會帶本人規矩、記憶目錄與判讀摘要，每打一句有相關記憶時會提示可叫 recall（`/apersona:off` 可關）。網站上「登出這台」按下去，這台就要重新 `/apersona:login`。
