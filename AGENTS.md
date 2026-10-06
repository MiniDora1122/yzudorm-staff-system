# AGENTS.md — 宿舍工讀生系統

## 1. 專案目的

本專案為「宿舍工讀生系統」，提供宿舍服務單位的管理員與工讀生使用。

目前專案已有一份 `main.html` 管理員介面雛形，包含：
- 月曆式排班檢視
- 辦公室 / 管理中心地點篩選
- 新增與刪除排班
- 同地點同時段重複排班檢查
- 同一工讀生時間重疊檢查
- 工讀生基本資料名冊
- 外籍工讀生工作證與居留證期限欄位
- 當月工讀時數與薪資統計

請保留上述可用邏輯與操作概念，不要直接全部推翻重寫；若為了完整系統架構需要重構，可以拆分前端、後端、資料庫，但應盡量延續現有 UX 與排班規則。

---

## 2. 開發原則

1. 先閱讀與本次任務直接相關的檔案及其呼叫鏈。若涉及安全、資料模型、交易、備份、跨模組行為或全域重構，才進行 repository-wide review；不得因未閱讀與任務無關的檔案而阻止低風險工作。
2. 不要一次做超大型重寫。請分階段完成，每個階段都必須可以執行。
3. 優先確保：
   - 資料正確
   - 權限安全
   - 手機與電腦皆可使用
   - 操作直覺
   - 維護容易
4. UI 語言以繁體中文（zh-TW）為主。
5. 保持現有 FullCalendar 的月曆式排班體驗，可改善 UI，但不要失去核心功能。
6. 所有日期與時間以 Asia/Taipei 為預設時區。
7. 所有密碼不可明文儲存。
8. 工作證、居留證、證件號碼與上傳影像屬敏感資料，禁止寫入 log。
9. 產品中的刪除、核准、拒絕等重要操作，必須有使用者確認、權限驗證、CSRF 保護及清楚的結果提示。此規則不代表 Agent 在修改相關程式或執行本機測試前必須再次詢問使用者。

### 自主執行與確認邊界

使用者已明確要求實作時，以下屬於一般實作範圍，可直接執行：
- 修改專案內程式碼、模板、樣式與測試
- 建立或更新 migration
- 執行本機測試、lint、建置與可逆的本機驗證
- 修正與本次任務直接相關的資料模型、route、UI 與文件

以下情況需先取得使用者確認：
- 刪除正式資料、正式檔案或不可逆的歷史紀錄
- 修改 production database 或正式環境設定
- 部署、推送遠端 repository、發布安裝檔
- 將敏感資料傳送到外部服務
- 停止或重啟正在提供服務的正式程序
- 涉及薪資、法規、個資政策的重大行為變更

只有在需求模糊且不同解讀會改變資料、安全性、權限或使用者可見行為時，才需要詢問澄清；否則採取最小、可逆且符合既有規則的預設。
10. 先完成核心功能與測試，再做額外花俏功能。

---

## 3. 建議技術架構

若現有 repo 尚未有後端，請優先採用容易維護的小型全端架構：

### 建議方案
- Backend: Python Flask
- ORM: SQLAlchemy
- Database: SQLite（開發環境）
- Production DB: 架構需可透過 `DATABASE_URL` 切換 PostgreSQL
- Frontend: Jinja2 + Bootstrap 5 + FullCalendar
- JavaScript: Vanilla JS；不要在沒有必要時引入大型前端框架
- Authentication: Server-side session
- Password hashing: Argon2 或 bcrypt
- Migration: Flask-Migrate / Alembic
- Tests: pytest

如果 repo 已存在其他成熟框架，請延續現有框架，不要為了符合本段而強制改寫。

---

## 4. 使用者角色與權限

系統至少有兩種角色：

### ADMIN — 管理員
可以：
- 登入 / 登出
- 查看全部排班
- 新增、修改、刪除排班
- 查看與維護全部工讀生
- 查看請假申請
- 核准 / 拒絕請假
- 查看換班申請
- 核准 / 拒絕換班
- 查看每位工讀生當月總時數
- 匯出當月工讀時數報表
- 查看工作證 / 居留證效期
- 查看證件即將到期提醒

### STUDENT — 工讀生
只可以：
- 登入 / 登出
- 查看自己的排班
- 查看自己的當月總時數
- 提出請假申請
- 取消尚未處理的請假申請
- 提出換班申請
- 接受 / 拒絕其他工讀生對自己的換班邀請
- 查看自己的換班進度
- 修改自己的可修改基本資料
- 修改自己的密碼
- 上傳自己的工作證 / 居留證照片

