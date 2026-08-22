# XAMPP Apache 設定教學

本 Flask 系統不能直接放進 `htdocs`。建議讓 Launcher 將 Waitress 綁定在 `127.0.0.1:8000`，再由 XAMPP Apache 以 HTTPS reverse proxy 對外服務。

## 1. Launcher 設定

1. Port 設為 `8000`。
2. 不要勾選「允許區域網路連線」，保持 `127.0.0.1`。
3. 編輯專案 `.env`：

```dotenv
SESSION_COOKIE_SECURE=1
TRUST_PROXY=1
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1
```

只有在 Waitress 僅監聽本機且前方代理是本機 Apache 時，才可將 `TRUST_PROXY` 設為 `1`。

## 2. 啟用 Apache 模組

以管理員權限編輯 `C:\xampp\apache\conf\httpd.conf`，確認以下行沒有被 `#` 註解：

```apache
LoadModule proxy_module modules/mod_proxy.so
LoadModule proxy_http_module modules/mod_proxy_http.so
LoadModule headers_module modules/mod_headers.so
LoadModule ssl_module modules/mod_ssl.so
```

同時確認：

```apache
Include conf/extra/httpd-vhosts.conf
```

## 3. VirtualHost 範例

將下列內容依實際網域與憑證修改後，加入 `C:\xampp\apache\conf\extra\httpd-vhosts.conf`：

```apache
<VirtualHost *:443>
    ServerName dorm-staff.example.edu.tw

    SSLEngine on
    SSLCertificateFile "C:/path/to/fullchain.pem"
    SSLCertificateKeyFile "C:/path/to/private-key.pem"

    ProxyPreserveHost On
    ProxyPass        / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    # 覆寫而不是沿用瀏覽器送入的值，避免用戶偽造來源 IP。
    RequestHeader set X-Forwarded-For "expr=%{REMOTE_ADDR}"
    RequestHeader set X-Forwarded-Proto "https"
    RequestHeader set X-Forwarded-Port "443"
    Header always set Strict-Transport-Security "max-age=31536000"

    ErrorLog "logs/dorm-staff-error.log"
    CustomLog "logs/dorm-staff-access.log" combined
</VirtualHost>
```

### X-Forwarded-For 與稽核 IP

系統的安全事件與操作稽核會保存 Flask 看到的 `request.remote_addr`。啟用 `TRUST_PROXY=1` 及 `PROXY_FIX_X_FOR=1` 後，Flask 會把最右側一層可信代理提供的 `X-Forwarded-For` 視為用戶 IP。因此必須同時符合以下條件：

1. Waitress 只監聽 `127.0.0.1`，不可讓外部直接連線 `8000`。
2. Flask 前方恰好只有這一層本機 Apache；`PROXY_FIX_X_FOR=1` 的數字代表可信代理層數，不是開關值。
3. Apache 必須用 `RequestHeader set` 覆寫 `X-Forwarded-For`，不可讓瀏覽器自行傳入的同名標頭原樣進入後端。
4. 若未來在 Apache 前再加入 Cloudflare、負載平衡器或校方反向代理，不可直接把數字改成 `2`；須先由網管確認該代理會清除並重建標頭，才能調整可信層數。

Waitress 3 預設會在 Flask 收到請求前移除未受信任的代理標頭。目前的 Launcher、watchdog 與 `deployment/start-production.ps1` 已固定只信任本機 Apache（`127.0.0.1`），並只接受 `X-Forwarded-For`、`Proto`、`Host` 與 `Port`。若自行用其他命令啟動 Waitress，也必須加入：

```powershell
--trusted-proxy=127.0.0.1 --trusted-proxy-count=1 --trusted-proxy-headers="x-forwarded-for x-forwarded-proto x-forwarded-host x-forwarded-port"
```

不可使用 `--trusted-proxy=*`，也不可讓 Waitress 對外監聽後仍信任任意代理標頭。

可在管理員「稽核紀錄」頁檢查登入事件的 IP。內網測試時應顯示實際用戶端 IP，而不是 `127.0.0.1`；若所有紀錄都是本機 IP，代表 Apache 尚未正確傳送標頭。請勿用公開網站傳入任意 `X-Forwarded-For` 來測試，因為正式設定應覆寫它。

## 4. 檢查與啟動

先在命令提示字元執行：

```powershell
C:\xampp\apache\bin\httpd.exe -t
```

看到 `Syntax OK` 後，才從 XAMPP Control Panel 重啟 Apache。接著以正式 HTTPS 網址測試登入、排班、報表及一份證件預覽／下載。

## 5. 正式上線注意事項

