# Threads 接駁：預覽及明確確認發布

版本：2026-10-20預覽及確認發布版。部署前請確認Render已取得本版本；PUBLISH_ENABLED預設關閉。沒有實際發布測試，不宣稱已驗證發布權限。

## 功能
- GET /health：公開健康檢查。
- GET /：自帶繁體中文操作頁；密鑰用password欄，不寫入網址、localStorage或cookie，不需DevTools。
- GET /account、GET /history：Bearer BRIDGE_API_KEY保護。
- POST /preview：同源、密鑰認證、500單位內文字、核對預期帳戶後回傳簽名預覽；不建立Meta容器或發布。
- POST /publish：開關啟用、同源、密鑰、明確approved=true及十分鐘內有效簽名預覽才處理。確認綁定帳戶與原文，不能預覽A卻改送B。
- Meta流程：建立TEXT容器、輪詢FINISHED、發布容器、讀permalink。任何發送結果不明不自動再發；連結查詢失敗也不重發。
- 支援500字內主帖及可選10,000單位內長文附文，可保留600字故事全文；不支援排程、圖片或串文。程式是否開啟不是Meta授予權限的證據，首次實測仍可被Meta拒絕。

## Render設定
程式檔server.py及publisher.html必須放在同一目錄。Runtime Python3，Branch main，Root Directory `integrations/threads-bridge`，Build `python -m compileall -q server.py`，Start `python server.py`，Health `/health`，免費方案可先測。

保留兩個現有秘密：
- THREADS_ACCESS_TOKEN：Meta帳戶Token，不放Chat、GitHub或備份。
- BRIDGE_API_KEY：Render產生的服務密鑰，不是Meta App Secret；只輸入你自己的服務頁。

新增三個非秘密設定：
- THREADS_EXPECTED_USERNAME=`wannasaybutdidnt`，避免連錯帳戶。
- PUBLIC_ORIGIN=`https://creative-hl3h.onrender.com`，不含末尾斜線。
- PUBLISH_ENABLED=`false`，先保持發布關閉；完成預覽檢查後，使用者決定進行一次發布測試時才改`true`。

PUBLISH_STATE_PATH可選，預設/tmp/threads-publications.sqlite3。程式不需要App Secret或OAuth回呼；現階段採Meta產生的測試Token。憑證仍會到期，需按官方機制更新，沒有自動換Token或排程執行。

## 第一次測試
1. 核對GitHub新版本含兩個執行檔，Render手動部署最新commit；/health回version=preview-publish-v1。
2. 打開服務根網址，輸入BRIDGE_API_KEY，按核對帳戶。應為預期帳戶。
3. 輸入使用者想測試的短文（尚未批准的測試文不代寫作已批准），產生預覽；此時開關false，發布按鈕不可用，核對預覽完全一致。
4. 使用者決定指定帳戶、指定全文及立即公開發布後才改PUBLISH_ENABLED=true並重啟服務。此動作本身仍不發帖。
5. 回頁面重新預覽，逐字檢查帳戶、文字，勾選批准並按確認立即公開發布，再接受確認對話框。這時才真正出帖。
6. 保留成功返回post_id及permalink，在Threads查看實際內容，將版本、平台、香港時間、連結及依據記總表。沒有連結仍可能已發布，先查帳戶與服務紀錄，勿自動再試。
7. 若只做一次測試，完成後可把PUBLISH_ENABLED改回false。

## 防重複範圍與限制
SQLite以帳戶+文字hash作唯一鍵。併發或同文再送不重發；結果不明標uncertain並禁止同文再送。每次顯示目前可讀紀錄；只保留hash、狀態、ID和時間，不保存憑證或原文。
**免費Render主機重啟、休眠或部署可能清除本地狀態，不提供跨此類事件的永久去重保證。** 本階段不能用作無人值守自動發布。重新部署／重啟後先核對Threads真實內容，不以重新預覽繞過結果不明。正式定期發布前需接持久資料庫及恢復／核對機制，並另外接排程與批准版本佇列。

此頁面是由使用者以自己持有的密鑰操作。Codex仍未自動取得主機秘密或發布工具，不等於聊天可直接代發。往後需安全工具連接，而不是把BRIDGE_API_KEY貼入對話。

