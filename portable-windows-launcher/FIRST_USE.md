# Portable Launcher 第一次使用

## 給一般使用者

1. 將完整系統資料夾放到任意可寫入的位置，例如 `D:\DormStaffSystem`。請勿放在 `C:\Program Files` 或 XAMPP `htdocs`。
2. 開啟 `portable-windows-launcher\DormStaffLauncher.exe`。
3. 確認「專案資料夾」指向含有 `wsgi.py` 與 `requirements.txt` 的資料夾。
4. 按「安裝／修復環境」。第一次需要網路，會下載官方 Python、pip、PortableGit 及 Python 套件。
5. Launcher 會自動建立缺少的 `instance` 與 SQLite、執行 migration；GitHub 不需要也不應包含 `.db`。
6. 若尚未有管理員，按「啟動系統」時會引導使用者建立第一位管理員。
7. 完成後再次按「啟動系統」，再按「開啟系統」。

新版 Launcher 也會在啟動 migration 前修復舊版 `.env` 的 `sqlite:///instance/dorm_staff.db` 設定，避免 SQLite 被解析成重複的 `instance\instance` 路徑。

所有 runtime 都安裝在 Launcher 所在資料夾的 `.venv`，不會修改 Windows 系統 Python 或 Git；此資料夾可隨時由 Launcher 重建，也不會塞進系統資料備份。設定保存在 `launcher.ini`，預設專案路徑使用 `..`，所以整個資料夾搬到不同磁碟後仍可運作。

## Port 與連線範圍

- 預設 `127.0.0.1:8000`：只有本機能連線，適合單機使用及 XAMPP reverse proxy。
- 勾選「允許區域網路連線」會改為 `0.0.0.0`。此模式會讓區網其他電腦可能連入，必須由管理者設定 Windows 防火牆、HTTPS 與校方網路政策。
- 不要把 Waitress port 直接暴露到公網；正式環境請使用 XAMPP Apache HTTPS reverse proxy。

## 清除測試資料並建立第一位管理員

若複製來的系統包含舊測試資料，可使用紅色的「全新初始化：清空資料並建立第一位管理員」：

1. 先完成「安裝／修復環境」，並停止所有 Launcher、排程或其他 Waitress 服務。
2. 輸入新的管理員登入帳號、顯示名稱、密碼及密碼確認。
3. 密碼必須為 8–128 個字元；管理員帳號必須為 3–80 位英數字、句點、底線或連字號。
4. 輸入大寫 `RESET`，再閱讀第二次永久刪除確認。
5. 完成後即可使用新管理員帳號與自行設定的密碼登入，不會建立 demo 帳號。

重置後不會帶入任何學生、排班或申請資料；請由新管理員登入後依實際單位需要建立／確認工作地點、班別、薪資設定及工讀生帳號。

### 為什麼 GitHub 不放預設空白 DB

SQLite 是每台電腦的執行資料，不是程式碼。若把空白 DB commit 到 Git，正式使用後它會永遠顯示為本機修改，可能阻擋 Git 更新；錯誤的 checkout／merge 也可能覆蓋正式資料。Repository 應保留 migration 與 `.env.example`，由 Launcher 在每台電腦建立自己的 DB。

重置會永久刪除 `instance` 內的 SQLite、所有帳號、工讀生、排班、申請、薪資設定、證件、session、文件金鑰及 audit history，並輪替 `.env` 的 session／文件加密密鑰。畫面另有預設勾選項，可一併刪除 `outputs` 內可能含舊資料的匯出報表。

這項操作不會刪除程式原始碼、Git repository、Launcher runtime 或 `launcher.ini`，也不會建立舊資料備份。若 migration 或建立第一位管理員失敗，Launcher 會嘗試自動還原原本的 `instance` 與 `.env`。

為避免誤刪外部資料，此功能只接受資料庫、證件與金鑰都位於專案 `instance` 內的 portable SQLite 設定；PostgreSQL 或外部儲存路徑必須由正式資料庫管理程序處理。

## Git 更新

「Git 安全更新」只會在下列條件成立時執行：

- 所選資料夾是 Git repository 根目錄。
- 工作目錄沒有未提交變更。
- 目前分支與畫面設定一致。
- 更新前備份成功。

更新先在目前 Launcher 內執行備份、`fetch` 與安全檢查，接著暫停背景巡檢並關閉 Launcher；由複製到 Windows 暫存資料夾的外部更新器執行 `merge --ff-only`、套件安裝、migration 及應用程式匯入檢查。如此可一併更新 Windows 正在鎖定的 `DormStaffLauncher.exe` 及所有相關腳本。新版 Launcher 成功開啟並持續運作後才會清除復原檔；套用失敗時會回復舊 commit、舊套件、更新前資料備份與舊 Launcher。完成、已回復或無法回復時都會重新開啟 Launcher並顯示結果；詳細紀錄位於 `.venv/logs/self-update.log`。

若電腦在更新期間斷電，`.venv/update-in-progress` 與 `.venv/update-state.ini` 會保留更新狀態。watchdog 會暫停網站自動啟動，Launcher 下次開啟時會主動詢問是否回復舊 commit、套件、資料與 EXE；完成復原前不要直接刪除 `update-state.ini`、`launcher-before-update.exe` 或更新前 ZIP。

更新期間不要手動重新開啟 Launcher、移動 portable 資料夾或關閉電腦。若收到的資料夾沒有 `.git`，請輸入 HTTPS repository URL，先將專案 Clone 到新的空白資料夾。

Private repository 可能要求 Windows Credential Manager 或 Personal Access Token。不要把 token 寫進 URL、`launcher.ini` 或交給其他人。

