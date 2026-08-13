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
- 系統內建每日完整備份與自動驗證；請在 `.env` 將 `AUTOMATIC_BACKUP_DIR` 指向另一顆受 BitLocker 保護的磁碟。管理員可在「設定 → 排班鎖定與備份」查看最近成功／失敗結果並手動重跑。多個 Waitress 程序只會有一個取得維護排程鎖，不會重複建立備份或清理文件。
