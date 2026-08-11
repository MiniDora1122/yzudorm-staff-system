# 宿舍工讀生系統：純 HTTP 搬家與 Demo Seed 復原手冊

本手冊說明如何使用「純 HTTP + XAMPP Apache + Waitress」將宿舍工讀生系統從舊電腦完整搬移到新電腦，並說明不小心執行 demo seed 後，只保留管理員帳號的兩種復原方式。

> **安全警告**：HTTP 不會加密登入帳號、密碼及 Session Cookie，只適合封閉的校內可信任網路或已加密的 VPN。請勿把純 HTTP 系統直接暴露到 Internet 或公共 Wi-Fi。

## 目錄

1. [搬移內容與重要原則](#1-搬移內容與重要原則)
2. [舊電腦：停止系統](#2-舊電腦停止系統)
3. [舊電腦：建立完整搬家包](#3-舊電腦建立完整搬家包)
4. [新電腦：安裝必要軟體](#4-新電腦安裝必要軟體)
5. [新電腦：還原專案](#5-新電腦還原專案)
6. [新電腦：設定純 HTTP 環境](#6-新電腦設定純-http-環境)
7. [新電腦：安裝 Python 環境](#7-新電腦安裝-python-環境)
8. [新電腦：測試 Waitress](#8-新電腦測試-waitress)
9. [新電腦：設定 XAMPP Apache](#9-新電腦設定-xampp-apache)
10. [Windows 防火牆與自動啟動](#10-windows-防火牆與自動啟動)
11. [正式切換與驗證](#11-正式切換與驗證)
12. [誤執行 Demo Seed 的復原方式](#12-誤執行-demo-seed-的復原方式)
13. [方法 A：重建成只有 Admin 的全新資料庫](#13-方法-a重建成只有-admin-的全新資料庫)
14. [方法 B：保留預設地點與班別，只移除 Demo 學生](#14-方法-b保留預設地點與班別只移除-demo-學生)
15. [已有正式資料時的處理原則](#15-已有正式資料時的處理原則)

## 1. 搬移內容與重要原則

系統的可攜式搬家 ZIP 會包含：

- Flask 系統程式。
- SQLite 資料庫。
- 管理員與工讀生帳號（密碼為安全雜湊）。
- 工讀生基本資料、學號與聯絡資料。
- 排班、請假、換班與稽核紀錄。
- 地點、班別、時數與薪資設定。
- 加密後的居留證及工作證影像。
- 證件解密金鑰及金鑰備份。
- `.env` 環境設定與 `SECRET_KEY`。
- 本機 Bootstrap、Bootstrap Icons、FullCalendar 等前端資源。

搬家 ZIP 不會包含：

- `.venv` 虛擬環境。
- Python cache。
- 登入 Session cache。
- Waitress 與 Apache log。
- 舊輸出及舊備份。

搬家 ZIP 同時包含個資與解密金鑰，等同一套完整正式系統，應使用下列方式搬運：

- 啟用 BitLocker 的隨身碟。
- 受學校管理的加密外接硬碟。
- 其他經校方核准的加密儲存設備。

請勿使用一般 Email、LINE 或公開雲端連結傳送搬家 ZIP。

## 2. 舊電腦：停止系統

### 2.1 暫停使用

先通知所有工讀生，在搬移完成以前不要登入、排班、請假、換班或上傳證件，以免備份完成後舊電腦又產生新資料。

### 2.2 停止手動啟動的服務

如果系統目前在 PowerShell 中執行，回到該視窗按：

```text
Ctrl+C
```

### 2.3 停止 Windows 工作排程器服務

如果已註冊自動啟動，以系統管理員身分開啟 PowerShell：

```powershell
Stop-ScheduledTask -TaskName "DormStaffSystem-Waitress"
```

如果顯示找不到排程，代表目前可能不是透過工作排程器啟動，可忽略該訊息並確認手動啟動視窗已停止。

### 2.4 確認 Waitress 已停止

```powershell
Test-NetConnection 127.0.0.1 -Port 8000
```

應顯示：

```text
TcpTestSucceeded : False
```

如果仍為 `True`，先不要備份，應找出並停止仍在執行的 Waitress 或 Flask 程序。

## 3. 舊電腦：建立完整搬家包

以下範例假設：

- 舊專案位於 `C:\Users\yangl\Documents\ChatGPT\宿舍工讀生系統`。
- 加密隨身碟代號是 `E:`。

### 3.1 進入專案

```powershell
Set-Location "C:\Users\yangl\Documents\ChatGPT\宿舍工讀生系統"
```

### 3.2 建立搬家 ZIP

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\deployment\create-portable-backup.ps1" `
  -Destination "E:\dorm-staff-migration.zip"
```

成功時會顯示：

```text
Backup created successfully.
```

### 3.3 確認 ZIP 存在

```powershell
Get-Item "E:\dorm-staff-migration.zip"
```

### 3.4 記錄 SHA256

```powershell
Get-FileHash "E:\dorm-staff-migration.zip" -Algorithm SHA256
```

把顯示的 SHA256 值另外記錄。新電腦必須再次計算並比對，確認檔案沒有損壞或遭到更換。

### 3.5 暫時保留舊電腦

新電腦完整驗證以前：

- 不要刪除舊專案。
- 不要刪除舊 SQLite。
- 不要刪除 `instance/private_keys`。
- 不要重新開放舊系統給學生使用。
- 不要讓新舊兩台電腦同時成為正式系統。

SQLite 不會自動同步兩台電腦的資料；如果兩台同時使用，資料將分流且無法簡單合併。

## 4. 新電腦：安裝必要軟體

### 4.1 安裝軟體

在新電腦安裝：

1. 64-bit Python 3。
2. XAMPP。

### 4.2 確認 Python

```powershell
py -3 --version
```

### 4.3 確認 XAMPP Apache

```powershell
Test-Path "C:\xampp\apache\bin\httpd.exe"
```

應顯示：

```text
True
```

### 4.4 設定固定 IP

建議請網管為新電腦設定固定 IP 或 DHCP reservation，例如：

```text
192.168.1.50
```

若 IP 日後改變，學生原本使用的網址也會失效。

### 4.5 驗證搬家 ZIP

假設加密隨身碟在新電腦仍為 `E:`：

```powershell
Get-FileHash "E:\dorm-staff-migration.zip" -Algorithm SHA256
```

確認結果與舊電腦記錄的 SHA256 完全一致。

## 5. 新電腦：還原專案

建議將系統放在：

```text
D:\DormStaffSystem
```

不要放進：

```text
C:\xampp\htdocs
```

否則 `.env`、SQLite、證件或解密金鑰可能被 Apache 當成公開檔案處理。

### 5.1 確認目的資料夾

目的資料夾應不存在或完全空白。如果已經有同名且不為空的資料夾，先停止，不要直接覆蓋。

```powershell
New-Item -ItemType Directory -Path "D:\DormStaffSystem"
```

### 5.2 解壓縮

```powershell
Expand-Archive `
  -LiteralPath "E:\dorm-staff-migration.zip" `
  -DestinationPath "D:\DormStaffSystem"
```

### 5.3 確認重要檔案

```powershell
Test-Path "D:\DormStaffSystem\wsgi.py"
Test-Path "D:\DormStaffSystem\.env"
Test-Path "D:\DormStaffSystem\instance\dorm_staff.db"
Test-Path "D:\DormStaffSystem\instance\private_keys\document-fernet.key"
```

四項都應顯示：

```text
True
```

## 6. 新電腦：設定純 HTTP 環境

開啟 `.env`：

```powershell
notepad "D:\DormStaffSystem\.env"
```

確認至少包含：

```dotenv
FLASK_APP=wsgi.py
FLASK_DEBUG=0

DATABASE_URL=sqlite:///dorm_staff.db

SESSION_TIMEOUT_MINUTES=30
SESSION_COOKIE_SECURE=0

TRUST_PROXY=1
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1

DOCUMENT_STORAGE_DIR=instance/private_documents
DOCUMENT_KEY_DIR=instance/private_keys
DOCUMENT_KEY_BACKUP_DIR=instance/private_keys/backup
```

重要說明：

- 純 HTTP 必須使用 `SESSION_COOKIE_SECURE=0`，否則登入 Cookie 不會透過 HTTP 傳送。
- 前方使用 XAMPP Apache，所以使用 `TRUST_PROXY=1`。
- Waitress 仍應只監聽 `127.0.0.1`。
- 不要更換搬家包原有的 `SECRET_KEY`。
- 不要重新產生或覆蓋 `document-fernet.key`。
- 不要把完整證號、密碼或金鑰寫進 log。

## 7. 新電腦：安裝 Python 環境

```powershell
Set-Location "D:\DormStaffSystem"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\deployment\install-production.ps1"
```

此腳本會：

- 建立新的 `.venv`。
- 安裝 Flask、Waitress 及其他套件。
- 執行資料庫 migration。
- 驗證 Flask application 可以載入。

正式資料搬移時，不要執行：

```powershell
flask seed
```

## 8. 新電腦：測試 Waitress

### 8.1 啟動 Waitress

```powershell
Set-Location "D:\DormStaffSystem"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\deployment\start-production.ps1"
```

### 8.2 本機測試

在新電腦瀏覽器開啟：

```text
http://127.0.0.1:8000
```

逐項測試：

1. 管理員登入及登出。
2. 工讀生登入及登出。
3. 管理員排班月曆。
4. 學生「我的班表」。
5. 請假及換班紀錄。
6. 工讀生學號與基本資料。
7. 時數及薪資設定。
8. CSV／XLSX 報表下載。
9. 舊證件預覽及下載。

務必測試一份舊證件。舊證件能正常預覽，才代表 SQLite、加密文件與解密金鑰都已正確搬移。

完成後按：

```text
Ctrl+C
```

停止測試服務。

## 9. 新電腦：設定 XAMPP Apache

### 9.1 啟用 Apache 模組

開啟：

```text
C:\xampp\apache\conf\httpd.conf
```

確認下列項目前面沒有 `#`：

```apache
LoadModule proxy_module modules/mod_proxy.so
LoadModule proxy_http_module modules/mod_proxy_http.so
LoadModule headers_module modules/mod_headers.so
```

確認有：

```apache
Include conf/extra/httpd-vhosts.conf
```

純 HTTP 不需要設定 SSL 憑證。

### 9.2 設定 VirtualHost

開啟：

```text
C:\xampp\apache\conf\extra\httpd-vhosts.conf
```

假設新電腦固定 IP 是 `192.168.1.50`，加入：

```apache
<VirtualHost *:80>
    ServerName 192.168.1.50

    ProxyRequests Off
    ProxyPreserveHost On

    RequestHeader set X-Forwarded-Proto "http"
    RequestHeader set X-Forwarded-Port "80"

    ProxyPass        / http://127.0.0.1:8000/ retry=0 timeout=60
    ProxyPassReverse / http://127.0.0.1:8000/

    ErrorLog  "logs/dorm-staff-error.log"
    CustomLog "logs/dorm-staff-access.log" combined
</VirtualHost>
```

如果要限制只有特定校內網段可以連線，例如 `192.168.1.x`，可在 VirtualHost 內加入：

```apache
<Location "/">
    Require ip 192.168.1.0/24
</Location>
```

網段必須依學校實際環境調整，不要直接假設範例網段正確。

### 9.3 檢查 Apache 語法

```powershell
C:\xampp\apache\bin\httpd.exe -t
```

應顯示：

```text
Syntax OK
```

檢查 VirtualHost：

```powershell
C:\xampp\apache\bin\httpd.exe -S
```

確認無誤後，由 XAMPP Control Panel 重啟 Apache。

## 10. Windows 防火牆與自動啟動

### 10.1 開放 Apache HTTP

以系統管理員身分開啟 PowerShell：

```powershell
New-NetFirewallRule `
  -DisplayName "Dorm Staff HTTP" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 80 `
  -Action Allow `
  -Profile Domain,Private
```

只需對學生端開放：

```text
TCP 80
```

不要對外開放：

```text
TCP 8000
```

### 10.2 註冊 Waitress 自動啟動

以系統管理員身分開啟 PowerShell：

```powershell
Set-Location "D:\DormStaffSystem"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\deployment\register-startup-task.ps1"
```

確認排程：

```powershell
Get-ScheduledTask -TaskName "DormStaffSystem-Waitress"
```

### 10.3 設定 Apache 自動啟動

在 XAMPP Control Panel 將 Apache 安裝為 Windows Service，並設定開機自動啟動。

### 10.4 重新開機驗證

重新啟動新電腦後執行：

```powershell
Test-NetConnection 127.0.0.1 -Port 8000
Test-NetConnection 127.0.0.1 -Port 80
```

兩項都應顯示：

```text
TcpTestSucceeded : True
```

## 11. 正式切換與驗證

學生端網址範例：

```text
http://192.168.1.50
```

正式切換前完成以下驗證：

- [ ] HTTP 網址能正常開啟。
- [ ] 管理員可以登入及登出。
- [ ] 工讀生可以登入及登出。
- [ ] 管理員排班月曆正常。
- [ ] 學生「我的班表」正常。
- [ ] 排班時間及地點完整顯示。
- [ ] 排班衝突檢查正常。
- [ ] 請假申請及審核正常。
- [ ] 換班申請及審核正常。
- [ ] 月份篩選正常。
- [ ] 時數及薪資試算正常。
- [ ] CSV／XLSX 報表可以下載。
- [ ] 舊證件可以預覽及下載。
- [ ] 手機版可以正常使用。
- [ ] Windows 重新啟動後服務會自動恢復。
- [ ] Apache error log 沒有持續錯誤。

確認新電腦完整運作後：

1. 公告學生改用新電腦網址。
2. 舊電腦停止提供正式服務。
3. 保留舊電腦資料一段安全觀察期。
4. 將搬家 ZIP 存放於加密的離線備份位置。
5. 不要把搬家 ZIP 留在桌面、Downloads 或 `htdocs`。

## 12. 誤執行 Demo Seed 的復原方式

目前 `seed` 可能建立：

- `admin`。
- `student1`。
- `student2`。
- 學號 `DEMO001`。
- 學號 `DEMO002`。
- 辦公室及管理中心。
- 6 種預設班別。
- 2026 年 8 月的 6 筆示範排班。
- 2026-01-01 生效的預設薪資試算設定。

`seed` 可重複執行，不會每次都重複增加相同項目。

如果 `admin` 是由 seed 建立，其 demo 密碼是公開的開發密碼，必須立即修改，不可用於正式環境。

復原前先判斷：

| 情況 | 建議方法 |
|---|---|
| 剛初始化，完全沒有正式資料 | 方法 A：重建資料庫 |
| 希望保留預設地點、班別及薪資設定 | 方法 B：只移除 demo 學生 |
| Demo 帳號已經參與正式操作 | 停止並依第 15 節處理 |

## 13. 方法 A：重建成只有 Admin 的全新資料庫

此方法適用於：

- 尚未建立任何正式學生。
- 沒有正式排班。
- 沒有正式請假或換班。
- 沒有正式證件。
- 目標是得到真正只有一個管理員的空系統。

此方法完成後，不會保留預設地點、班別及薪資設定，需要由管理介面重新建立。

### 13.1 停止服務

```powershell
Stop-ScheduledTask -TaskName "DormStaffSystem-Waitress"
```

若沒有排程，請在啟動 Waitress 的視窗按 `Ctrl+C`。

### 13.2 保留誤 Seed 的資料庫

不要直接刪除。先用可復原方式重新命名：

```powershell
Set-Location "D:\DormStaffSystem"

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

Move-Item `
  -LiteralPath ".\instance\dorm_staff.db" `
  -Destination ".\instance\dorm_staff.accidental-seed-$stamp.db"
```

### 13.3 建立全新空資料庫

```powershell
.\.venv\Scripts\python.exe -m flask --app wsgi.py db upgrade
```

此時只有空資料表，尚無帳號。

### 13.4 建立唯一管理員

進入 Flask shell：

```powershell
.\.venv\Scripts\python.exe -m flask --app wsgi.py shell
```

逐行輸入：

```python
from getpass import getpass
from app.extensions import db
from app.models import Role, User
```

建立管理員：

```python
admin = User(
    username="admin",
    role=Role.ADMIN,
    is_active=True,
    must_change_password=False,
)
```

安全輸入密碼：

```python
admin.set_password(getpass("請輸入管理員密碼："))
```

加入資料庫：

```python
db.session.add(admin)
db.session.commit()
```

檢查：

```python
[(user.username, user.role.value) for user in db.session.scalars(db.select(User)).all()]
```

正常應只看到：

```text
[("admin", "ADMIN")]
```

離開：

```python
exit()
```

重新啟動系統後，用剛建立的管理員登入，再建立正式地點、班別及薪資設定。

## 14. 方法 B：保留預設地點與班別，只移除 Demo 學生

此方法完成後會保留：

- `admin`。
- 辦公室。
- 管理中心。
- 6 種預設班別。
- 預設薪資設定。

並移除：

- `student1`。
- `student2`。
- `DEMO001`。
- `DEMO002`。
- Demo 學生目前擁有的排班。

此方法只適合剛誤執行 seed，且 demo 學生尚未：

- 提出請假。
- 參與換班。
- 上傳證件。
- 被改成正式學生。
- 產生需要保留的正式紀錄。

### 14.1 停止服務並建立備份

先停止 Waitress，再建立完整備份：

```powershell
Set-Location "D:\DormStaffSystem"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\deployment\create-portable-backup.ps1" `
  -Destination "E:\before-remove-demo.zip"
```

### 14.2 進入 Flask shell

```powershell
.\.venv\Scripts\python.exe -m flask --app wsgi.py shell
```

載入模型：

```python
from app.extensions import db
from app.models import User
```

讀取 demo 帳號：

```python
student1 = db.session.scalar(
    db.select(User).where(User.username == "student1")
)

student2 = db.session.scalar(
    db.select(User).where(User.username == "student2")
)
```

先確認學號：

```python
student1.staff_profile.student_number
student2.staff_profile.student_number
```

必須分別顯示：

```text
DEMO001
DEMO002
```

如果帳號不存在、沒有 StaffProfile，或學號不是上述值，立刻停止，不要刪除：

```python
db.session.rollback()
exit()
```

### 14.3 移除 Demo 排班與帳號

確認完全符合 demo 身分後，先刪除這兩人的排班：

```python
for user in (student1, student2):
    for shift in list(user.staff_profile.shifts):
        db.session.delete(shift)
```

再刪除帳號：

```python
db.session.delete(student1)
db.session.delete(student2)
db.session.commit()
```

檢查剩餘帳號：

```python
[(user.username, user.role.value) for user in db.session.scalars(db.select(User)).all()]
```

若原本沒有其他正式帳號，正常應只看到：

```text
[("admin", "ADMIN")]
```

離開：

```python
exit()
```

### 14.4 修改管理員密碼

如果 `admin` 是由 seed 建立，重新啟動系統並登入後，立即前往「修改密碼」。

不要繼續使用 demo 密碼：

```text
AdminDemo!2026
```

## 15. 已有正式資料時的處理原則

如果 demo 帳號已經：

- 提出請假。
- 參與換班。
- 上傳證件。
- 被改成正式學生。
- 排班被換給其他工讀生。
- 產生稽核紀錄。
- 參與正式報表或薪資計算。

請勿直接執行第 14 節的刪除操作，也不要刪除整個 SQLite。

安全處理順序：

1. 停止 Waitress，暫停所有使用者操作。
2. 建立目前狀態的完整可攜式備份。
3. 優先還原執行 seed 前的備份。
4. 如果沒有 seed 前備份，逐筆盤點 demo 帳號的排班、請假、換班、文件及 audit 關聯。
5. 確認每項資料要保留、轉移或刪除後，再進行針對性清理。
6. 不要直接刪除整個資料庫，否則正式資料也會消失。

## 附錄：常用檢查指令

### 檢查 Waitress

```powershell
Test-NetConnection 127.0.0.1 -Port 8000
```

### 檢查 Apache

```powershell
Test-NetConnection 127.0.0.1 -Port 80
C:\xampp\apache\bin\httpd.exe -t
C:\xampp\apache\bin\httpd.exe -S
```

### 查看 Waitress 日誌

```text
D:\DormStaffSystem\instance\logs
```

### 查看 Apache 日誌

```text
C:\xampp\apache\logs\dorm-staff-error.log
C:\xampp\apache\logs\dorm-staff-access.log
```

### 移除 Waitress 自動啟動排程

```powershell
Set-Location "D:\DormStaffSystem"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\deployment\unregister-startup-task.ps1"
```

---

文件版本：2026-08-11  
預設時區：Asia/Taipei
