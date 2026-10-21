# 在目前 Codex 對話直接連接 Google Drive

狀態：唯讀接駁第一版已實作並通過離線測試；尚未部署或通過真實 Google 授權。部署步驟見 DEPLOY.md。寫入 Docs／Sheets 尚未實作。

## 已確認需求

- 所有日常工作留在目前 Codex 對話。
- 目標資料夾：`1_ho3hHWrG8txOcoUi6R34xnKfVVivEIE`。
- 先只讀驗證資料夾名稱和可見檔案；建立及更新文件須按使用者指示執行。
- 不依賴另一個 Chat，也不使用其登入資料。

## 接駁方案

使用 Google 官方 OAuth 授權，透過獨立服務提供 Drive、Docs、Sheets 操作。Codex 使用服務的驗證憑證呼叫；Google refresh token 只儲存在服務端秘密設定，不能放入 GitHub、聊天或紀錄。

不修改目前 Threads 發布服務的路由或秘密設定，避免影響已工作的發布流程。

## 必要前置工作

Google 帳戶擁有人在 Google Cloud 建立或選擇專案，啟用 Drive、Docs、Sheets API，設定 OAuth 同意畫面與 Web application client。回呼網址需在選定服務網址後精確登記，不使用範例網址作正式設定。

使用者須親自完成一次 Google 登入及同意授權；ChatGPT 插件的既有授權不能當作此服務的憑證。OAuth 測試模式的 refresh token 可能有時限，完成部署前必須確認適用的發布狀態和授權限制，不能承諾永不需重新授權。

選取 scopes 前先確認是否要管理既有檔案。`drive.file` 不能直接當成可存取使用者全部既有文件的權限；若要較廣 scopes，應向使用者列明權限範圍。服務端必須驗證目標檔案位於指定資料夾，不能只信任呼叫者傳來的檔案 ID。

## 驗收

1. OAuth 回呼驗證一次性 state，服務不記錄 token。
2. Codex 經驗證的服務請求讀取指定資料夾，回報真實名稱與檔案。
3. 使用者指示測試寫入後，建立測試文件、更新相同 ID、讀回核對、確認所屬資料夾。
4. 測試授權失效與拒絕資料夾外檔案，確認錯誤不洩露秘密。
5. 只有實際成功後才標示「已接通」。

目前阻礙：本對話沒有 Google 管理介面或已授權的 Google API 身分，不能代替帳戶擁有人完成 Google Cloud 設定及同意流程。
