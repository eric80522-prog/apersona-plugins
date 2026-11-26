# Apersona（Claude Code 外掛）

兩行裝好，依序貼：

    claude plugin marketplace add eric80522-prog/apersona-plugins
    claude plugin install apersona@apersona

Claude Code 不會自己更新第三方外掛庫的外掛（官方預設只有 Anthropic 自己的庫自動更新）。要自動更新：在 Claude Code 打 `/plugin`，到 Marketplaces 分頁選 `apersona`，選 Enable auto-update。沒打開時手動更新：`claude plugin marketplace update apersona`，再 `claude plugin update apersona@apersona`，重開 Claude Code。外掛比網站要的舊時，開場包第一行會寫「〈外掛有新版〉」；太舊的網站不接，回 `plugin_too_old`。

裝完先在 Claude Code 打 `/apersona:login`，照畫面上的網址與代碼到瀏覽器按「允許」（裝置碼登入，開場包的 hook 靠它）。授權頁會印出這台連到的人格名稱（帳號有好幾個人格時可以選），並寫明：只有這台電腦上的外掛，用你的帳號做查詢、回報、上傳與修改，別人不能用。

0.2.3 起，Claude Code 外掛內的 MCP 工具與 hook 共用這次裝置登入。外掛用本機 Python 標準函式庫將 stdio JSON-RPC 轉送到網站 `/mcp`，不另開第二筆 OAuth 授權。登入存在這台的 `~/.apersona/plugin-credentials.json`（資料夾 700、檔案 600；Windows 沿用資料夾權限）。沒登入時 hook 不擋提問，MCP 回覆需要先登入；已安裝舊版者更新外掛後重新開啟 Claude Code，讓它重新載入 MCP 設定。

Claude Code 的 `PostToolUse` 只從成功、完整的 Apersona recall 工具結果取出對話識別與收件識別，以同一裝置登入確認收到。不是從使用者或模型自己寫的文字取識別；錯誤、截斷或未知結果格式不確認。Codex 與純遠端連接器沒有這條自動確認路徑，必須由同一憑證的 MCP 客戶端在 recall 的 `received` 帶回收件識別，並沿用回覆的 `conversation_id`。不同裝置或不同 OAuth 授權不能替彼此確認。

