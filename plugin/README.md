# Apersona（Claude Code 外掛）

兩行裝好，依序貼：

    claude plugin marketplace add eric80522-prog/apersona-plugins
    claude plugin install apersona@apersona

裝完先在 Claude Code 打 `/apersona:login`，照畫面上的網址與代碼到瀏覽器按「允許」（裝置碼登入，開場包與每輪記憶的 hook 靠它）。授權頁會印出這台連到的人格名稱（帳號有好幾個人格時可以選），並寫明：只有這台電腦上的外掛，用你的帳號做查詢、回報、上傳與修改，別人不能用。

0.2.3 起，Claude Code 外掛內的 MCP 工具與 hook 共用這次裝置登入。外掛用本機 Python 標準函式庫將 stdio JSON-RPC 轉送到網站 `/mcp`，不另開第二筆 OAuth 授權。登入存在這台的 `~/.apersona/plugin-credentials.json`（資料夾 700、檔案 600；Windows 沿用資料夾權限）。沒登入時 hook 不擋提問，MCP 回覆需要先登入；已安裝舊版者更新外掛後重新開啟 Claude Code，讓它重新載入 MCP 設定。

Claude Code 的 `PostToolUse` 只從成功、完整的 Apersona recall 工具結果取出對話識別與收件識別，以同一裝置登入確認收到。不是從使用者或模型自己寫的文字取識別；錯誤、截斷或未知結果格式不確認。下一輪 transcript 補送僅接受可配對的 recall 結果且回覆對話識別與宿主相符，每批最多 256 筆。Codex 與純遠端連接器沒有這條自動確認路徑，必須由同一憑證的 MCP 客戶端在 recall 的 `received` 帶回收件識別，並沿用回覆的 `conversation_id`。不同裝置或不同 OAuth 授權不能替彼此確認。

