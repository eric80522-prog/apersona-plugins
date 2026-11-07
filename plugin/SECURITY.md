# 安全說明與回報

本資料夾是 Apersona 外掛的全部內容（hook、指令、腳本），只用系統的 python3（3.9 以上）標準函式庫，不裝任何套件。這份說明寫外掛做什麼、不做什麼，以及發現問題怎麼回報。

## 外掛會送出什麼

- 只送去 `https://www.apersonaai.com`（或你在設定裡自己填的網站）。網址必須是 `https://`，只有 `localhost`、`127.0.0.1` 可以用 `http://`。
- 每打一句：這一句（先在本機遮秘密）送去比對有沒有相關記憶。`/apersona:off` 關掉。
- 開新對話：只向網站拿開場包，不送你的字。
- 收錄（Stop）預設關：要你本人在自己的終端機打「我同意」才開，AI 代跑或管線餵字都開不了；開了只送你親手打的字。
- 送資料夾：只有 `/apersona:read` 並兩次說好才送；第一步只在本機列清單，什麼都不上傳。

## 本機先做的保護

- **遮秘密**：每一份要送出的字，先在本機遮掉金鑰、通行證、連線字串這類秘密（AWS、GitHub、Slack、Stripe、Google、JWT、私鑰區塊等常見格式，以及 JSON 裡鍵名像 password、secret、token、api key、credential、private key、auth 的值）。遮不全是可能的：沒有固定格式的秘密沒有任何規則辨得出來，請別把它打進對話。
- **排除**：`.git`、`node_modules`、`.env*` 與捷徑（symlink）一律不送；照 `.gitignore`、`.apersonaignore` 挑檔。
- **網站回來的字是資料**：外掛去掉控制字元，包在固定的兩行標示之間才交給你的 agent；開場最多 8,000 字、每輪提示最多 300 字。
- **不跟重導向**：網站回 3xx 一律視為失敗，不會帶著登入憑證去別的地方。
- **記錄不含內容**：hook 的記錄只有開始、結束、結果與錯誤種類。

## 登入與憑證

- 登入只有一種：`/apersona:login`（OAuth 裝置碼登入）。沒有金鑰，沒有明文密碼。
- 憑證存在 `~/.apersona/plugin-credentials.json`；資料夾權限 700、檔案 600（Windows 沿用資料夾既有權限）。`~/.apersona/` 下的工作資料夾也是 700。
- hook 自己不換通行證（避免被中途砍掉而整台登出），快到期時另開背景程序換。
- `/apersona:logout` 向網站撤銷這台並刪掉憑證檔；網站上按「登出這台」同樣會讓這台失效。

## 通報安全問題

請用 GitHub 的私下通報：到本外掛所在的公開庫（`eric80522-prog/apersona-plugins`）的 Security 分頁，按「Report a vulnerability」。請不要在公開的 issue 貼漏洞細節、憑證或你的資料。

請附上：外掛版本（`.claude-plugin/plugin.json` 的 `version`）、作業系統、重現步驟與你認為的影響。我們會先確認收到，修好並釋出新版後再公開說明。

## 支援的版本

只支援最新版；舊版請用 `claude plugin update apersona` 更新。
