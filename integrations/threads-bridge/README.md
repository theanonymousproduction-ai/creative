# Threads 接駁：預覽及明確確認發布

版本：2026-10-20預覽及確認發布版。部署前請確認Render已取得本版本；PUBLISH_ENABLED預設關閉。沒有實際發布測試，不宣稱已驗證發布權限。

## 功能
- GET /health：公開健康檢查。
- GET /：自帶繁體中文操作頁；密鑰用password欄，不寫入網址、localStorage或cookie，不需DevTools。
- GET /account、GET /history：Bearer BRIDGE_API_KEY保護。
- POST /preview：同源、密鑰認證、500單位內文字、核對預期帳戶後回傳簽名預覽；不建立Meta容器或發布。
- POST /publish：開關啟用、同源、密鑰、明確approved=true及十分鐘內有效簽名預覽才處理。確認綁定帳戶與原文，不能預覽A卻改送B。
- Meta流程：建立TEXT容器、輪詢FINISHED、發布容器、讀permalink。任何發送結果不明不自動再發；連結查詢失敗也不重發。
- 不支援排程、圖片、串文或長文附件；600字故事不可直接送單帖。程式是否開啟不是Meta授予權限的證據，首次實測仍可被Meta拒絕。

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
`python -m unittest discover -s integrations/threads-bridge -p 'test_*.py' -v`（repo根目錄）。13項離線測試，Meta被mock，沒有用真實Token。
已涵蓋預設禁止發布、明確批准、預覽綁定與過期、帳戶變動、超長文字、同文與併發去重、結果不明、容器失敗、連結失敗不重發、HTTP認證與來源、上游錯誤不回傳密鑰。

官方參考（2026-10-20本輪讀取）：
- https://developers.facebook.com/documentation/threads/posts/ ：兩階段發布及500字單帖限制。
- https://developers.facebook.com/docs/threads/retrieve-and-discover-posts/retrieve-posts/ ：permalink。
- 目前已通過讀帳戶的graph.threads.net/v1.0沿用；若Meta調整API，需核對官方及實際錯誤，不能假裝已測試發帖。
