# Git 長期更新教學

本系統的 Git repository 只保存程式碼與 migration，不保存正式資料。下列內容必須永遠留在每台正式主機本機：

- `.env`
- `instance/dorm_staff.db`
- `instance/private_documents`
- `instance/private_keys`
- session、log、報表、備份 ZIP

## 一、建立 Private remote repository

可使用 GitHub Private repository、學校 GitLab 或其他私有 Git server。若使用 GitHub：

1. 登入 GitHub，建立新的 repository。
2. Visibility 選擇 **Private**。
3. 不要預先加入 README、`.gitignore` 或 License，保持空 repository。
4. 複製 HTTPS 或 SSH remote URL。

以下以 `https://github.com/ACCOUNT/dorm-staff-system.git` 為例，請換成真正網址。

## 二、舊電腦建立第一個版本

```powershell
Set-Location "C:\Users\yangl\Documents\ChatGPT\宿舍工讀生系統"

git config --local user.name "你的姓名"
git config --local user.email "你的工作 Email"
git branch -M main
```

加入檔案前先確認敏感資料會被忽略：

```powershell
git check-ignore .env
git check-ignore instance\dorm_staff.db
git check-ignore instance\private_keys\document-fernet.key
git check-ignore outputs\portable-backups\example.zip
```

每個指令都應回傳對應路徑。接著：

```powershell
git add .
git status
```

在 staged files 中不可看到 `.env`、`instance`、`.db`、證件、金鑰、報表或 ZIP。再做一次機器檢查：

```powershell
git diff --cached --name-only | Select-String -Pattern '(^|/)(\.env|instance|outputs|tmp)(/|$)|\.db$|\.zip$'
```

正常應沒有輸出。確認後建立並上傳第一個版本：

```powershell
git commit -m "Initial production-ready dorm staff system"
git remote add origin https://github.com/ACCOUNT/dorm-staff-system.git
git push -u origin main
```

GitHub HTTPS 不接受帳號密碼登入 Git；依 GitHub 畫面使用瀏覽器授權、Personal Access Token，或改用 SSH key。

## 三、第一次搬到新電腦

舊電腦先停止系統並建立 portable backup：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\deployment\create-portable-backup.ps1" `
  -Destination "E:\dorm-staff-migration.zip"
```

新電腦不要把 portable ZIP 直接當作日後 Git repository。先 clone 程式：

```powershell
git clone https://github.com/ACCOUNT/dorm-staff-system.git D:\DormStaffSystem
New-Item -ItemType Directory -Path D:\DormStaffMigration
Expand-Archive -LiteralPath E:\dorm-staff-migration.zip -DestinationPath D:\DormStaffMigration
```

只把正式環境與資料複製到 clone：

```powershell
Copy-Item -LiteralPath D:\DormStaffMigration\.env -Destination D:\DormStaffSystem\.env
Copy-Item -LiteralPath D:\DormStaffMigration\instance -Destination D:\DormStaffSystem\instance -Recurse
```

確認 `.env` 的 HTTP 設定：

```dotenv
FLASK_DEBUG=0
SESSION_COOKIE_SECURE=0
TRUST_PROXY=1
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1
```

安裝與啟動：

```powershell
Set-Location D:\DormStaffSystem
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\install-production.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\register-startup-task.ps1
```

確認登入、排班、報表與至少一份既有證件後，安全刪除 `D:\DormStaffMigration`；該目錄包含完整個資與金鑰。

## 四、舊電腦日常開發與發布

每次完成新功能：

```powershell
Set-Location "C:\Users\yangl\Documents\ChatGPT\宿舍工讀生系統"
git status
.\.venv\Scripts\python.exe -m pytest -q
git add .
git diff --cached
git commit -m "說明這次更新內容"
git push origin main
```

不要使用 `git add -f` 強迫加入被忽略的資料。不要把 `.env`、資料庫或搬家 ZIP 改名後加入 Git。

## 五、新電腦安全更新

以系統管理員 PowerShell 執行，並把備份直接放到加密外接磁碟：

```powershell
Set-Location D:\DormStaffSystem
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\deployment\update-from-git.ps1 `
  -Remote origin `
  -Branch main `
  -BackupDirectory E:\DormStaffBackups
```

腳本會依序：

1. 拒絕更新有未提交修改的正式 working tree。
2. 停止 `DormStaffSystem-Waitress`。
3. 建立包含 SQLite、證件及金鑰的更新前備份。
4. 執行 `git fetch` 與 `git merge --ff-only`。
5. 更新 Python dependencies。
6. 執行 `flask db upgrade`，保留既有資料並套用 migration。
7. 驗證 application 能載入。
8. 重新啟動 Waitress 排程。

腳本不會執行 `seed`，也不會從 Git 覆蓋 `.env` 或 `instance`。

## 六、更新後驗證

每次至少確認：

1. 管理員與學生可以登入、登出。
2. 原有學生、排班、請假及換班仍存在。
3. 報表可下載。
4. 既有證件可預覽、下載。
5. 新功能可操作。
6. `instance/logs` 與 Apache error log 沒有錯誤。

## 七、更新失敗復原

更新腳本會印出舊 commit hash 及備份 ZIP。若 migration 已執行，不可只把 Git 切回舊版，資料庫也必須一起恢復。

先停止服務並解壓備份到暫存目錄：

```powershell
Stop-ScheduledTask -TaskName DormStaffSystem-Waitress
New-Item -ItemType Directory -Path D:\DormStaffRollback
Expand-Archive -LiteralPath E:\DormStaffBackups\before-git-update-時間.zip -DestinationPath D:\DormStaffRollback
```

將目前 instance 改名保留，再恢復備份資料：

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Rename-Item D:\DormStaffSystem\instance "instance-failed-$stamp"
Copy-Item D:\DormStaffRollback\instance D:\DormStaffSystem\instance -Recurse
Copy-Item D:\DormStaffRollback\.env D:\DormStaffSystem\.env -Force
```

把程式切回更新腳本顯示的舊 commit：

```powershell
Set-Location D:\DormStaffSystem
git reset --hard 舊COMMIT_HASH
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "from app import create_app; app=create_app(); print('Rollback import OK')"
Start-ScheduledTask -TaskName DormStaffSystem-Waitress
```

驗證成功後再移除 rollback 暫存資料。不要對已恢復的舊資料庫執行較新版本的 migration。

