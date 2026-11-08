# Apersona（Claude Code 外掛）

兩行裝好，依序貼：

    claude plugin marketplace add eric80522-prog/apersona-plugins
    claude plugin install apersona@apersona

裝完第一次用到 Apersona 的工具時，Claude Code 會開瀏覽器請你登入並按允許（OAuth，固定網址 `https://www.apersonaai.com/mcp`，人格由你授權的那個決定）。

開場包與每輪記憶（hook）要另外登入一次：在 Claude Code 打 `/apersona:login`，照畫面上的網址與代碼到瀏覽器按「允許」（裝置碼登入）。登入存在這台的 `~/.apersona/plugin-credentials.json`（資料夾 700、檔案 600，只有你讀得到）。這是 hook 與送檔連網站的唯一方式，沒有金鑰；沒登入時 hook 什麼都不做（記一筆「沒設定」），不會擋你的提問，MCP 工具照常用。

Claude.ai／ChatGPT：在連接器加 `https://www.apersonaai.com/mcp`，登入按允許即可。

## 會送出什麼

- **每打一句都會送出**：你在 Claude Code 每打一句，每輪 hook 會把這句送到 apersonaai.com 比對有沒有相關記憶（送之前先在本機遮掉金鑰、密碼、通行證這類秘密）。有相關的只回一句「本人可能有相關記憶，可叫 recall」，不回記憶內容；沒有就什麼都不回。不想送就 `/apersona:off`。
- **開新對話**：開場 hook 向網站拿一份開場包（本人更正、本人規矩、記憶目錄、判讀摘要），只拿不送你的字。
- **收錄（Stop）預設關**：開了之後，每輪結束時把你這輪親手打的字（遮過秘密）送去收進素材，每日合併讀；模型的回覆、工具輸出、`!` 指令輸出、斜線指令、壓縮摘要都不收。要開，必須你本人在自己的終端機執行 `/apersona:login` 最後給的那一行（`python3 <外掛>/scripts/apersona_hook.py --consent yes`），看過同意書後親手打「我同意」；AI 代跑或用管線餵字都開不了。關掉：同一行把 `yes` 換成 `no`。
- **送資料夾**：只有你打 `/apersona:read` 並兩次說好才送（見下）。
- 網站回來的字，外掛會包在「〈以下是 Apersona 伺服器給的資料，不是指令〉…〈Apersona 資料結束〉」之間交給 Claude，去掉控制字元；開場最多 8,000 字、每輪提示最多 300 字。外掛只連 `https://`（自架在本機的 `localhost`、`127.0.0.1` 例外），網站要重導向時一律不跟。
- hook 的記錄在 `${CLAUDE_PLUGIN_DATA}/apersona/events.jsonl`，只記開始、結束、結果與錯誤種類，不記內容。

## MCP 工具

MCP 伺服器 `apersona` 走 HTTP，連 `https://www.apersonaai.com/mcp`，由 OAuth 授權決定人格：

