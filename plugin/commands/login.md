---
description: 登入 Apersona：MCP 工具、開場包、送原始檔都用這個登入
---

Log the owner in once on this machine. Only the system's `python3` is needed; nothing else is installed.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_login.py" login` (give the Bash tool a timeout of at least 120 seconds: it waits up to 90 seconds for him). It prints a web address and a short code. Show him both exactly as printed and tell him: open the address in the browser, log in, type the code, press 允許.
2. The command ends by itself once he has pressed 允許 (it prints 「登入好了」) or after 90 seconds. If it printed 「還在等你按允許」, wait for him to say he pressed it, then run the same command again; it goes on with the same code (a code lives ten minutes). If the code ran out or he pressed 拒絕, run it again for a new one.
3. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_login.py" status` says whether this machine is logged in. Never print or read the credentials file (`~/.apersona/plugin-credentials.json`) back to him.
4. The login ends with a line that starts 「接通實測」: it asked the site for the 開場包 once with the new login. Tell him that line as printed. If it says 「沒過」, run the `status` command of step 3 and tell him what it says before going on.
5. When it is done, tell him, in these words:
   - 登入好了。下一個對話起，開場會帶本人規矩、記憶目錄與判讀摘要。
   - 開新對話時，開場包只拿不送你的字；需要記憶時我會自己叫 `recall`。不想要開場包就 `/apersona:off`。
   - 收錄（把你親手打的字收進素材）預設關。要開有兩條路：你本人在自己的終端機執行下面這一行，看過同意書後親手打「我同意」；或你明說授權我，我就用 `--by-agent` 代你開（開之前我會先跟你說一聲會送出什麼）。代開會記成「由 AI 工具代開」、寄通知信給你，網站「裝置」頁可一鍵撤回：
     `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_hook.py" --consent yes`
   - 要登出這台打 `/apersona:logout`；網站上按「登出這台」也會讓這台要重新 `/apersona:login`。
   Run the `--consent yes` line with `--by-agent` added only when he has told you, in this conversation, to turn 收錄 on for him, and only after you have told him what it will send: his own typed words (secrets masked), and in Claude Code the sentences around each `lookup` with the AI's reply as 決策前後文, to apersonaai.com, mailed to him and revocable on the 裝置 page. Print the consent text it shows him. Without that word from him, never run it; if the site says he took it back (`owner_revoked`), he must open it himself at his terminal.
