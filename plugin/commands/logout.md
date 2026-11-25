---
description: 登出 Apersona：在網站上撤銷這台的登入，並刪掉這台的登入檔
---

Sign this machine out. Only the system's `python3` is needed. `L` below is `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apersona_login.py" logout`.

1. Run `L`. If this machine's 收錄 is on, the script first turns it off on the website; what it took in stays saved and nothing is asked or deleted. It then asks the website to sign this machine out (the refresh token is revoked, which ends the access token too), deletes `~/.apersona/plugin-credentials.json` whether the website could be reached or not, and deletes this machine's capture-consent files.
2. If he asks to have what was taken in deleted, tell him the two ways the printed line gives (write to us at the contact address on the website's privacy page, or delete the account under 「設定」→「帳號」 on the website); nothing here deletes it.
3. Tell him the printed lines as they are. If they say the website could not be reached, tell him to press 「登出這台」 on the website as well.
4. Never print or read the credentials file back to him. After this the hooks do nothing until `/apersona:login` again, and 收錄 is off until he turns it on again.