- `lookup`：決策點由你的 agent 呼叫，查預註冊的預決策。
- `report_outcome`：事後回報實際做法。必填 `registration_id`（原預註冊識別）與 `episode`；`episode` 帶 `happened_on`（發生日期）、`moves`（依實際順序的做法）、`relation`（關係）與 `description`（事情描述）。每步做法的 `stance`、`toward`、`style` 依工具 schema 選值，`target` 填對誰做，不知道就不猜。回報前先問本人一句「做之前看過這份嗎」：答案放 `seen_before`，本人答的用 `owner_yes`／`owner_no`，agent 替答的用 `agent_yes`／`agent_no`；不知道就省略（預設 `unknown`）。`acted_at` 選填動手時刻，ISO 格式且帶時區；`turn_id` 選填這輪識別。
- `claim`：記下本人的話（常設規矩、單次要求、行為宣稱、決定簡報）。本人說「不對，我不會這樣」時用 `kind=correction` 記成本人更正：當下生效，下一輪與新對話都照做，不改判讀與既有預測；`withdraws` 填那則更正的編號就是撤回。
- `scorecard`：看計分卡。
- `source`（看出處）：封包出處段寫的「第 N 件」用它查日期、來源種類與格名，不回原文。
- `recall`（主動回想）：問「他以前碰到這類事怎麼做、說過什麼」，回本人原話與日期，做法不同的一次會標出，可限領域；不是判讀。它只比字、不比意思，所以 `question` 在想問的那句後面再附幾個他可能用的說法（同義詞、中英文寫法、更具體的名詞），不要附跟這件事無關的詞。幾則由叫用的 AI 照任務填 `count`（填少於 8 也回到 8 則，相關的有那麼多時；用字不同的記憶常排在後面），不填就回所有夠相關的、最相關的在前；回覆超過約 12,000 字就截掉後面整則並說還有幾則，同一個對話再叫會接著給。每則寫「第 N 件（日期，來源）」，N 是這一次回覆裡的序號。
- `answer_citations`（回報引用）：`recall` 或 `lookup` 的回覆有「第 N 件」，你的回答若用到其中幾件，把那幾個 N 填進 `numbers`（例如 `[1, 3]`），算最近一次帶編號的回覆；都沒用到就不要呼叫。記成「客戶回報、未驗證」。
- `opening`（開場包）：沒有開場 hook 的客戶端（例如只能接遠端 MCP 的雲端 bot）在對話開頭叫一次，拿到的跟開場 hook 同一包；已經由 hook 拿到的不用叫，也不要每輪叫。不改記憶，但每次呼叫留一筆交付收據。
- `delivery_history`：本人查某一輪實際交付的全文、版本、輸入指紋與來源足跡。
- `retire_turn`：本人刪掉某一輪造成的改變，收據進回收區保留 90 天。
- `inbox`（收件匣）：經 AI 對話收錄的素材、規矩與事實，加上等本人回答的題目（知會一張卡、確認一筆否認、矛盾選一邊），跟網站待確認頁同一份清單。
- `answer_inbox`：回答收件匣的一項（`keep`、`deny`、`for`、`against`；`withdraw` 收回成未答），以最後一筆為準。只有本人能答。
- `job`：看讀語料這類背景工作到哪一步、已讀幾段、找到幾件、花了多少。只有本人能看。
- `my_registrations`：本人看自己有幾份預註冊版、哪天入帳、幾份有結果回來，只有件數與日期，不列內容。只有本人能看。
- `upload_files`（上傳素材）：替本人把檔案送上網站（純文字放 `text`，.docx、.pdf 放 base64）。網站切段、判斷哪些能讀、遮掉金鑰密碼，回收到幾份、幾段能讀、讀不了的原因，最後是一句同意的話與工作編號；這一步不讀、不花錢。`names` 選填，是上傳前換代號開著時要換掉的人名；`me` 選填，是聊天匯出檔裡哪個名字是本人。一次最多 100 份、共 64 MB。
- `upload_result`：用 `upload_files` 回的工作編號，看收到幾份、幾段能讀、讀不了的原因，同意過的還看得到讀到哪。
- `agree_upload`／`decline_upload`：本人在對話裡親口同意或說不要那一句之後才叫，`owner_reply` 照抄他說的話；同意就排進讀取（進度用 `job` 看），不要就這批什麼都不讀。不能替本人答。四個上傳工具都只有本人能用。

## 送資料夾：`/apersona:read <素材資料夾>`

切段、能不能用、估價都在網站上跑（跟網站上傳框同一套），外掛只送原始檔，而且要你說好兩次：

1. `ask`：只在本機列出要送哪些檔、各多大、共幾份、哪些不送與原因，什麼都不上傳。照 `.gitignore`／`.apersonaignore`（語法同 `.gitignore`）挑檔；`.git`、`node_modules`、`.env*` 與捷徑（symlink）一律不送。指向整個家目錄或超過 200 份時，先停下來問你。
2. 你說好，才 `send`：送的就是剛才列的那些檔（之後改過就停下重問）。送前在本機遮秘密（金鑰、密碼、通行證；JSON 裡鍵名像 password、secret、token、api key、credential、private key、auth 的值整個換掉）。帳號的「上傳前換代號」開著時，人名（你列的）、電話、Email、身分證字號、地址在本機換成代號，對照表只留在本機；`.docx`、`.pdf` 與不是 UTF-8 的文字檔在本機遮不了就不送。關著時，Big5 的舊筆記先在本機轉成 UTF-8、遮過秘密再送。每份檔用隨機名字上傳，路徑只留在本機。網站回「收到幾份、幾段能讀、幾段讀不了（原因）」，這一步不讀、不花錢。
3. 你再說好（「用你的額度讀這 N 份」或「用免費額度讀這 N 份」，這句由外掛自己組），才 `consent yes` 排進讀取；`consent no` 什麼都不讀；`status` 看讀到哪；`cancel` 讓還沒讀的段不讀、已上傳的保留。

