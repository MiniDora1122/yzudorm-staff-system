# Windows + XAMPP 正式部署

此系統是 Flask 應用程式，不能像純 HTML/PHP 一樣只複製到 `htdocs`。建議把專案放在受保護的資料夾，由 Waitress 在本機 `127.0.0.1:8000` 執行，再讓 XAMPP Apache 以 HTTPS 反向代理。SQLite、證件與金鑰都不會直接暴露在網站目錄。

## 1. 第一次安裝

1. 安裝 64 位元 Python 3 與 XAMPP。
2. 將專案放到例如 `D:\DormStaffSystem`（不建議放在 `htdocs`）。
3. 在 PowerShell 執行：

```powershell
Set-Location D:\DormStaffSystem
Copy-Item .env.production.example .env
notepad .env
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\install-production.ps1
```

請將 `.env` 的 `SECRET_KEY` 換成至少 32 bytes 的隨機值。可用以下指令產生：

```powershell
py -3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

正式使用 HTTPS 時保留：

```dotenv
SESSION_COOKIE_SECURE=1
TRUST_PROXY=1
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1
```

`TRUST_PROXY=1` 只能在 Waitress 僅監聽 `127.0.0.1`，且前方代理為本機 Apache 時使用；不要把 Waitress 的 8000 port 對外開放。

## 2. 先直接測試 Waitress

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\start-production.ps1
```

在同一台電腦測試 `http://127.0.0.1:8000`。確認後按 `Ctrl+C` 停止，再設定 Apache。

## 3. 設定 XAMPP Apache

在 `C:\xampp\apache\conf\httpd.conf` 確認下列模組沒有被 `#` 註解：

```apache
LoadModule proxy_module modules/mod_proxy.so
LoadModule proxy_http_module modules/mod_proxy_http.so
LoadModule headers_module modules/mod_headers.so
LoadModule ssl_module modules/mod_ssl.so
```

在 `httpd.conf` 加入或確認 `Include conf/extra/httpd-vhosts.conf`。將 [dorm-staff-vhost.conf.example](apache/dorm-staff-vhost.conf.example) 依實際網域與憑證路徑修改後，加入 `C:\xampp\apache\conf\extra\httpd-vhosts.conf`。先執行語法檢查：

```powershell
C:\xampp\apache\bin\httpd.exe -t
```

再由 XAMPP Control Panel 重啟 Apache。校外或跨網段使用前，請由校方網管設定 DNS、受信任的 TLS 憑證、防火牆與存取政策；不要以自簽憑證作為正式服務。

## 4. 開機自動啟動

以系統管理員身分開啟 PowerShell：

```powershell
Set-Location D:\DormStaffSystem
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\register-startup-task.ps1
```

此排程會以 Windows `SYSTEM` 帳號於開機時啟動 Waitress，失敗時最多重試 3 次。日誌位於 `instance\logs`。移除方式：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\unregister-startup-task.ps1
```

更新程式或執行 migration 前，先在工作排程器停止 `DormStaffSystem-Waitress`，完成後再啟動。

## 5. 一鍵搬移／備份

建議先停止 `DormStaffSystem-Waitress`，再執行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\create-portable-backup.ps1 -Destination E:\Backup\dorm-staff.zip
```

ZIP 會包含程式、本機前端套件、SQLite、加密證件、文件金鑰／金鑰備份，以及可攜式 `.env`；不包含 `.venv`、session、cache、log 與舊輸出。SQLite 會透過線上備份 API 建立一致快照，不是直接複製使用中的 DB。

此 ZIP 同時包含個資與解密金鑰，等同完整正式系統，必須放在 BitLocker 或其他加密磁碟並限制承辦人存取，不可透過未加密郵件或公開雲端連結傳送。

在新電腦還原：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\restore-portable.ps1 `
  -Archive E:\Backup\dorm-staff.zip `
  -Destination D:\DormStaffSystem
Set-Location D:\DormStaffSystem
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\install-production.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deployment\register-startup-task.ps1
```

還原腳本只接受不存在或空白的目的資料夾，不會覆蓋現有系統。完成後依第 3 節設定該電腦的 XAMPP，並實際測試登入、排班、報表及一份證件預覽／下載。

## 6. 維護與備份原則

- 每次更新前建立可攜式備份，並定期做異機還原演練。
- 至少保留一份不在網站主機上的加密備份。
- 不把 `.env`、`instance`、備份 ZIP 或證件放進 `htdocs`／Git。
- 正式環境不執行 `flask seed`，避免建立 demo 帳號。
- 資料庫結構更新使用 `.venv\Scripts\python.exe -m flask --app wsgi.py db upgrade`。
- 定期檢查 `instance\logs`、Apache error log、證件清理 audit log 與磁碟容量。