若手上只有 Launcher、還沒有專案，可先按一次「安裝／修復環境」建立 Python、pip 與 PortableGit，再輸入 HTTPS Git URL 執行 Clone；選取 Clone 完成的專案資料夾後，再按一次「安裝／修復環境」即可完成套件與資料庫設定。

## 無網路快速搬移

可以先在一台 64-bit Windows 電腦完成安裝並停止服務，再直接複製完整資料夾到新電腦，包含隱藏的 `portable-windows-launcher\.venv`。Launcher 每次啟動服務前會重寫目前專案位置，因此磁碟機代號或上層路徑可以不同。

若複製內容包含 `instance`，也會包含正式資料庫、證件與解密金鑰，只能交給有權限的承辦人並使用加密媒體；不要把含正式資料的 portable folder 當作一般安裝包公開散發。

## 資料與備份

- `instance` 內含 SQLite、證件影像及文件金鑰；搬移時必須整份保留。Launcher 的完整備份 ZIP 只保存可還原的資料、`.env`、證件與金鑰，不重複打包可由 Git 取得的程式碼。
- Launcher 首次產生或全新初始化輪替 `SECRET_KEY` 後，會將完整 `.env` 備份至隱藏檔 `instance/private_keys/backup/application-env.backup`；備份驗證失敗時會中止該流程。
- 若 `.env` 遺失，可在停止系統後將上述備份複製回專案根目錄並命名為 `.env`。此檔含 `SECRET_KEY` 與完整環境設定，不得上傳 GitHub、傳送給無權限人員或放在公開網路磁碟。
- `instance` 與備份 ZIP 都含敏感資料，應放在 BitLocker 磁碟並限制存取。
- 更新前 Launcher 會建立 portable backup，但仍應另外保存一份異機加密備份。
- 防毒軟體或 SmartScreen 可能對自行編譯且未簽章的 EXE 顯示警告。正式發給多人使用前，建議由學校使用自己的 Code Signing 憑證簽署。

## 資料移轉與還原

Launcher 的「匯出／備份系統」可建立完整 portable backup ZIP；「移轉／還原資料」接受下列來源：

- 完整 ZIP（推薦）：還原 SQLite、證件檔案、文件金鑰及 `.env`，但不會用備份內的舊程式碼覆蓋目前新版程式。
- 單一 `.db`／`.sqlite`／`.sqlite3`：只取代資料庫；若來源有證件紀錄，證件檔案及解密金鑰不會隨 DB 移入，Launcher 會在確認畫面警告。

移轉前 Launcher 會停止目前服務、檢查 SQLite 完整性、確認 migration revision 並顯示帳號／工讀生／排班／證件筆數。使用者必須輸入大寫 `MIGRATE` 才會繼續。目的系統會先備份到 `outputs/portable-backups/before-data-migration-*.zip`；migration、管理員登入條件或文件金鑰驗證失敗時會自動回復原資料。

移轉採整套取代，不會合併 A、B 兩套資料，也不會刪除來源檔。若來源 revision 比目前程式更新，請先執行 Git 安全更新，再重新移轉。

## 自啟動巡檢

在 Launcher 設定「巡檢分鐘」（1–1440）後，按「啟用自啟動巡檢」並同意 Windows UAC。系統會建立兩個 Windows 工作排程：`DormStaffSystem-PortableWatchdog` 在開機時及指定間隔檢查網站，只有系統未回應且 Port 未被占用時才啟動 Waitress；`DormStaffSystem-PortableLauncher` 則在目前使用者登入 Windows 時自動顯示 Launcher。

Launcher 每兩秒透過輕量 `/healthz` 檢查應用程式與資料庫，並定期查詢 Windows 工作排程的實際狀態，所以無論系統由 Launcher 或背景巡檢啟動，右上角都會顯示真實運行及自啟動狀態；若兩個排程只剩一個，會顯示「自啟動：不完整」。背景巡檢紀錄也會同步到畫面下方。按「停止系統」可停止背景巡檢啟動的系統，但只要自啟動巡檢仍啟用，下一個巡檢週期就會再次啟動。若要持續停止，請先按「停用自啟動巡檢」，再按「停止系統」。

實際運行狀態以設定 Port 的登入頁健康檢查為準，不依賴 PID 檔是否存在。若背景系統由 Windows SYSTEM 身分啟動，「停止系統」會要求 UAC 權限；Launcher 只會停止由本 portable Python 占用該 Port 的程序，不會停止其他軟體。

「停用自啟動巡檢」會一鍵移除上述兩個排程，但不會停止目前已執行的系統。若搬動整個 portable 資料夾，請在新位置重新啟用一次，讓工作排程更新路徑。巡檢紀錄位於 `portable-windows-launcher/.venv/logs/watchdog.log`。

## 維護狀態、診斷與紀錄

- 安裝／修復、資料庫升級、備份、資料移轉、全新初始化、啟動前 migration 及 watchdog 啟動共用同一個 `.venv/maintenance.lock`，避免維護資料時被背景排程重新開站。
- 同一個 portable 資料夾一次只允許一個 Launcher 視窗，避免兩個視窗同時操作同一套 DB 或服務。
- 「匯出診斷支援包」會收集版本、Git commit、Port、工作排程狀態與最近紀錄，但不包含 DB、`.env`、證件或金鑰；提供給協助排錯的人員前仍應先確認內容。
- Launcher、server、watchdog 與更新紀錄預設保留最近 30 天；watchdog／更新主紀錄超過約 2 MB 時會輪替。

## 重新編譯

Windows 10/11 內建的 .NET Framework 可編譯本啟動器：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build-launcher.ps1 -Clean
```

一般使用者不需要執行此步驟，直接開啟已建立的 `DormStaffLauncher.exe` 即可。
