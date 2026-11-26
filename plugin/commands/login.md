---
description: 登入 Apersona：MCP 工具、開場包、送原始檔都用這個登入
---

Log the owner in once on this machine. Only the system's `python3` is needed; nothing else is installed.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_login.py" login` (give the Bash tool a timeout of at least 120 seconds: it waits up to 90 seconds for him). It prints a web address and a short code. Show him both exactly as printed and tell him: open the address in the browser, log in, type the code, press 允許.
2. The command ends by itself once he has pressed 允許 (it prints 「登入好了」) or after 90 seconds. If it printed 「還在等你按允許」, wait for him to say he pressed it, then run the same command again; it goes on with the same code (a code lives ten minutes). If the code ran out or he pressed 拒絕, run it again for a new one.
3. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_login.py" status` says whether this machine is logged in. Never print or read the credentials file (`~/.apersona/plugin-credentials.json`) back to him.
4. The login ends with a line that starts 「接通實測」: it asked the site for the 開場包 once with the new login. Tell him that line as printed. If it says 「沒過」, run the `status` command of step 3 and tell him what it says before going on.
5. Tell him: 登入好了。下一個對話起會拿開場包。需要時我會用 `recall` 查你的原話，或用 `lookup` 查判讀。要登出這台打 `/apersona:logout`。