STUDENT 絕對不能：
- 查看其他人的完整個資
- 修改其他人的資料
- 查看其他人的證件照片 / 證號
- 自行核准請假或換班
- 直接修改已正式發布的排班

所有權限必須由後端驗證，不能只靠前端隱藏按鈕。

---

## 5. 登入 / 登出

需要真正的登入系統，不是前端假登入。

### 必要功能
- 帳號登入
- 密碼登入
- 登出
- Session timeout
- 未登入不得進入內頁
- ADMIN / STUDENT RBAC
- 修改密碼
- 新增工讀生時可建立登入帳號
- 管理員可重設工讀生密碼為一次性臨時密碼
- 使用臨時密碼登入後要求更改密碼

### 安全要求
- 密碼使用 Argon2 或 bcrypt hash
- Cookie 至少使用 HttpOnly、SameSite
- Production 使用 Secure cookie
- POST / PUT / DELETE 要有 CSRF 保護或等效機制
- 不在前端 JavaScript 內儲存真實密碼或登入憑證

---

## 6. 管理員介面

請將目前單頁 tab 介面改善為清楚的管理後台，可使用左側 sidebar 或上方 navigation。

### 建議主要選單
1. 儀表板
2. 排班管理
3. 請假 / 換班
4. 工讀生管理
5. 時數報表
6. 帳號 / 登出

### 6.1 儀表板
顯示：
- 本月排班總數
- 待審請假數
- 待審換班數
- 工作證 30 / 60 天內到期人數
- 居留證 30 / 60 天內到期人數
- 今日值班人員

### 6.2 排班管理
延續現有 FullCalendar。

需要：
- 月曆檢視
- list view
- 依地點篩選
- 依工讀生篩選
- 新增排班
- 編輯排班
- 刪除排班
- 點日期快速新增
- 顯示工讀生姓名、地點、班別與時間
- 保留時間重疊檢查
- 保留同地點同班別不可重複排人規則

現有班別先保留：
- 辦公室 09:00–13:00
- 辦公室 13:00–17:00
- 管理中心 09:00–13:00
- 管理中心 13:00–17:00
- 管理中心 18:00–21:00
- 管理中心 SDA 17:30–21:30

班別日後應可由設定或資料表維護，不要永遠 hard-code 在 HTML。

### 6.3 請假檢視
管理員可查看：
- 申請人
- 原排班日期
- 原班別
- 地點
- 請假原因
- 申請時間
- 狀態
- 備註

狀態：
- PENDING
- APPROVED
- REJECTED
- CANCELLED

核准請假後：
- 不可靜默刪除歷史資料
- 原排班應保留 audit trail
- 可以標記為請假 / 缺員，或依系統設計建立 vacancy
- UI 要明確顯示此班目前是否需要補人

### 6.4 換班檢視
建議流程：
1. A 選自己的某一班
2. A 選欲交換的 B 與 B 的某一班，或指定 B 接自己的班
3. B 先接受 / 拒絕
4. B 接受後送管理員
5. 管理員最終核准
6. 核准時才正式更新排班

狀態可包含：
- PENDING_PEER
- PEER_REJECTED
- PENDING_ADMIN
- APPROVED
- REJECTED
- CANCELLED

換班核准前必須重新檢查：
- 雙方是否仍有該班
- 新排班是否造成時間重疊
- 同地點班別是否衝突

所有換班前後紀錄都必須保留。

### 6.5 當月個人總時數 / 報表
管理員可以：
- 選擇年月
- 查看全部工讀生
- 顯示各地點時數
- 顯示總時數
- 必要時顯示預估薪資
- 點擊某位工讀生查看該月明細
- 匯出 CSV
- XLSX 僅在使用者明確要求，或專案已有可靠 XLSX 產生流程時實作；不得因 XLSX 未完成而宣稱報表功能全部完成

報表至少包含：
- 姓名
- 學號
- 年月
- 辦公室時數
- 管理中心時數
- 其他班別時數
- 總時數
- 每一筆排班明細

總時數必須依實際 shift start/end 或班別 hours 計算，不可只依畫面文字。

---

## 7. 工讀生介面

工讀生登入後只能看到與自己有關的資訊。

### 7.1 首頁
顯示：
- 今天是否有班
- 下一個班
- 本月累計時數
- 待處理請假
- 待處理換班
- 證件到期提醒

### 7.2 個人排班
提供：
- 月曆
- 清單
- 日期
- 地點
- 班別
- 開始 / 結束時間
- 本月總時數

手機版要容易閱讀。

