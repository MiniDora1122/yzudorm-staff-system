# Portable Launcher 第一次使用

## 給一般使用者

1. 將完整系統資料夾放到任意可寫入的位置，例如 `D:\DormStaffSystem`。請勿放在 `C:\Program Files` 或 XAMPP `htdocs`。
2. 開啟 `portable-windows-launcher\DormStaffLauncher.exe`。
3. 確認「專案資料夾」指向含有 `wsgi.py` 與 `requirements.txt` 的資料夾。
4. 按「安裝／修復環境」。第一次需要網路，會下載官方 Python、pip、PortableGit 及 Python 套件。
5. 完成後按「啟動系統」，再按「開啟系統」。

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

重置會永久刪除 `instance` 內的 SQLite、所有帳號、工讀生、排班、申請、薪資設定、證件、session、文件金鑰及 audit history，並輪替 `.env` 的 session／文件加密密鑰。畫面另有預設勾選項，可一併刪除 `outputs` 內可能含舊資料的匯出報表。

這項操作不會刪除程式原始碼、Git repository、Launcher runtime 或 `launcher.ini`，也不會建立舊資料備份。若 migration 或建立第一位管理員失敗，Launcher 會嘗試自動還原原本的 `instance` 與 `.env`。

為避免誤刪外部資料，此功能只接受資料庫、證件與金鑰都位於專案 `instance` 內的 portable SQLite 設定；PostgreSQL 或外部儲存路徑必須由正式資料庫管理程序處理。

## Git 更新

「Git 安全更新」只會在下列條件成立時執行：

- 所選資料夾是 Git repository 根目錄。
- 工作目錄沒有未提交變更。
- 目前分支與畫面設定一致。
- 更新前備份成功。

更新採 `fetch` 加 `merge --ff-only`，之後安裝套件、執行 migration 及應用程式匯入檢查。若收到的資料夾沒有 `.git`，請輸入 HTTPS repository URL，先將專案 Clone 到新的空白資料夾。

Private repository 可能要求 Windows Credential Manager 或 Personal Access Token。不要把 token 寫進 URL、`launcher.ini` 或交給其他人。

若手上只有 Launcher、還沒有專案，可先按一次「安裝／修復環境」建立 Python、pip 與 PortableGit，再輸入 HTTPS Git URL 執行 Clone；選取 Clone 完成的專案資料夾後，再按一次「安裝／修復環境」即可完成套件與資料庫設定。

## 無網路快速搬移

可以先在一台 64-bit Windows 電腦完成安裝並停止服務，再直接複製完整資料夾到新電腦，包含隱藏的 `portable-windows-launcher\.venv`。Launcher 每次啟動服務前會重寫目前專案位置，因此磁碟機代號或上層路徑可以不同。

若複製內容包含 `instance`，也會包含正式資料庫、證件與解密金鑰，只能交給有權限的承辦人並使用加密媒體；不要把含正式資料的 portable folder 當作一般安裝包公開散發。

## 資料與備份

- `instance` 內含 SQLite、證件影像及文件金鑰；搬移時必須整份保留。
- `instance` 與備份 ZIP 都含敏感資料，應放在 BitLocker 磁碟並限制存取。
- 更新前 Launcher 會建立 portable backup，但仍應另外保存一份異機加密備份。
- 防毒軟體或 SmartScreen 可能對自行編譯且未簽章的 EXE 顯示警告。正式發給多人使用前，建議由學校使用自己的 Code Signing 憑證簽署。

## 重新編譯

Windows 10/11 內建的 .NET Framework 可編譯本啟動器：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build-launcher.ps1 -Clean
```

一般使用者不需要執行此步驟，直接開啟已建立的 `DormStaffLauncher.exe` 即可。
