# Apersona 連接器（只有 MCP）

連接器就是在 AI 助理裡加一個網址。加好之後，助理可以查你的人格、記憶與預決策；它不會在每次開新對話時自動帶記憶（那是外掛的事，見上一層的說明）。

網址（固定一個，人格由你登入時授權的那個決定）：

    https://www.apersonaai.com/mcp

認證用 OAuth 2.1，已上線。第一次連時會跳出登入頁，登入後按「允許」即可，不用貼金鑰。

## Claude.ai、ChatGPT 與其他遠端 MCP 客戶端

1. 在客戶端新增自訂遠端 MCP，名稱填 `Apersona`，伺服器網址填上面的 `/mcp`。
2. 認證選 OAuth。Apersona 支援動態用戶端註冊（DCR），不需要自行貼金鑰；按平台流程登入網站，選人格並由本人允許。
3. 在對話中啟用這個連接器，先請 AI 呼叫 `opening`。之後沿用回覆中的 `conversation_id`；沒有 hook 不會自動開場。

實際新增入口與可用範圍依平台版本、工作區權限及安全設定而異。ChatGPT 的官方說明見 [新增自訂 MCP](https://developers.openai.com/api/docs/guides/custom-mcp-server)；建立後仍需安裝並在對話使用，不以建立成功當作首次查詢已完成。

遠端 MCP 與本機外掛的裝置登入是獨立授權。收件確認須由取得 recall 回覆的同一憑證，帶該回覆的 `conversation_id` 與 `received`；不能由另一台裝置代確認。這裡列的是服務已支援的協定流程，不代表每個平台皆完成實機驗收。

## Claude Code

只要 MCP（不要開場包與每輪記憶）：

    claude mcp add --transport http apersona https://www.apersonaai.com/mcp

要開場包與每輪記憶，請改裝外掛，見上一層的 README。