## 檢查
`python -m unittest discover -s integrations/threads-bridge -p 'test_*.py' -v`（repo根目錄）。20項離線測試，Meta被mock，沒有用真實Token。
已涵蓋預設禁止發布、明確批准、預覽綁定與過期、帳戶變動、超長文字、同文與併發去重、結果不明、容器失敗、連結失敗不重發、HTTP認證與來源、上游錯誤不回傳密鑰。

官方參考（2026-10-20本輪讀取）：
- https://developers.facebook.com/documentation/threads/posts/ ：兩階段發布及500字單帖限制。
- https://developers.facebook.com/docs/threads/retrieve-and-discover-posts/retrieve-posts/ ：permalink。
- 目前已通過讀帳戶的graph.threads.net/v1.0沿用；若Meta調整API，需核對官方及實際錯誤，不能假裝已測試發帖。

## Chat批准後直接發布（第二階段）

正式流程不要求使用者逐篇開本頁：Manager在Chat交付主帖、長文附文、帳戶及版本，使用者批准後，Manager才建立一個新的approved-threads/*.json工作檔並提交main。GitHub自動工作執行scripts/publish_chat_approved.py，讀取runner秘密呼叫Render預覽並核對完全一致後發布，回傳receipt及永久GitHub deployment嘗試紀錄。

### 一次性設定（尚待完成及端到端驗證）
1. Repository Settings → Secrets and variables → Actions，新增Repository secret `THREADS_BRIDGE_API_KEY`，值是Render既有BRIDGE_API_KEY。不是Threads Token，不把值送到Chat。現有Codex連接無權替使用者讀取或設定此管理項目。
2. GitHub須有.github/workflows/publish-chat-approved.yml，Actions允許執行；程式指定contents:read與deployments:write，不能改成只讀deployments。
3. Render部署此版，保留THREADS_ACCESS_TOKEN、BRIDGE_API_KEY、THREADS_EXPECTED_USERNAME、PUBLIC_ORIGIN；測試指定版本準備好後一次性設PUBLISH_ENABLED=true，之後每篇由Chat批准工作檔控制，不再逐篇改開關。
4. 第一個Chat已批准短文工作檔跑完後，核對實際Threads內容及回傳；未成功前不稱整條鏈已接通。600字可用主帖+長文附文，附文原封保留全文，先在Chat顯示形式及全文才批准。

### 永久去重與執行回傳
GitHub workflow只處理一次commit新增的一個已批准工作檔，全部發布任務串行；修改舊檔不觸發新發布。以帳戶、主帖與附文的hash建立唯一environment對應的deployment嘗試紀錄，發送前建立；即使Render重啟，舊嘗試仍擋重送。任何結果不明先停，不自動重發。GitHub紀錄也可能由管理者刪除，故仍須保留receipt及核對真實貼文，並非不可遺失的保證。

工作結果保存在Actions artifact `threads-publication-receipt`（90日）及deployment狀態；Manager取得後寫回內容總表。提交工作檔不等於已發布，排隊／成功／不明分開。沒有Actions授權或secret時會停，不公開秘密也不降級為無認證端點。

**此repo公開，approved-threads工作檔中的已批准主帖及附文也會成為公開repository內容。** 未經批准的草稿不得提交該目錄；若使用者不接受這種持久公開紀錄，須先改用私人repository／私人佇列再啟用，不能假稱GitHub是私人傳輸。

### 工作檔格式（不要直接用這段當批准內容）
schema=1、approved=true、account、approval_ref（固定作品及版本）、approved_at（Unix秒，24小時內）、text（主帖）、attachment（可空，長文全文）、sha256。sha256以scripts/publish_chat_approved.py的fingerprint計算，需精確匹配Chat已批准版本。不得因排程到點或一般「好」而自行新增工作檔；本次功能設定同意不是測試貼文批准。

20項離線測試已通過，涵蓋長文與Chat交接、跨主機重啟的外部嘗試紀錄、完整版本比對及不明結果停送。尚未以真實貼文驗證API、GitHub Actions及主機全部權限。