這條接法已以隔離本機服務與 Python 3.9 驗證；Claude Code 真平台的事件格式、更新後掛載與重連仍需實機驗收，不能把本機模擬當作已驗收。事件契約依 [Claude Code hooks 文件](https://code.claude.com/docs/en/hooks)。

Claude.ai／ChatGPT／Grok 這類純連接器（只加遠端 MCP、你的電腦上沒有我們的 hook）：加 `https://www.apersonaai.com/mcp`，登入按允許即可。授權頁預設只給「查詢與回報」：問你的人格、回報結果，你在對話裡說的更正也照常記（但不會因此啟動要扣款的習慣分析）。要讓它上傳素材、改回覆長短設定、刪除一輪，在授權頁另外勾「也讓它上傳資料與修改」。沒勾的連接器叫 `upload_files` 這類工具會被擋，回一句白話（`connector_read_only`）說明到網站「設定」頁的「授權裝置與連線」按「開啟…的上傳與修改」；那裡隨時能開或關。本機工具（Claude Code、Codex、Cursor 這類有 hook 的）按允許就是全開，不用勾。這個分別是看登記時的轉址網址（本機位址＝有 hook），不是看工具自稱的名字；這個範圍上線前授權的裝置維持原有權限。

## 開場與記憶

Claude Code 開新對話會自動拿開場包。需要相關記憶時，助理使用 `recall`，判讀用 `lookup`。Codex CLI 用下方的可選 hooks 取開場與每輪提示。Cursor 若需要自動開場，用 `python plugin/scripts/apersona_opening.py install`，重開後生效；仍可獨立用遠端 MCP＋OAuth。

開場 hook 只帶對話識別和專案雜湊，向網站取開場資料。用 `/apersona:off` 停用，`/apersona:on` 恢復，可只對目前專案操作。開發工具見 [Apersona 輔助開發外掛](https://github.com/eric80522-prog/apersona-dev-plugin)。

## 送資料夾：`/apersona:read <素材資料夾>`

切段、能不能用、估價都在網站上跑（跟網站上傳框同一套），外掛只送原始檔；送之前問你一次要不要送，讀之前再問你一次要不要花錢讀（那一句寫出預估與最多花多少，你要親口說出確認碼，AI 不能代為同意）：

1. `ask`：只在本機列出要送哪些檔、各多大、共幾份、哪些不送與原因，什麼都不上傳。照 `.gitignore`／`.apersonaignore`（語法同 `.gitignore`）挑檔；`.git`、`node_modules`、`.env*` 與捷徑（symlink）一律不送。指向整個家目錄或超過 200 份時，先停下來問你。
2. 你說好（「要把這幾份送上網站嗎？」；送上去只保存、還不讀），才 `send`：送的就是剛才列的那些檔（之後改過就停下重問）。送前在本機遮秘密（金鑰、密碼、通行證；JSON 裡鍵名像 password、secret、token、api key、credential、private key、auth 的值整個換掉），其餘照原樣送（人名、電話、Email 不換）。Big5 的舊筆記先在本機轉成 UTF-8、遮過秘密再送；`.docx`、`.pdf` 原檔照送，由網站遮秘密。每份檔用隨機名字上傳，路徑只留在本機。網站把讀進來的每份檔整份文字（秘密已遮）存著，只存、不讀；讀的是切好的段，聊天裡你每則訊息前後別人的話（前面最多 3 則、後面 1 則，每則最多 300 字，連同說話者名字）一起上傳當背景，不算你說的。網站回「收到幾份、幾段能讀、幾段讀不了（原因）」與要讀的那一句（「讀這 N 份，預估 US$X，最多 US$Y」和確認碼）；你親口同意並說出確認碼，才 `consent yes --words "<你那句話>"` 排進讀取（目前只開放測試帳號讀取，其他帳號送上去只保存、不讀）。
3. 你在網站上刪過的段落又出現在送的檔裡，那些段先不收（不讀、不用、不給出去）；結果會說「這幾份裡有刪過的內容，這次先不收」和是哪幾份。你說出要收回哪一份，才 `send --take-deleted "<那份的路徑>"`，那些內容當成新的來源，一樣要你對讀的那一句同意才讀；也可以到網站資料頁恢復刪掉的那份來源。
4. 不給資料夾（`/apersona:read` 或 `/apersona:read ai`）就是送這台的 AI 對話紀錄：一次找齊 Claude Code 存的對話（`~/.claude/projects`，子代理的不算）與你親手打的 Codex 對話（`~/.codex/sessions`；`codex exec` 與子代理的不送），按工具列幾份、多大，問法同上；原檔照送（秘密先遮），哪句是你打的、哪些是 AI 或工具輸出由網站分。
5. 你說不要，什麼都不送、不讀。`status` 看讀到哪；`cancel` 讓還沒讀的段不讀、已上傳的保留；送完後改變心意可用 `consent no`，網站就不讀已收的檔。

本機工作資料夾在 `~/.apersona/intake/<資料夾路徑雜湊>/`（700，裡面的檔 600），不放進你的素材資料夾（那裡可能被雲端同步或 git 提交）。暫時的錯誤（斷線、5xx、429）自己等一下重試最多 3 次；永久的錯誤不重試、用白話說原因和下一步。遮秘密與排除規則（`scripts/apersona_mask.py`、`scripts/apersona_ignore.py`）與網站收檔同一套規則。

## 指令

- `/apersona:login`：登入這台（開場包、送原始檔都用它）。`python3 <外掛>/scripts/apersona_login.py status` 看有沒有登入（快到期的通行證也在這裡換新）。
- `/apersona:logout`：向網站撤銷這台的登入，並刪掉 `~/.apersona/plugin-credentials.json`。
- `/apersona:off` 停用開場 hook，`/apersona:on` 恢復。
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

Antigravity 的 SessionStart／PreInvocation 只注入簡短工具呼叫指引，不發網路請求、不讀 transcript、不儲存使用者輸入；不是已取回本人的開場包，也不強制每輪重拿。沒有 Stop handler，因此不攔截停止。Gemini 包沒有 hook。Antigravity `lastUserInput` 是最新輸入，`invocationNum` 是模型呼叫次數，不能當穩定的人類輪次；已有手動接線的 Antigravity 使用者可用 `python scripts/apersona_opening.py run --platform antigravity --event SessionStart`，開場由本機裝置登入取得。停用或移除請用宿主的外掛管理；Gemini 可用 `gemini extensions disable apersona`／`gemini extensions uninstall apersona`。移除外掛不等於撤銷 OAuth，需在網站「授權裝置與連線」另行撤銷。

格式依 [Antigravity Build Plugins](https://antigravity.google/docs/build-plugins)、[Antigravity MCP](https://antigravity.google/docs/mcp)、[Gemini extension reference](https://geminicli.com/docs/extensions/reference/) 與 [Gemini MCP](https://geminicli.com/docs/tools/mcp-server/)。MCP instructions 的宿主處理不同：Gemini CLI 會加入系統指引；Antigravity 的延遲探索會存 instructions.md，不能據此宣稱每輪自動呼叫；Google ADK 需應用端另外接開場指引，不能當成 Gemini CLI extension。

## Codex 桌面：原生外掛

`codex/` 是獨立的 Codex 外掛：一個接入 skill 加遠端 MCP，登入一次 OAuth 即可查詢。它不載入 Claude 的 hooks、不需要 Python 或 Bash，也不要求第二次裝置碼登入。安裝後執行外掛的 setup，讓 Codex 接續目前的任務、取得開場並查本人原話；只有安裝或授權成功還不算完成，必須實際取得 `opening` 回覆。

從公開 marketplace 安裝（Codex CLI 與桌面使用同一台的設定）：

    codex plugin marketplace add https://github.com/eric80522-prog/apersona-plugins.git
    codex plugin add apersona@apersona

回桌面重新載入外掛，選 Apersona 執行 setup；需要認證時由本人完成該連線的 OAuth。對話中說「用 Apersona 幫我處理這件事」即可。若已經手動加過同站 MCP，先確認使用哪一條連線，避免重複安裝與授權。

開場與查詢由 skill 呼叫 MCP。收件確認使用同一連線的 `recall.received`，不宣稱 Codex 自動 ACK。桌面附帶 CLI 的隔離安裝與真人 OAuth／首問是兩項不同的驗證。

## Codex CLI：手動 MCP 與可選 hook

`python plugin/scripts/apersona_codex.py install` 裝 SessionStart 與 UserPromptSubmit，在 Codex `/hooks` 信任安裝的兩個 hooks。手動把遠端 MCP 加進 Codex 並由本人走 OAuth；安裝程式會列出指令。用同一支程式的 `status`、`on`、`off` 或 `uninstall` 管理 hooks。


## 只停用一個專案

Claude Code：執行 `python3 <外掛>/scripts/apersona_hook.py --switch off --project-directory <專案目錄>`；Codex：執行 `python <外掛>/scripts/apersona_codex.py off --project <專案目錄>`。把 `off` 換成 `on` 可重新開啟。路徑含空白時加引號。

開關寫在該目錄的 `.apersona.json`，套用到子目錄；父目錄停用時，子目錄不能自行覆蓋。停用會阻止開場與每輪比對，不影響其他專案，也不登出帳號。它只控制外掛 hook，不會取消手動 MCP 呼叫或已授權的手動送檔；要撤銷整台授權請登出。