### 7.3 請假系統
工讀生只能針對自己的有效排班請假。

欄位：
- 排班
- 原因
- 備註
- 申請時間
- 狀態

規則：
- 已過期排班不可提出新請假
- 已核准 / 已拒絕不能自行修改
- PENDING 可以取消
- 不可對同一班重複建立多筆有效請假

### 7.4 換班系統
需要清楚區分：
- 我提出的
- 等我回覆的
- 等管理員審核的
- 已完成 / 已拒絕

正式換班前不可直接修改 schedule。

### 7.5 基本資料維護
至少包含：
- 姓名（視管理規則可唯讀）
- 學號（通常唯讀）
- 聯絡電話
- Email
- 國籍
- 帳號
- 修改密碼

外籍生額外：
- 居留證號
- 居留證截止日
- 工作證開始日
- 工作證截止日
- 證件影像

敏感欄位在一般畫面需遮罩，例如只顯示後 4 碼。

---

## 8. 證件文件安全保存與審核

### 使用流程
1. 使用者選擇「居留證」或「工作證」
2. 居留證上傳正反面；工作證上傳完整文件
3. 檢查檔案類型、大小與頁面數
4. 顯示預覽並保存至非公開儲存區
5. 學生上傳後即可使用系統；管理員另行審核資料正確性
6. 管理員核對居留證號與截止日，或工作證開始日與截止日
7. 核准或退回；退回時必須填寫原因

### 隱私與保存
- 不把證件影像送到未經說明的第三方
- 不把完整證號寫到 application log
- 上傳檔案使用隨機檔名，不使用證號當檔名
- 檔案不得放在可直接公開瀏覽的 static URL
- 下載 / 顯示證件必須經過登入與權限驗證
- 可以設定只保留最新一份，舊檔依政策刪除或留 audit metadata

---

## 9. 證件期限提醒

針對外籍工讀生：
- 60 天內到期：提醒
- 30 天內到期：重要提醒
- 已到期：紅色警示

管理員儀表板與工讀生首頁都要顯示。

提醒天數請集中設定，不要散落 hard-code。

---

## 10. 資料模型建議

至少建立以下 model / table。

### User
- id
- username
- password_hash
- role
- is_active
- must_change_password
- last_login_at
- created_at
- updated_at

### StaffProfile
- id
- user_id
- name
- student_number
- phone
- email
- nationality
- residence_id
- residence_expiry
- work_permit_start
- work_permit_expiry
- created_at
- updated_at

### ShiftType
- id
- code
- name
- location
- start_time
- end_time
- default_hours
- display_order
- is_active

### Shift
- id
- shift_date
- shift_type_id
- staff_id
- status
- created_by
- created_at
- updated_at

### LeaveRequest
- id
- staff_id
- shift_id
- reason
- note
- status
- reviewed_by
- reviewed_at
- created_at
- updated_at

### SwapRequest
- id
- requester_id
- requester_shift_id
- target_staff_id
- target_shift_id (nullable)
- peer_status
- admin_status
- note
- reviewed_by
- created_at
- updated_at

### StaffDocument
- id
- staff_id
- document_type
- stored_path / object_key
- original_filename
- mime_type
- uploaded_at
- ocr_status
- ocr_confidence
- extracted_data_json

### AuditLog
只記必要操作事件，不記敏感證號或密碼：
- id
- actor_user_id
- action
- entity_type
- entity_id
- safe_summary
- created_at

---

## 11. UI / UX 方向

風格：
- 校務系統感，但不要老舊
- 清爽、現代、容易閱讀
- 白底 + 深藍主色
- 輔助色用綠色表示成功、橘色表示待處理、紅色表示錯誤 / 到期
- 卡片圓角適中
- icon 可繼續使用 Bootstrap Icons
- 不要大量 emoji 當主要 UI icon
- 電腦版與手機版 responsive

### 狀態 badge
統一設計：
- 待處理：warning
- 核准：success
- 拒絕：danger
- 取消：secondary

### 表單
- 必填欄位清楚
- validation message 使用繁體中文
- 儲存成功 / 失敗要有 toast 或 alert
- 長時間操作需要 loading state
- 按鈕避免重複提交

---

## 12. 現有排班規則不得破壞

請從現有 `main.html` 擷取並保留：
- 同一日期、同一地點、同一時段不得安排兩人到同一個班
- 同一工讀生不能同時出現在重疊班別
- 地點目前至少有 OFFICE 與 MC
- 工讀時數可以依工讀生彙總
- FullCalendar 月曆為主要排班操作之一

