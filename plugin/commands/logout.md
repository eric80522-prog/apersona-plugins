---
description: 登出 Apersona：在網站上撤銷這台的登入，並刪掉這台的登入檔
---

Sign this machine out. Only the system's `python3` is needed.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_login.py" logout`. It asks the website to sign this machine out (the refresh token is revoked, which ends the access token too) and deletes `~/.apersona/plugin-credentials.json`, whether the website could be reached or not.
2. Tell him the printed line as it is. If it says the website could not be reached, tell him to press 「登出這台」 on the website as well.
3. Never print or read the credentials file back to him. After this the hooks do nothing until `/apersona:login` again.