- DNS、TLS 憑證、防火牆與校外存取政策須由校方網管確認。
- 不建議正式使用自簽憑證。
- 不要公開 Waitress 的 `8000` port。
- 稽核紀錄包含 IP 與瀏覽器資訊，應視為管理資料，只授權管理員查閱並納入校方保存政策。
- 若需要開機後無人登入也能運作，可直接在 Launcher 設定巡檢分鐘並按「啟用自啟動巡檢」；Windows 工作排程會以 SYSTEM 身分定期檢查並在必要時啟動服務。
- 完整正式部署與備份說明仍可參考專案的 `deployment\DEPLOYMENT_WINDOWS_XAMPP.md`。
- 系統內建完整備份與自動驗證；管理員可在「設定 → 排班鎖定與備份」選擇每隔幾小時或每天固定時間執行。請在 `.env` 將 `AUTOMATIC_BACKUP_DIR` 指向另一顆受 BitLocker 保護的磁碟。多個 Waitress 程序只會有一個取得維護排程鎖，不會重複建立備份或清理文件。
- 打卡裝置數量不固定，每台都應建立獨立裝置、綁定地點與內網 CIDR，不可共用註冊包或密鑰。Launcher 可選 `HTTPS` 或 `ENCRYPTED_HTTP`；儲存後會安全更新 `.env`，必須重新啟動系統才生效。

### 無法配置 HTTPS 時的打卡 API

若封閉內網暫時無法配置 TLS，可在 Launcher 勾選「啟用上下班打卡服務」，將「打卡傳輸」設為 `ENCRYPTED_HTTP`。Launcher 會寫入：

```dotenv
ATTENDANCE_ENABLED=1
ATTENDANCE_TRANSPORT_MODE=ENCRYPTED_HTTP
ATTENDANCE_REQUIRE_HTTPS=0
```

此模式只允許打卡終端使用：卡號、帳密、打卡時間、結果與姓名以每台裝置的 AES-256-GCM 獨立密鑰雙向加密，並檢查時間窗、防重放 request id、裝置撤銷狀態與 CIDR。首次密鑰不經 HTTP 傳送，而是由管理端下載密碼保護的 `.dormclock` 註冊包，再於指定終端匯入。管理員必須設定 1–168 小時的啟用期限；首次匯入需連上中央主機，成功後權杖立即失效，同一註冊包不可在第二台電腦啟用。

`ENCRYPTED_HTTP` 不會隱藏來源／目的 IP、API 路徑、裝置代碼、流量大小與時間，也不能保護一般瀏覽器頁面。因此仍須：

1. 只在受控 VLAN／內網使用。推薦仍讓 Waitress 綁定 `127.0.0.1`，由 Apache 另開一個只代理 `/attendance-api/` 的內網 Port；不要直接公開 Waitress。
2. 每台終端設定最小範圍 CIDR，不要為方便留空或設成全網。
3. 網頁登入、管理員操作、證件與報表繼續經 Apache HTTPS，不能把一般使用者導向 HTTP Port。
4. 終端遺失時立即在「設定 → 打卡設定」撤銷裝置；重新下載註冊包會輪替密鑰，使舊設定失效。
5. 維持 Windows 自動校時；偏差超過五分鐘的封包會被拒絕。
6. 終端會自動回報 Windows 電腦名稱與 MAC 位址；異動時先核對實體設備，再於「設定 → 打卡設定」確認。MAC 可被偽造，只作設備辨識與警示，授權仍以一次性啟用、裝置密鑰及 CIDR 為準。

若日後能配置憑證，優先切回 `HTTPS`、重啟服務並為各終端重新註冊。切換模式前先確認三台以上或未來新增的所有終端都已停止，避免舊模式佇列在切換期間持續重試。

Apache 可另外建立只供終端使用的內網入口（網段與 Port 請依實際環境修改）：

```apache
Listen 8081
<VirtualHost *:8081>
    ProxyPreserveHost On
    ProxyPass        /attendance-api/ http://127.0.0.1:8000/attendance-api/
    ProxyPassReverse /attendance-api/ http://127.0.0.1:8000/attendance-api/

    RequestHeader set X-Forwarded-For "expr=%{REMOTE_ADDR}"
    RequestHeader set X-Forwarded-Proto "http"

    <Location "/attendance-api/">
        Require ip 192.168.10.0/24
    </Location>
    <LocationMatch "^/(?!attendance-api/)">
        Require all denied
    </LocationMatch>
</VirtualHost>
```

Windows 防火牆也只允許打卡 VLAN 連入 `8081`。此拓撲下可維持本文件前段的 `TRUST_PROXY=1`，因為 Waitress 仍只接受本機 Apache；終端註冊包的中央網址填 `http://主機內網IP:8081`。若選擇讓終端直接連 Waitress，必須改成 `TRUST_PROXY=0`，否則攻擊者可能偽造 `X-Forwarded-For` 規避 CIDR；同時一般登入頁也會出現在 HTTP Port，因此不建議這種做法。
