# Threads唯讀海巡接駁

新增認證GET `/read`，operation只能為search、post、replies、own_posts。search接Meta `/keyword_search`，q最多80字，limit 1–20，search_type RECENT/TOP；post/replies只接受數字post_id。帳戶仍核對THREADS_EXPECTED_USERNAME，沒有增加公開讀取或寫入端點。`/health`版本為discovery-read-v4。
回傳僅id/text/username/permalink/timestamp/media_type；不轉傳含Token的paging URL，不跟隨上游next。只查一頁，has_more供提示；空結果或僅本帳戶貼文不能證明公開搜尋已接通。程式回public_discovery_verified，只有本次search確實讀到其他username才為true。
新workflow `.github/workflows/threads-read-only.yml` 只在main新增 `threads-read-requests/*.json` 時跑，執行 `scripts/read_threads.py`。只有contents:read、沒有發布或部署權限；沿用Actions現有THREADS_BRIDGE_API_KEY，Meta Token繼續只在Render。結果存artifact threads-read-only-result七日。請求檔及Actions紀錄在公開repo可見，只用已授權公開話題／公開貼文，不放私人內容或秘密。這不是定時海巡，也不會發留言。
範例唯讀工作檔：`{"schema":1,"account":"luen.dayori","operation":"search","q":"戀愛腦","limit":5,"search_type":"RECENT"}`；讀單帖改operation=post及post_id，沒有q/limit。只允許固定操作，不允許任意URL、方法、發布文字或Token參數。
正式回覆沿現有Chat批准→approved-threads流程，reply_to_id簽進預覽及永久去重hash；必須先實讀原帖、記作者/ID/連結與確切回覆文字，取得針對該回覆或明確批次範圍的授權，才提交發布工作檔。這次接駁授權不包含測試留言或自動海巡；不重啟任何暫停排程。

Meta權限與限制：搜尋需要threads_keyword_search；公開陌生帳戶搜尋還涉及App Review及Advanced Access，單有Token scope未必足夠。未獲批准可能只搜本人貼文；讀回覆另需相應threads_read_replies存取，不能保證能讀任意他人帖的留言。程式部署不等於Meta權限成功。以實際唯讀測試及回執確認，未成功如實標pending/blocked。既有reply_to_id已曾用於自己的續文，但外部貼文回覆仍需按原帖回覆設定與實際結果驗證，不能冒稱本輪發過測試留言。
官方端點例：https://www.postman.com/meta/threads/request/34203612-b3b2c12a-7ce6-4d86-a3c6-6d31e3b66ea1
Meta範例庫權限說明：https://github.com/fbsamples/threads_api/issues/72 （Contributor對Advanced Access與僅本人搜尋的說明；非Token狀態證據）。官方主文件讀取429，使用官方Postman端點及範例庫討論有限核對，不宣稱已讀受阻頁。
30項離線測試通過，實際Render部署及Meta搜尋權限待唯讀測試，不包含發帖或自動互動測試。