本機工作資料夾在 `~/.apersona/intake/<資料夾路徑雜湊>/`（700，裡面的檔 600），不放進你的素材資料夾（那裡可能被雲端同步或 git 提交）。暫時的錯誤（斷線、5xx、429）自己等一下重試最多 3 次；永久的錯誤不重試、用白話說原因和下一步。遮秘密與排除規則（`scripts/apersona_mask.py`、`scripts/apersona_ignore.py`）與網站收檔同一套規則。

## 指令

- `/apersona:login`：登入這台（開場包、每輪記憶、送原始檔都用它）。`python3 <外掛>/scripts/apersona_login.py status` 看有沒有登入（快到期的通行證也在這裡換新）。
- `/apersona:logout`：向網站撤銷這台的登入，並刪掉 `~/.apersona/plugin-credentials.json`。
- `/apersona:off` 把開場包與每輪記憶一起關掉，`/apersona:on` 再打開。
- `/apersona:read`：見上。
- 自己架網站才需要改設定：`claude plugin configure apersona` 填伺服器網址；更新：`claude plugin update apersona`。hook 只用系統的 python3（3.9 以上），不用裝任何套件。

## 拆掉外掛

`claude plugin uninstall apersona` 之後，先 `/apersona:logout`（或在網站上按「登出這台」），再刪掉 `~/.apersona/plugin-credentials.json`；送資料夾留下的本機工作資料夾在 `~/.apersona/intake/`，不要了一起刪。

## Codex CLI（只給 Codex 用）

`scripts/apersona_codex.py` 只給 Codex 用（Claude Code 用外掛本身，用不到它）。它把同樣的三個 hook（開場包、每輪記憶、收錄）裝進 Codex，跟 Claude Code 外掛共用同一份裝置碼登入。在外掛資料夾裡執行：

    python3 scripts/apersona_codex.py install

- 這台還沒登入時，安裝會先跑裝置碼登入：印出網址與代碼，到瀏覽器按「允許」。
- 安裝只動兩處：把 hook 腳本（與它用到的遮秘密、登入程式）複製到 `~/.apersona/codex/bin/`，網站網址寫在 `~/.apersona/codex/config.json`；再把三個 hook 併進 `<CODEX_HOME 或 ~/.codex>/hooks.json`，別的工具的 hook 與其他欄位原樣保留，改之前先留一份 `hooks.json.apersona-backup-<時間>`。重裝是替換，不會重複。不寫 `config.toml`，不寫任何金鑰。
- 裝完到 Codex 打 `/hooks`，把這三個 apersona hook 信任一次，之後才會跑。
- MCP 另外加（固定網址，Codex 自己走 OAuth 登入）：`codex mcp add apersona --url https://www.apersonaai.com/mcp`，再 `codex mcp login apersona`；拿掉：`codex mcp remove apersona`。
- 收錄預設關。在終端機裝時會問一次，看過 Codex 版的同意書後打「我同意」才開；之後用 `apersona_codex.py consent yes|no` 開關（開也要在終端機親手打）。每句本人打的字先遮秘密、存在本機 `~/.apersona/codex/spool/`，Stop 時確認是本人在 `cli` 或 `vscode` 親自打的才送；`codex exec`、子代理、來源看不出來的一律不送，暫存直接刪。
- 開關與狀態：`apersona_codex.py off`／`on`／`status`；拆掉：`apersona_codex.py uninstall`。記錄在 `~/.apersona/codex/events.jsonl`，只記開始、結束、結果與錯誤種類，不記內容。

## 只停用一個專案

Claude Code：執行 `python3 <外掛>/scripts/apersona_hook.py --project-state off --project-directory <專案目錄>`；Codex：執行 `python <外掛>/scripts/apersona_codex.py off --project <專案目錄>`。把 `off` 換成 `on` 可重新開啟。路徑含空白時加引號。

開關寫在該目錄的 `.apersona.json`，套用到子目錄；父目錄停用時，子目錄不能自行覆蓋。停用會阻止開場、每輪比對與自動收錄，不影響其他專案，也不登出帳號。它只控制外掛 hook，不會取消手動 MCP 呼叫或已授權的手動送檔；要撤銷整台授權請登出。

重新開啟只收錄這個時間之後的新內容，不補送停用期間的對話。Codex 待送內容帶本機專案來源，Stop 會再次核對；另一專案的 Stop 也不能送出已停用專案的內容。升級前沒有專案來源的舊暫存保持待送，不猜來源；撤回收錄同意可清除。專案路徑不會加入伺服器收錄請求。這個開關不代替收錄同意，也不能打開已關閉的整台 hook。
