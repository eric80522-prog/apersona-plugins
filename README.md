# Apersona 連接器與 Claude Code／Codex 外掛

Apersona（https://www.apersonaai.com）把你本人的經驗與取捨，用在你正在做的真實任務上。這個庫提供遠端連接器、Claude Code 外掛與 Codex 原生外掛。

## 連接器和外掛差在哪

| | 連接器 | 外掛 |
|---|---|---|
| 是什麼 | 在 AI 助理加一個網址（MCP） | 裝進 Claude Code，除了 MCP 還有 hook 與指令 |
| 能做什麼 | 助理需要時查你的記憶與預決策 | 同左，另外開新對話自動帶開場包、每輪提示有相關記憶 |
| 適用 | Claude.ai、ChatGPT、Claude Code | Claude Code（Codex 的裝法見外掛說明） |
| 登入 | OAuth，按「允許」 | Claude Code 的 MCP 與 hook 共用 `/apersona:login` 裝置登入（0.2.3 起） |

上表的 hook 外掛是 Claude Code 版本。Codex 原生包是 skill 加遠端 MCP，由 skill 在任務中取開場與記憶，沒有自動 hook。

## 安裝

### Claude.ai、ChatGPT

照 [connector/README.md](connector/README.md) 加連接器，網址 `https://www.apersonaai.com/mcp`，登入按允許。

### Claude Code：只要 MCP

    claude mcp add --transport http apersona https://www.apersonaai.com/mcp

裝了什麼：只在你的 Claude Code 設定加一個叫 `apersona` 的 MCP 伺服器，指向上面的網址；第一次用時走 OAuth 登入。沒有 hook，沒有開場包。

### Claude Code：要開場包與每輪記憶

    claude plugin marketplace add eric80522-prog/apersona-plugins
    claude plugin install apersona@apersona

第一行把這個庫登記為外掛來源（`apersona`），不安裝任何東西；第二行安裝其中的 `apersona` 外掛，裝了：

- MCP 伺服器 `apersona`：本機 stdio 轉送到網站，與 hook 共用裝置登入，不另建 OAuth 授權。
- 開新對話時拿開場包、每打一句找相關記憶的 hook（用系統的 python3，不需裝套件）。要先在 Claude Code 打一次 `/apersona:login`，到瀏覽器按「允許」；登入存在你這台的 `~/.apersona/plugin-credentials.json`，沒登入時 hook 不擋提問，MCP 會提示先登入。
- `/apersona:login`、`/apersona:logout`、`/apersona:on`、`/apersona:off`、`/apersona:read` 指令（`read` 把你指定資料夾的原始檔送到網站，送前先問你、先在本機遮秘密）。

裝完後打 `/apersona:login` 登入一次（人格由你授權的那個決定，不用填人格識別碼或金鑰）。詳細見 [plugin/README.md](plugin/README.md)。

### Codex 桌面：原生外掛

    codex plugin marketplace add https://github.com/eric80522-prog/apersona-plugins.git
    codex plugin add apersona@apersona

回桌面重新載入外掛，選 Apersona 執行 setup，由本人完成一次 OAuth。接著直接問正在做的事，skill 會取開場、查本人經驗並附來源回答。這個 Codex 包不需要 Python／Bash、不載入 Claude hooks、不另做裝置碼登入；沒有每輪自動提示或自動收錄。

已用 Codex 桌面附帶 CLI 在隔離設定驗安裝、啟用及 MCP 載入；真人 OAuth 與首次問題仍須實走。詳細見 [接入說明](plugin/README.md)。

### Codex CLI：手動 MCP 與可選 hook

純 MCP 可使用遠端 OAuth；需要開場與每輪提示時，另有隔離安裝腳本與排錯步驟，見 [Codex 接入說明](plugin/README.md)。Codex hook 沒有自動確認 recall 收件的已驗證事件契約；客戶端須沿同一 MCP 憑證回傳 `received`，不能用另一筆裝置登入代確認。

### Antigravity／Gemini CLI

下載本庫後，`plugin/antigravity` 與 `plugin/gemini` 分別是兩家的原生安裝根。都用遠端 MCP 與 OAuth，不用 API key；Antigravity 另有工具呼叫提示 hook，Gemini 使用 extension skill。安裝命令及限制見 [Google 接入說明](plugin/README.md#googleantigravity-與-gemini-cli-原生包)。

已驗 Antigravity manifest schema／hook JSON 契約，以及隔離 Gemini CLI 的 extension、skill 與 MCP 設定載入；尚未完成真人 OAuth 與首問。Gemini 安裝結尾曾出現 Windows libuv assertion，後續 list 命令成功，不能據此宣稱所有環境安裝全綠。

### 收件確認與驗證範圍

Claude Code 的 PostToolUse 從成功完整的 recall 結果取出伺服器對話識別與收件識別，使用同裝置確認。丟失、錯誤、截斷與未知格式不確認，其他裝置不能代確認。舊版更新後需重新開啟 Claude Code 載入新的 MCP 掛載。已驗證隔離本機 MCP、憑證隔離與 Python 3.9；真 Claude Code 平台更新、事件與重連尚未實機驗收。

遠端 `/mcp` 的網頁 OAuth 接法維持不變。轉送逾時或取消不會重送工具呼叫，遠端可能已執行，應先查結果。

## 授權

MIT，見 [LICENSE](LICENSE)。
