# 第三方下載來源與驗證

Launcher 僅把 runtime 安裝在自身的 `.venv` 資料夾，不進行系統層級安裝；既有 portable backup 會排除此可重新建立的資料夾。

| 元件 | 來源 | 驗證方式 |
|---|---|---|
| Python 3.12.10 x64 embeddable | `https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip` | 固定 SHA-256：`4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3` |
| pip bootstrap | PyPA `pypa/get-pip` repository 的固定 commit | 固定 SHA-256：`DFE9FD5C28DC98B5AC17979A953EA550CEC37AE1B47A5116007395BFACFF2AB9` |
| PortableGit / MinGit x64 | Git for Windows 官方 GitHub latest release API | 從官方 release asset 的 `digest` 讀取並驗證 SHA-256 |
| Flask 與其他 Python 套件 | 專案 `requirements.txt`，由 pip 經 PyPI 安裝 | pip 使用 HTTPS 及套件 index metadata；版本範圍由專案 requirements 限制 |

下載只允許 HTTPS。Python、pip bootstrap 與 Git ZIP 在解壓或執行前都會進行 SHA-256 比對；ZIP 解壓另有路徑穿越檢查。

本啟動器本身使用 Windows .NET Framework WinForms，不額外安裝 .NET runtime。正式大量發佈前，建議由學校資訊單位以 Code Signing 憑證簽署 `DormStaffLauncher.exe`。
