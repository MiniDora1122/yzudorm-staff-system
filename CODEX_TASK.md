# CODEX_TASK.md — 第一次交給 Codex 的任務

請先閱讀：
1. `AGENTS.md`
2. 現有 `main.html`
3. repository 中其他所有現有檔案

這是一個「宿舍工讀生系統」。

目前 `main.html` 是管理員排班與人員管理的前端 prototype。我希望把它逐步改造成真正可以多人登入使用的系統，而不是把現有畫面丟掉重新做一個完全無關的 demo。

## 這次先做 Phase 1，不要直接開始 OCR

請完成：

1. 分析目前 `main.html`
   - 列出目前已有功能
   - 找出哪些邏輯可保留
   - 找出目前因為只有純前端而無法正式使用的部分

2. 建立合理的專案結構
   - 如果 repo 尚無後端，採 Flask + SQLAlchemy + Jinja2 + Bootstrap + FullCalendar
   - SQLite 作為開發 DB
   - DATABASE_URL 可切換其他 DB
   - 設定 `.env.example`

3. 建立 DB models
   - User
   - StaffProfile
   - ShiftType
   - Shift
   - 先預留 LeaveRequest / SwapRequest 所需架構也可以，但不要為了趕進度寫假功能

4. 建立登入 / 登出
   - ADMIN
   - STUDENT
   - session authentication
   - password hash
   - role authorization
   - unauthorized redirect / 403
   - 修改密碼基礎 route

5. 將現有工讀生資料與班別改為 DB seed
   - 不再由 JavaScript array 當正式資料來源
   - 現有範例資料只能當 demo seed
   - 班別不可永久 hard-code 在 HTML

6. 建立兩種登入後首頁
   - ADMIN：先有 Dashboard / 排班 / 工讀生管理入口
   - STUDENT：先有「我的排班 / 我的資料」入口
   - 尚未完成的請假、換班、OCR 功能可以顯示「開發中」，但不要做成看似可用的假功能

7. UI
   - 整體改成乾淨現代的校務後台
   - 白底 + 深藍主色
   - Bootstrap Icons
   - responsive
   - 保留 FullCalendar 技術

8. Migration + seed
   - 提供初始化方式
   - demo admin / student 帳號

9. Tests
   至少測：
   - login success/failure
   - logout
   - 未登入阻擋
   - STUDENT 無法進 ADMIN route
   - password 不是 plaintext
   - model 基礎 CRUD

## 很重要

不要一次把整個專案全部重寫到 Phase 6。

請先做出一個可執行、可登入、可區分管理員與工讀生、資料真的存在 DB 的 Phase 1。

現有 `main.html` 的排班設計與邏輯是之後 Phase 2 要移植的基礎，不要遺失。

## 完成後請提供

### Completed
本次完成項目

### Current architecture
簡要說明目前架構

### Files changed
每個檔案做什麼

### Database
models / migration / seed

### Tests
實際執行指令與結果

### Run
從乾淨環境開始的完整啟動步驟

### Demo accounts
開發用 admin / student 帳號

### Existing main.html migration notes
哪些功能已搬、哪些會留到 Phase 2

### Next
建議下一個 Phase 的工作，但先不要自行開始大改
