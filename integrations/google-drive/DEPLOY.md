# 免費 Render 部署：唯讀連線第一階段

此程式尚未驗證真實 Google 授權；離線測試不等於已接通。

## 先取得服務網址

在 Render 新建獨立 Web Service，選 creative repository、main：

- Root Directory: `integrations/google-drive`
- Runtime: Python 3
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 server:app`
- Health Check Path: `/health`
- Instance: Free；若帳戶沒有免費選項，停止，不選付費方案。

不開啟 access log：OAuth 回呼的 query string 含一次性 code。不要開 debug。
服務可在 Google 設定未填妥時先啟動；只有 /health 和登入介紹頁可用。
單一 worker 是 OAuth 一次性 state 存在記憶體的必要條件。部署或休眠重啟期間的登入可能失效，須重新開始。

## 取得真實網址之後

1. 將服務的 HTTPS 網址（無結尾斜線）設為 `PUBLIC_ORIGIN`。
2. Google OAuth Web client 的 Authorized redirect URIs 填 `PUBLIC_ORIGIN` 加上 `/oauth/callback`。
3. Authorized JavaScript origins 留空；此服務走伺服器端 OAuth。
4. 建立 client，把 client ID、client secret 分別存到 Render 的 `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`，不要貼到聊天或 repo。
5. 在 Render 產生至少 32 字元的 `DRIVE_BRIDGE_API_KEY`。
6. `GOOGLE_TOKEN_KEY` 必須是 Fernet 格式：32 bytes 隨機資料的 URL-safe Base64。可在可信任本機用 Python `from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())` 產生並只存到秘密設定。不可任意使用不符合格式的 Generate 字串。
7. `GOOGLE_FOLDER_ID` 填指定資料夾 ID。

登入後頁面提供加密的 `GOOGLE_TOKEN_BUNDLE`，一次性存回同一 Render 服務環境變數。保留 token key；兩者都不提交 repo。重啟後以 /folder 驗證。此版本授權 drive.readonly，可讀帳戶 Drive，但對外只開放指定資料夾第一層的名稱及中繼資料；不下載文件或修改任何檔案。

## Codex 的接駁仍需設定

獨立服務啟動不代表此 Codex 已獲憑證。把相同 `DRIVE_BRIDGE_API_KEY` 存入此 Codex 環境的秘密設定，目的地限制為新服務實際 hostname，再以 Authorization: Bearer 呼叫 /folder。不要把 key 當一般公開環境變數或放進指令文字紀錄。必須實際查到資料夾，才算首次連線完成。

## 尚未完成的下一階段

Google Docs／Sheets 寫入尚未實作。需另行決定 scopes、檔案授權範圍及更新衝突處理；不能把唯讀連線稱為完整文件管理完成。
外部 OAuth app 的 Testing 模式，要求 Drive scope 時 refresh token 通常七天到期。長期使用需處理發布狀態與 Google 適用規則；不可承諾永遠只登入一次。免費 Render 休眠及帳戶總用量限制仍適用。
