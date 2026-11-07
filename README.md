# Apersona 連接器與 Claude Code 外掛

Apersona（https://www.apersonaai.com）把你本人的經驗與取捨，用在你正在做的真實任務上。這個庫放兩種接上去的方式。

## 連接器和外掛差在哪

| | 連接器 | 外掛 |
|---|---|---|
| 是什麼 | 在 AI 助理加一個網址（MCP） | 裝進 Claude Code，除了 MCP 還有 hook 與指令 |
| 能做什麼 | 助理需要時查你的記憶與預決策 | 同左，另外開新對話自動帶開場包、每輪提示有相關記憶 |
| 適用 | Claude.ai、ChatGPT、Claude Code | Claude Code（Codex 的裝法見外掛說明） |
| 登入 | OAuth，按「允許」 | MCP 同左；hook 另外在 Claude Code 打一次 `/apersona:login`（裝置碼登入，按「允許」） |

連接器只能「被問才答」；外掛會在開場與每輪主動帶記憶。

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

- MCP 伺服器 `apersona`（同上，OAuth 登入）。
- 開新對話時拿開場包、每打一句找相關記憶的 hook（用系統的 python3，不需裝套件）。要先在 Claude Code 打一次 `/apersona:login`，到瀏覽器按「允許」；登入存在你這台的 `~/.apersona/plugin-credentials.json`，沒登入時 hook 什麼都不做，MCP 不受影響。
- `/apersona:login`、`/apersona:on`、`/apersona:off`、`/apersona:read` 指令（`read` 把你指定資料夾的原始檔送到網站，送前先問你、先在本機遮秘密）。

裝完後打 `/apersona:login` 登入一次（人格由你授權的那個決定，不用填人格識別碼或金鑰）。詳細見 [plugin/README.md](plugin/README.md)。

## 授權

MIT，見 [LICENSE](LICENSE)。
