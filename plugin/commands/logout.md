---
description: 登出 Apersona：在網站上撤銷這台的登入，並刪掉這台的登入檔
---

Sign this machine out. Only the system's `python3` is needed. `L` below is `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_login.py" logout`.

1. Run `L`. When this machine's 收錄 is on and has taken turns in, it does nothing yet: it prints the question 這台開收錄以來收了 N 輪（…），要保留嗎？ and ends with exit code 6.
   - Show him that question as printed and ask him to say 保留 or 刪除. Only he chooses; never answer for him, and 人格代答 does not count. When he answers, run `L --keep keep` (保留) or `L --keep delete` (刪除). If he does not answer or says not to log out, stop: nothing is signed out.
2. Otherwise (or after his answer) the script turns 收錄 off on the website with his answer, asks the website to sign this machine out (the refresh token is revoked, which ends the access token too), deletes `~/.apersona/plugin-credentials.json` whether the website could be reached or not, and deletes this machine's capture-consent files.
3. Tell him the printed lines as they are. If they say the website could not be reached, tell him to press 「登出這台」 on the website as well.
4. Never print or read the credentials file back to him. After this the hooks do nothing until `/apersona:login` again, and 收錄 is off until he turns it on again.