這條接法已以隔離本機服務與 Python 3.9 驗證；Claude Code 真平台的事件格式、更新後掛載與重連仍需實機驗收，不能把本機模擬當作已驗收。事件契約依 [Claude Code hooks 文件](https://code.claude.com/docs/en/hooks)。

Claude.ai／ChatGPT／Grok 這類純連接器（只加遠端 MCP、你的電腦上沒有我們的 hook）：加 `https://www.apersonaai.com/mcp`，登入按允許即可。授權頁預設只給「查詢與回報」；要讓它上傳素材、改回覆長短設定，在授權頁另外勾「也讓它上傳資料與修改」。本人更正仍可用 `claim` 記下，不必另開修改權限。沒勾的連接器叫 `upload_files` 這類工具會被擋，回一句白話（`connector_read_only`）說明到網站「設定」頁的「授權裝置與連線」按「開啟…的上傳與修改」；那裡隨時能開或關。本機工具（Claude Code、Codex、Cursor 這類有 hook 的）按允許就是全開，不用勾。這個分別是看登記時的轉址網址（本機位址＝有 hook），不是看工具自稱的名字；這個範圍上線前授權的裝置維持原有權限。

## 哪些 AI 工具能用到什麼

Apersona 在每個 AI 工具裡能做到的事不一樣，差別只在「我們看得到多少」，不在「你拿得到多少」：所有能接遠端 MCP 的工具都能叫 `lookup`、`recall`、`claim`，拿到一樣的封包。

| 工具 | 開場包自動到 | 每輪自動提醒 | 收你打的字 | 看得到 AI 回答（決策前後文） |
|---|---|---|---|---|
| Claude Code（裝外掛） | 是 | 是 | 是（要同意） | 是 |
| Codex CLI（`apersona_codex.py install`） | 是 | 是 | 是（要同意） | hook 不自動收；可經 MCP 回報 |
| Cursor | 是 | 否（開場包會教 AI 自己叫 recall） | 是（要同意） | 是 |
| Gemini CLI（原生包） | skill 呼叫 `opening` | 否 | 不自動收 | 不自動收 |
| Antigravity（原生包） | hook 指引 AI 呼叫 `opening`，不是已取回開場包 | 每次模型呼叫前提示工具流程 | 不自動收 | 不自動收 |
| Claude.ai、ChatGPT、Gemini app、grok.com、Grok Bot | 否（AI 要自己叫 `opening`） | 否 | 否 | 開了收錄才有（AI 自己帶你那句與它的回答） |

Cursor、Antigravity 已接進 `apersona_feed.py`（`cursor`、`antigravity` 平台，見下節）；Gemini CLI 只接了 AfterAgent 叫醒送出，SessionStart 與 BeforeAgent 還沒接。沒有 hook 的工具，`opening` 回來的第一行會寫明這段對話少哪幾樣，第二行給一個本段對話識別，AI 之後每次叫工具都帶它當 `conversation_id`。這類工具的 AI 叫 `lookup`、`recall` 時可以在 `owner_words` 帶你那句原話，叫 `answer_citations` 時在 `answer` 帶它的回答：那台裝置開了收錄（`capture_consent`，你授權的 AI 可以自己開，網站裝置頁可撤回）才存成決策前後文，只給決策中心看、不讀進人格；沒開就不存，決策中心那一件只列封包，並寫明「工具查詢與交付結果可查；對話原文與 AI 回答未收錄」。網站「授權裝置與連線」頁同樣標出每台登入的環境與能力。

### 支援矩陣

底線是每個客戶端都能接遠端 MCP＋OAuth；有 hook 的再往上加。能力等級：**完整**＝有開場並收本人的字；**只有開場**＝有開場、不收字；**無 hook**＝只有 MCP 工具，開場靠 AI 叫 `opening`。網站連接 AI 頁用同樣的分法標每台裝置（`hooks`／`opening_only`／`mcp_only`，查不到顯示「不明」）。

| 客戶端 | 體系 | 接法 | 能力等級 | 還缺什麼 |
|---|---|---|---|---|
| Claude Code | Anthropic | 本機 stdio 轉送 MCP；與 hook 共用裝置登入 | 完整 | 新掛載與 PostToolUse 真平台未驗 |
| Codex CLI | OpenAI | 遠端 MCP＋OAuth；`apersona_codex.py` hook | 完整（開場與本人字） | AI 回答不自動收；桌面版 hook 尚待實機核對 |
| Cursor | xAI | 遠端 MCP＋OAuth；`apersona_feed.py --platform cursor` 的 hook（`sessionStart`、`beforeSubmitPrompt`、`stop`） | 完整（收字用 `generation_id` 當輪次識別） | 每輪提示（`beforeSubmitPrompt` 只能擋不能加字，靠開場包教 AI 叫 `recall`）；Cloud Agent 沒有 `sessionStart`；實機未驗 |
| Antigravity | Google | `antigravity/` 原生包：遠端 MCP＋OAuth＋skill | hook 只給工具指引，skill 叫開場 | 真平台安裝、OAuth 與首問待驗；不自動收錄 |
| Gemini CLI | Google | `gemini/` extension：遠端 MCP＋OAuth＋skill | 無 hook，skill 叫開場 | 真人 OAuth 與首問待驗；不自動收錄 |
| ChatGPT | OpenAI | 遠端 MCP＋OAuth（Developer mode） | 無 hook | 開場要 AI 叫 `opening` |
| Grok（grok.com、Grok Build、Grok Bot） | xAI | 遠端 MCP；Grok Build 另有 hook 事件 | 無 hook（Grok Build 的事件沒有本人那句、AI 回覆） | 來源與同意見 `docs/development/plugin_platforms.md`；Grok Bot 的 `redirect_uri`、`client_name` 要正式站實機看 |
| Claude.ai 網頁／桌面 | Anthropic | 遠端 MCP＋OAuth（連接器） | 無 hook | 開場要 AI 叫 `opening` |

出處是各家官方 hook 與 MCP 文件（2026-11-09 查證），沒有真的客戶端實機驗收。

## 會送出什麼

- **每打一句都會送出**：你在 Claude Code 每打一句，每輪 hook 會把這句送到 apersonaai.com 比對有沒有相關記憶（送之前先在本機遮掉金鑰、密碼、通行證這類秘密）。有相關的只回一句「本人可能有相關記憶，可叫 recall」，不回記憶內容；沒有就什麼都不回。不想送就 `/apersona:off`。
- **開新對話**：開場 hook 向網站拿一份開場包（本人更正、本人規矩、記憶目錄、判讀摘要），只拿不送你的字。
- **收錄（Stop）預設關**：開了之後，每輪結束時把你這輪親手打的字（遮過秘密）送去收進素材，每日合併讀；模型的回覆、工具輸出、`!` 指令輸出、斜線指令、壓縮摘要都不收進素材。照目前的同意書開的收錄，Stop 另外會在每次 `lookup` 前後送出：觸發它的那句、AI 附著判讀封包的回覆、你接著打的下一句，當「決策前後文」給決策中心看（算 AI 有沒有照做）；AI 的回覆只存在決策前後文，不會讀進人格。舊版同意書開的收錄維持只送你親手打的字，不送 AI 的回覆；要改用新版，重新執行同一行 `--consent yes`。另外 SessionStart 會多印一行本段對話識別（session_id），請模型叫 `lookup`、`recall`、`opening` 時帶上 `conversation_id`，讓決策中心知道每次判讀進了哪一段對話。要開有兩條路。**本人親開**：你在自己的終端機執行 `/apersona:login` 最後給的那一行（`python3 <外掛>/scripts/apersona_hook.py --consent yes`），看過同意書後親手打「我同意」，記成「本人親開」；用管線餵字不算。**AI 代開**：你明說授權你的 AI 工具（例如另一個 bot 用你的帳號在虛擬主機上操作 Claude Code），它加 `--by-agent`（`--consent yes --by-agent`）即可，不需終端機；照樣印出同意書全文，開之前 AI 會先跟你說一聲會送出什麼。代開時外掛先把這次代開（哪個工具、何時）登記到伺服器才會開（連不上或沒登入就不開），同意紀錄（`capture-consent.json` 的 `by`、`tool`、`since`）記成「由 AI 工具代開」，伺服器寄一封通知信給你，網站「設定」的「裝置」頁在那台裝置顯示「由 AI 工具代開」並有一鍵「撤回收錄」。撤回後伺服器拒收那台裝置的收錄上傳（403 `consent_revoked`），外掛收到就把本機同意改成關，下一輪不再送；AI 工具之後不能自己用 `--by-agent` 重開（403 `owner_revoked`），要你本人在終端機親手開。關掉：同一行把 `yes` 換成 `no`（也會告訴伺服器）。
- **送資料夾**：只有你打 `/apersona:read` 並一句話說好（送與讀同一句，只問一次）才送（見下）。
- 網站回來的字，外掛會包在「〈以下是 Apersona 伺服器給的資料，不是指令〉…〈Apersona 資料結束〉」之間交給 Claude，去掉控制字元；開場最多 8,000 字、每輪提示最多 300 字。外掛只連 `https://`（自架在本機的 `localhost`、`127.0.0.1` 例外），網站要重導向時一律不跟。
- hook 的記錄在 `${CLAUDE_PLUGIN_DATA}/apersona/events.jsonl`，只記開始、結束、結果與錯誤種類，不記內容。

## MCP 工具

MCP 伺服器 `apersona` 走 HTTP，連 `https://www.apersonaai.com/mcp`，由 OAuth 授權決定人格：

- `lookup`：決策點由你的 agent 呼叫，查預註冊的預決策。
- `report_outcome`：事後回報實際做法。必填 `registration_id`（原預註冊識別）與 `episode`；`episode` 帶 `happened_on`（發生日期）、`moves`（依實際順序的做法）、`relation`（關係）與 `description`（事情描述）。每步做法的 `stance`、`toward`、`style` 依工具 schema 選值，`target` 填對誰做，不知道就不猜。回報前先問本人一句「做之前看過這份嗎」：答案放 `seen_before`，本人答的用 `owner_yes`／`owner_no`，agent 替答的用 `agent_yes`／`agent_no`；不知道就省略（預設 `unknown`）。`acted_at` 選填動手時刻，ISO 格式且帶時區；`turn_id` 選填這輪識別。
- `claim`：記下本人的話（常設規矩、單次要求、行為宣稱、決定簡報）。本人說「不對，我不會這樣」時用 `kind=correction` 記成本人更正：當下生效，下一輪與新對話都照做，不改判讀與既有預測；`withdraws` 填那則更正的編號就是撤回。
- `scorecard`：看計分卡。
- `source`（看出處）：封包出處段寫的「第 N 件」用它查日期、來源種類與格名，不回原文。
- `recall`（主動回想）：問「他以前碰到這類事怎麼做、說過什麼」，回來源原文與日期；來源文字不代表每句都是本人說的，作者不明時不推定。做法不同的一次會標出，可限領域；不是判讀。它只比字、不比意思，所以 `question` 在想問的那句後面再附幾個他可能用的說法（同義詞、中英文寫法、更具體的名詞），不要附跟這件事無關的詞。幾則由叫用的 AI 照任務填 `count`（填幾則回幾則，最相關的有做法不同的另一次時成對給、填 1 也回兩則，相關的有那麼多時；用字不同的記憶常排在後面，要看多些就多填），不填就回所有夠相關的、最相關的在前；回覆超過約 12,000 字就截掉後面整則並說還有幾則，同一個對話再叫會接著給。每則寫「第 N 件（日期，來源）」，N 是這一次回覆裡的序號。
- `answer_citations`（回報引用）：`recall` 或 `lookup` 的回覆有「第 N 件」，你的回答若用到其中幾件，把那幾個 N 填進 `numbers`（例如 `[1, 3]`），算最近一次帶編號的回覆。記成「客戶回報、未驗證」。選填 `answer` 帶 AI 給本人的回答全文：那台裝置開了收錄才存成決策前後文，跟那次 `lookup` 在決策中心同一列；只給 `answer` 時算本段對話最近一次 `lookup` 或 `recall`。`numbers`、`answer` 至少給一個。
- `lookup`、`recall` 的選填 `owner_words`：本人這一輪的原話，照抄。同樣只在開了收錄時存成決策前後文，不影響這次封包或回想、不讀進人格。
- `opening`（開場包）：沒有開場 hook 的客戶端（例如只能接遠端 MCP 的雲端 bot）在對話開頭叫一次，拿到的跟開場 hook 同一包，多兩行：沒有 hook 少哪幾樣，與本段對話識別（沒帶 `conversation_id` 時伺服器發一個，之後每次叫工具都帶它）；已經由 hook 拿到的不用叫，也不要每輪叫。不改記憶，但每次呼叫留一筆交付收據。
- `capture_consent`（收錄同意）：本人授權時 AI 可以替這台裝置開收錄（`on: true`），不帶 `on` 只查狀態；開了才存上面兩個選填欄。
- `delivery_history`：本人查某一輪實際交付的全文、輸入指紋、帶了幾件與登錄識別（版本與內部編號只留在站方的收據）。
- `retire_turn`：本人刪掉某一輪造成的改變，收據進回收區保留 90 天。
- `inbox`（收件匣）：經 AI 對話收錄的素材、規矩與事實，加上等本人回答的題目（知會一張卡、確認一筆否認、矛盾選一邊），跟網站待確認頁同一份清單。
- `answer_inbox`：回答收件匣的一項（`keep`、`deny`、`for`、`against`；`withdraw` 收回成未答），以最後一筆為準。只有本人能答。
- `job`：看讀語料這類背景工作到哪一步、已讀幾段、找到幾件、花了多少。只有本人能看。
- `my_registrations`：本人看自己有幾份預註冊版、哪天入帳、幾份有結果回來，只有件數與日期，不列內容。只有本人能看。
- `upload_files`（上傳素材）：替本人把檔案送上網站（純文字放 `text`，.docx、.pdf 放 base64）。網站切段、判斷哪些能讀、遮掉金鑰密碼，回收到幾份、幾段能讀、讀不了的原因，最後是一句同意的話與工作編號；這一步不讀、不花錢。`names` 選填，是上傳前換代號開著時要換掉的人名；`me` 選填，是聊天匯出檔裡哪個名字是本人。一次最多 100 份、共 64 MB。
- `upload_result`：用 `upload_files` 回的工作編號，看收到幾份、幾段能讀、讀不了的原因，同意過的還看得到讀到哪。
- `agree_upload`／`decline_upload`：本人在對話裡親口同意或說不要那一句之後才叫，`owner_reply` 照抄他說的話；同意就排進讀取（進度用 `job` 看），不要就這批什麼都不讀。不能替本人答。四個上傳工具都只有本人能用。

## 送資料夾：`/apersona:read <素材資料夾>`

切段、能不能用、估價都在網站上跑（跟網站上傳框同一套），外掛只送原始檔，而且只問你一次（一句話同時問送與讀）：

1. `ask`：只在本機列出要送哪些檔、各多大、共幾份、哪些不送與原因，什麼都不上傳。照 `.gitignore`／`.apersonaignore`（語法同 `.gitignore`）挑檔；`.git`、`node_modules`、`.env*` 與捷徑（symlink）一律不送。指向整個家目錄或超過 200 份時，先停下來問你。
2. 你一句話說好（「要把這幾份送上網站，並讓網站接著讀進人格嗎？」；讀取先用免費提取額度，用完才用你的額度），才 `send --read`：送的就是剛才列的那些檔（之後改過就停下重問）。送前在本機遮秘密（金鑰、密碼、通行證；JSON 裡鍵名像 password、secret、token、api key、credential、private key、auth 的值整個換掉）。帳號的「上傳前換代號」開著時，人名（你列的）、電話、Email、身分證字號、地址在本機換成代號，對照表只留在本機；`.docx`、`.pdf` 與不是 UTF-8 的文字檔在本機遮不了就不送。關著時，Big5 的舊筆記先在本機轉成 UTF-8、遮過秘密再送。每份檔用隨機名字上傳，路徑只留在本機。網站回「收到幾份、幾段能讀、幾段讀不了（原因）」，接著用你剛才那一句的同意直接排進讀取，不再問第二次。
3. 你說不要，什麼都不送、不讀。`status` 看讀到哪；`cancel` 讓還沒讀的段不讀、已上傳的保留；送完後改變心意可用 `consent no`，網站就不讀已收的檔。

本機工作資料夾在 `~/.apersona/intake/<資料夾路徑雜湊>/`（700，裡面的檔 600），不放進你的素材資料夾（那裡可能被雲端同步或 git 提交）。暫時的錯誤（斷線、5xx、429）自己等一下重試最多 3 次；永久的錯誤不重試、用白話說原因和下一步。遮秘密與排除規則（`scripts/apersona_mask.py`、`scripts/apersona_ignore.py`）與網站收檔同一套規則。

## 指令

- `/apersona:login`：登入這台（開場包、每輪記憶、送原始檔都用它）。`python3 <外掛>/scripts/apersona_login.py status` 看有沒有登入（快到期的通行證也在這裡換新）。
- `/apersona:logout`：向網站撤銷這台的登入，並刪掉 `~/.apersona/plugin-credentials.json`。
- `/apersona:off` 把開場包與每輪記憶一起關掉，`/apersona:on` 再打開。
- `/apersona:read`：見上。
- 自己架網站才需要改設定：`claude plugin configure apersona` 填伺服器網址；更新：`claude plugin update apersona`。hook 只用系統的 python3（3.9 以上），不用裝任何套件。
- 腳本沒做成時結束碼不是 0，最後一行固定寫「下一步（誰做）：…；本人同意：…；怎麼確認：…；結束碼：N」：1 本機或站的問題、2 沒做完（再跑會接著做）、3 站拒絕（再跑也不會過）、4 還沒登入、5 暫時失敗重試用完、6 要本人先回答。網站回的錯誤 JSON 帶同樣四欄（`who`、`next`、`consent`、`check`）。

## 拆掉外掛

`claude plugin uninstall apersona` 之後，先 `/apersona:logout`（或在網站上按「登出這台」），再刪掉 `~/.apersona/plugin-credentials.json`；送資料夾留下的本機工作資料夾在 `~/.apersona/intake/`，不要了一起刪。

## Google：Antigravity 與 Gemini CLI 原生包

`antigravity/` 與 `gemini/` 是兩個獨立安裝根，各自帶遠端 MCP 與接入 skill，不載入上層 Claude hooks，也不需要 API key 或第二次裝置碼登入。Antigravity 另帶只輸出工具指引的 SessionStart／PreInvocation hook，需要可執行的 `python`（Python 3.9 以上）；Gemini 包不需要 Python。先取得公開包並進入外掛目錄：

    git clone https://github.com/eric80522-prog/apersona-plugins.git
    cd apersona-plugins/plugin

Antigravity 安裝：

    agy plugin validate --strict ./antigravity
    agy plugin install ./antigravity
    agy plugin list

在 Customizations → Installed → MCP Servers 找到此外掛的 Apersona 連線，依 Authenticate 流程由本人完成 OAuth。`opening`、`lookup`、`recall` 設成 eager；其他工具依宿主延遲探索。本機沒有 Antigravity，manifest 驗證不能取代真平台安裝驗收。

Gemini CLI 安裝：

    gemini extensions install ./gemini
    gemini extensions list
    gemini mcp list

重新啟動 Gemini CLI，於對話內執行 `/mcp auth apersona` 完成 OAuth。若只要手動 MCP、不安裝 extension，替代接法是 `gemini mcp add --transport http apersona https://www.apersonaai.com/mcp`，之後用相同 `/mcp auth apersona`；不要兩條都裝。既有 settings.json 的同名伺服器會優先於 extension，接錯時先核對 `/mcp` 顯示的來源。

兩者首次都可說「用 Apersona 幫我處理這件事」。成功須實際收到 `opening` 封包，接著沿用回傳的 `conversation_id` 呼叫 `lookup`／`recall`，再以來源回答目前任務。需要工作區時才選，授權失效時重新驗證同一連線；只有安裝成功、OAuth 跳轉或空泛回答不算驗收。後續 `recall.received` 只能回報同一連線完整收到的收件識別。

Antigravity 的 SessionStart／PreInvocation 只注入簡短工具呼叫指引，不發網路請求、不讀 transcript、不儲存使用者輸入；不是已取回本人的開場包，也不強制每輪重拿。沒有 Stop handler，因此不攔截停止。Gemini 包沒有 hook。兩包都沒有自動收錄或 AI 回答自動捕捉。Antigravity `lastUserInput` 是最新輸入，`invocationNum` 是模型呼叫次數，不能當穩定的人類輪次；舊 `apersona_feed.py` 適配器不會因安裝原生包而接線。停用或移除請用宿主的外掛管理；Gemini 可用 `gemini extensions disable apersona`／`gemini extensions uninstall apersona`。移除外掛不等於撤銷 OAuth，需在網站「授權裝置與連線」另行撤銷。

格式依 [Antigravity Build Plugins](https://antigravity.google/docs/build-plugins)、[Antigravity MCP](https://antigravity.google/docs/mcp)、[Gemini extension reference](https://geminicli.com/docs/extensions/reference/) 與 [Gemini MCP](https://geminicli.com/docs/tools/mcp-server/)。MCP instructions 的宿主處理不同：Gemini CLI 會加入系統指引；Antigravity 的延遲探索會存 instructions.md，不能據此宣稱每輪自動呼叫；Google ADK 需應用端另外接開場指引，不能當成 Gemini CLI extension。

## Codex 桌面：原生外掛

`codex/` 是獨立的 Codex 外掛：一個接入 skill 加遠端 MCP，登入一次 OAuth 即可查詢。它不載入 Claude 的 hooks、不需要 Python 或 Bash，也不要求第二次裝置碼登入。安裝後執行外掛的 setup，讓 Codex 接續目前的任務、取得開場並查本人原話；只有安裝或授權成功還不算完成，必須實際取得 `opening` 回覆。

從公開 marketplace 安裝（Codex CLI 與桌面使用同一台的設定）：

    codex plugin marketplace add https://github.com/eric80522-prog/apersona-plugins.git
    codex plugin add apersona@apersona

回桌面重新載入外掛，選 Apersona 執行 setup；需要認證時由本人完成該連線的 OAuth。對話中說「用 Apersona 幫我處理這件事」即可。若已經手動加過同站 MCP，先確認使用哪一條連線，避免重複安裝與授權。

此包沒有每輪自動提示或自動收錄；開場與查詢由 skill 呼叫 MCP。收件確認使用同一連線的 `recall.received`，不宣稱 Codex 自動 ACK。桌面附帶 CLI 的隔離安裝與真人 OAuth／首問是兩項不同的驗證。

## Codex CLI：手動 MCP 與可選 hook

MCP 負責查人格與記憶；本機 hook 另外負責自動開場與每輪提示。兩者各有自己的登入，任何一邊成功都不能代表另一邊已連上。以下 hook 步驟針對 Codex CLI；桌面版的設定入口、hook 事件與對話來源尚待實機核對，不能直接算成支援。

### 只接 MCP

在有 Codex CLI 的終端機依序執行：

    codex mcp add apersona --url https://www.apersonaai.com/mcp
    codex mcp login apersona

瀏覽器開啟 Apersona 授權頁後，由本人登入、核對人格與授權範圍，再按允許。一般網址使用這台連線的預設人格；要固定人格，使用網站「連接 AI」提供的 `/mcp/p/<人格編號>` 網址。不要自行猜人格編號。MCP 設定由 `codex mcp add` 寫入；這一步不安裝 Apersona hook。

回到 Codex 開新對話，沒有 hook 時請它先呼叫 Apersona `opening`。成功證據是工具真的返回人格開場資料與本段對話識別，不只是 CLI 顯示登入成功。之後每次工具呼叫沿用該 `conversation_id`。若 hook 已交完整開場與識別，直接沿用；只缺開場資料時，帶同一識別補叫 `opening`，不要另生一段對話。

### 加上本機 hook

需要 Git 與 Python 3.9 以上。從[公開安裝庫](https://github.com/eric80522-prog/apersona-plugins)取得完整目錄，不要只下載單一腳本（它依賴旁邊的登入、遮秘密與專案設定程式）：

    git clone https://github.com/eric80522-prog/apersona-plugins.git
    cd apersona-plugins/plugin
    python3 scripts/apersona_codex.py install

Windows 若 Python 命令是 `python`，將上面的 `python3` 換成 `python`。已有 clone 時，在自己的乾淨安裝目錄先更新，再執行相同安裝命令。公開庫與產品開發庫分開發布，請以實際下載版本的 README、腳本與驗證結果為準；開發庫已完成的修正不代表公開包已帶入。

1. 安裝會把腳本複製到 `~/.apersona/codex/bin/`，網站網址寫到 `~/.apersona/codex/config.json`，把三個 hook 併入 `<CODEX_HOME 或 ~/.codex>/hooks.json`。保留其他工具的 hook；修改前留下 `hooks.json.apersona-backup-<時間>`。重裝替換自身項目，不重複加入。這支安裝腳本不寫 `config.toml`。
2. hook 還沒登入時會印裝置碼與網址。本人到瀏覽器選人格、按允許；憑證存在 `~/.apersona/plugin-credentials.json`，與 Claude Code 外掛共用。這與前一節 Codex MCP 的 OAuth 是兩條登入；請核對兩次選的是同一人格。
3. 到 Codex CLI 執行 `/hooks`，信任三個 Apersona hook，再開新對話。要看到開場資料及本段對話識別才算開場有交到；只有檔案存在或安裝程式結束不算。缺資料但有識別時，用同一識別補叫 MCP `opening`。連識別也缺時，可先用 MCP `opening` 完成純 MCP 首次查詢，但這不代表已和 hook 對齊，仍須照下方排錯。
4. 收錄預設關，不開也可查人格與記憶。要開時，終端機會展示 Codex 同意書，本人親手輸入「我同意」；之後用 `python3 scripts/apersona_codex.py consent yes`／`consent no` 開關。本人授權的 AI 工具可用 `consent yes --by-agent` 代開（需所下載版本支援），同意與撤回規則同上。

開了收錄後，本人字先遮秘密、暫存在 `~/.apersona/codex/spool/`；Stop 確認來源是 `cli` 或 `vscode` 才送出。`codex exec`、子代理與無法確認的來源不收錄。Codex hook **不自動擷取 AI 回答**；要在決策中心保留回答，需該 MCP 連線開啟收錄，再由 AI 用 `answer_citations` 的 `answer` 回報。這是客戶回報，不能視為已驗證採用。

### 第一次用人格回答

- 先取得開場與固定 `conversation_id`。要用工作區時，請 AI 呼叫 `workspace_status` 列出本人工作區，再用 `select_workspace` 選組；後續沿用同一對話識別，需指定人格時使用該組回傳的人格識別。已有別組時，要先展示換組問題，等本人明示同意才確認。沒有工作區也可用連線預設人格。工作區不擴大授權；授權頁選了一人就只能開那一人，要用另一人需另建連線重新授權。
- 提一件正在做的事，請 AI 用 `lookup` 查判讀；沒有算好的預決策時可用 `recall` 查原文。工具應返回有來源的資料或明確說資料不足，再由你的模型回答。回覆好看不代表接入通過。
- 查驗時保留實際工具回覆、所選人格／工作區與對話識別，再用 `delivery_history` 或網站決策中心核對這輪交出的資料。空人格能連上不代表已有可用偏好；先上傳資料、看估價，由本人同意讀取後再驗。

### 看狀態、復原與拆除

以下命令在下載的 `plugin/` 目錄執行：

| 現象／目的 | 下一步 | 確認方式 |
|---|---|---|
| MCP 沒連上或授權失效 | 重跑 `codex mcp login apersona`；網站核對連線是否撤銷及人格是否仍可用 | 新對話實際呼叫 `opening` 成功 |
| hook 沒有開場 | `python3 scripts/apersona_codex.py status`，再 `python3 scripts/apersona_login.py status`；核對 `/hooks` 信任與專案開關 | 查看 `~/.apersona/codex/events.jsonl` 的結果／錯誤種類；重新開對話看輸出 |
| 裝置碼逾時或登入未完成 | `python3 scripts/apersona_login.py login`，照新輸出的網址／代碼完成本人允許 | 登入狀態成功，且 hook 取得開場；MCP 仍另驗 |
| 想停 hook | `python3 scripts/apersona_codex.py off`；恢復用 `on` | 新對話不再自動帶入；手動 MCP 仍可使用 |
| 想拆 hook | `python3 scripts/apersona_codex.py uninstall` | hook 設定與複製腳本移除；記錄、同意與暫存仍保留 |
| 想移除 MCP | `codex mcp remove apersona` | Codex 不再列此 MCP；hook 登入不因此撤銷 |
| 想撤銷 hook 登入 | `python3 scripts/apersona_login.py logout`，或網站撤銷那台裝置 | 共用的 Claude Code hook 登入也會失效；MCP 的獨立授權需另外撤銷 |

## 其他平台（Gemini CLI、Grok、只有工具前事件的工具、Cursor、Antigravity）

`scripts/apersona_feed.py`。這些平台沒有可信的「這是本人新打的一句」識別，所以不自動收：本人自己 `send`，並自己給這一輪的 `--id`；沒給就回 `id_required`，什麼都不存不送。Gemini 的 BeforeAgent hook 同樣回 `id_required`，AfterAgent 與工具前事件只負責叫醒送出已同意的待送列，模型回答與工具參數一律不收。每個平台有自己的同意書（`consent --platform … yes`，在終端機親手打「我同意」，或你授權的 AI 工具加 `--by-agent` 代開，規則同 Claude Code：先登記到伺服器、寄通知信、網站裝置頁可撤回，撤回後下一次送出就把本機同意關掉），`consent … no` 立刻關並清掉待送。細節與錯誤碼見 `docs/development/plugin_platforms.md`。

Cursor 例外：它的 `beforeSubmitPrompt` 帶本人送出的那句與 `generation_id`，所以 `--platform cursor` 的 hook 自動收：送出時先遮秘密存在本機待送列，輪次識別是 `cursor:<conversation_id>:<generation_id>`，`stop` 事件才送；沒有 `generation_id` 就什麼都不存。`sessionStart` 把開場包放進 `additional_context`。收錄預設關，`consent --platform cursor yes` 在終端機親手打「我同意」。Antigravity 只接 `SessionStart`（開場包放進 `injectSteps`），不收字、沒有同意書；它的 stdin 沒有事件名，hook 指令要加 `--event SessionStart`。本人自己把 hook 指令填進該工具的設定檔（Cursor `.cursor/hooks.json`，Antigravity `.agents/hooks.json`），外掛不寫。

## 只停用一個專案

Claude Code：執行 `python3 <外掛>/scripts/apersona_hook.py --project-state off --project-directory <專案目錄>`；Codex：執行 `python <外掛>/scripts/apersona_codex.py off --project <專案目錄>`。把 `off` 換成 `on` 可重新開啟。路徑含空白時加引號。

開關寫在該目錄的 `.apersona.json`，套用到子目錄；父目錄停用時，子目錄不能自行覆蓋。停用會阻止開場、每輪比對與自動收錄，不影響其他專案，也不登出帳號。它只控制外掛 hook，不會取消手動 MCP 呼叫或已授權的手動送檔；要撤銷整台授權請登出。

重新開啟只收錄這個時間之後的新內容，不補送停用期間的對話。Codex 待送內容帶本機專案來源，Stop 會再次核對；另一專案的 Stop 也不能送出已停用專案的內容。升級前沒有專案來源的舊暫存保持待送，不猜來源；撤回收錄同意可清除。專案路徑不會加入伺服器收錄請求。這個開關不代替收錄同意，也不能打開已關閉的整台 hook。
