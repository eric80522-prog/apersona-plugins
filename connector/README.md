# Apersona 連接器（只有 MCP）

連接器就是在 AI 助理裡加一個網址。加好之後，助理可以查你的人格、記憶與預決策；它不會在每次開新對話時自動帶記憶（那是外掛的事，見上一層的說明）。

網址（固定一個，人格由你登入時授權的那個決定）：

    https://www.apersonaai.com/mcp

認證用 OAuth 2.1，已上線。第一次連時會跳出登入頁，登入後按「允許」即可，不用貼金鑰。

## Claude.ai（網頁）

1. 設定 → 連接器 → 新增自訂連接器。
2. 名稱填 `Apersona`，網址填上面那一行。
3. 儲存後按「連線」，在跳出的 Apersona 登入頁登入，按「允許」。

## ChatGPT（網頁）

1. 設定 → 連接器（開發者模式或自訂連接器，依你的方案顯示而定）→ 建立。
2. 名稱填 `Apersona`，MCP 伺服器網址填上面那一行，認證選 OAuth。
3. 建立後在跳出的 Apersona 登入頁登入，按「允許」。

## Claude Code

只要 MCP（不要開場包與每輪記憶）：

    claude mcp add --transport http apersona https://www.apersonaai.com/mcp

要開場包與每輪記憶，請改裝外掛，見上一層的 README。