將上述 validation 移到後端，前端也可做預檢，但後端必須是最後權威。

---

## 13. 資料一致性與交易

以下操作應使用 transaction：
- 核准換班並修改兩筆 Shift
- 核准請假並變更 Shift 狀態
- 管理員核准證件資料並更新 profile/document metadata

如果任何一步失敗，不能留下半套資料。

---

## 14. 測試要求

至少建立 pytest 測試：

### Authentication
- 未登入不能進管理頁
- STUDENT 不能進 ADMIN route
- 密碼不以明文儲存

### Scheduling
- 同地點相同時段重複排班會被拒絕
- 同一人時間重疊會被拒絕
- 合法班別可新增

### Leave
- 工讀生只能替自己的 shift 請假
- 不可重複請同一班
- admin 才能 approve / reject

### Swap
- 使用者不能交換不屬於自己的班
- peer 未接受前 admin 不可直接完成
- approve 前重新檢查衝突
- 核准後排班正確更新

### Profile / Documents
- STUDENT 不能讀其他人的證件
- 未經管理員審核不可將證件資料標記為已確認
- 不合法圖片上傳會被拒絕

---

## 15. Seed / Demo 資料

開發環境請保留一組 demo 帳號，例如：
- admin / 測試密碼
- student1 / 測試密碼
- student2 / 測試密碼

但：
- 測試密碼只可存在 seed / README 開發說明
- Production 不可使用預設密碼
- `.env` 不可 commit

可將目前 main.html 中的範例工讀生轉為 seed data，但不要把真實個資放進 repository。

---

## 16. 開發階段

以下 Phase 僅作為歷史追蹤，不限制目前工作順序。開始新工作前，應以目前實際程式碼、migration、測試與使用者最新需求為準，不得重新加入已取消的功能；若規格與程式碼不一致，須在回報中指出差異。

### Phase 1 — 基礎架構
- 將現有單頁雛形整理成可維護專案
- 建 DB
- User / StaffProfile / ShiftType / Shift
- 登入 / 登出
- ADMIN / STUDENT 權限
- migration
- seed

### Phase 2 — 排班系統
- 移植現有排班 UI
- CRUD API / route
- 後端衝突檢查
- ADMIN 排班管理
- STUDENT 我的排班
- 月時數統計

### Phase 3 — 請假與換班
- LeaveRequest
- SwapRequest
- STUDENT workflow
- ADMIN 審核
- 狀態 badge
- audit trail

### Phase 4 — 基本資料與證件
- Profile 編輯
- 密碼修改
- 文件安全上傳
- 效期提醒

### Phase 5 — 證件審核與文件保存
- 居留證正反面上傳
- 工作證完整文件上傳
- 管理員審核證號與有效日期
- 拒絕時填寫原因
- 文件下載、保存期限與稽核

### Phase 6 — 報表與 UI polish
- 月報
- CSV / XLSX
- dashboard
- responsive
- accessibility
- UI consistency

每完成一個 Phase：
1. 執行測試
2. 說明修改檔案
3. 說明如何啟動
4. 列出未完成項目
5. 不要假裝未實作的功能已完成

### 完成定義

一項功能只有在以下條件都滿足時才能列為 Completed：
- 相關後端 route / service 實際可執行
- 權限與輸入驗證位於後端
- 必要的 migration 已建立
- 相關 pytest 已通過
- 重要 UI 流程至少完成一次本機手動驗證
- 涉及 responsive UI 時，需驗證桌面與窄視窗
- 尚未完成或未驗證的部分必須列在 Remaining

---

## 17. Codex 每次工作的回覆格式

本專案的回覆格式優先於一般 Skill 的簡短輸出限制。使用者要求審閱、分析、逐項報告或階段性回報時，應完整提供證據與未完成項目。

每次實作後請回覆：

### Completed
- 本次完成內容

### Files changed
- `path/to/file`: 修改摘要

### Database changes
- migration / model 改動

### Tests
- 執行了哪些測試
- 測試結果

### How to run
```bash
...
```

### Manual verification
1. ...
2. ...

### Remaining
- 尚未完成 / 下一階段

---

## 18. 禁止事項

- 不可把所有功能繼續塞在單一 `main.html`
- 不可用 localStorage 當正式資料庫
- 不可用 JavaScript 變數保存正式工讀生資料
- 不可明文儲存密碼
- 不可只做 UI 假功能
- 不可只在前端做權限判斷
- 不可把證件直接放 public/static
- 不可把完整證號或密碼寫入 log
- 不可為了「看起來完成」而加入未實際運作的假按鈕
