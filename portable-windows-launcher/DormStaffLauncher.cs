using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace DormStaffPortable
{
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;
            ServicePointManager.Expect100Continue = true;
            PortableEnvironment environment = new PortableEnvironment();
            if (args.Length > 0 && String.Equals(args[0], "--diagnose", StringComparison.OrdinalIgnoreCase))
            {
                string diagnosticPath = args.Length > 1
                    ? Path.GetFullPath(args[1])
                    : Path.Combine(environment.BaseDirectory, "launcher-diagnostic.txt");
                File.WriteAllText(diagnosticPath, environment.DiagnosticText(), new UTF8Encoding(false));
                Environment.Exit(environment.ProjectIsValid ? 0 : 2);
                return;
            }
            if (args.Length > 0 && String.Equals(args[0], "--install-headless", StringComparison.OrdinalIgnoreCase))
            {
                string logPath = Path.Combine(environment.BaseDirectory, "headless-install.log");
                try
                {
                    PortableManager manager = new PortableManager(environment, delegate(string message)
                    {
                        File.AppendAllText(logPath, "[" + DateTime.Now.ToString("HH:mm:ss") + "] " + message + Environment.NewLine, Encoding.UTF8);
                    });
                    manager.InstallOrRepair();
                    File.AppendAllText(logPath, "INSTALL_RESULT=SUCCESS" + Environment.NewLine, Encoding.UTF8);
                    Environment.Exit(0);
                }
                catch (Exception ex)
                {
                    File.AppendAllText(logPath, "INSTALL_RESULT=FAILED" + Environment.NewLine + ex.ToString() + Environment.NewLine, Encoding.UTF8);
                    Environment.Exit(1);
                }
                return;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string updateResult = "";
            foreach (string argument in args)
                if (argument.StartsWith("--update-result=", StringComparison.OrdinalIgnoreCase)) updateResult = argument.Substring(16);
            Application.Run(new LauncherForm(environment, updateResult));
        }
    }

    internal sealed class LauncherConfig
    {
        public string ProjectPath = "..";
        public string Port = "8000";
        public string ListenAddress = "127.0.0.1";
        public string GitRemote = "origin";
        public string GitBranch = "main";
        public string RepositoryUrl = "https://github.com/MiniDora1122/yzudorm-staff-system";
        public int WatchdogIntervalMinutes = 5;
        public bool AutoStartEnabled;
        public bool OpenBrowserAfterStart = true;

        public static LauncherConfig Load(string path)
        {
            LauncherConfig config = new LauncherConfig();
            if (!File.Exists(path)) return config;
            foreach (string rawLine in File.ReadAllLines(path, Encoding.UTF8))
            {
                string line = rawLine.Trim();
                if (line.Length == 0 || line.StartsWith("#") || line.StartsWith(";")) continue;
                int separator = line.IndexOf('=');
                if (separator <= 0) continue;
                string key = line.Substring(0, separator).Trim();
                string value = line.Substring(separator + 1).Trim();
                if (key.Equals("ProjectPath", StringComparison.OrdinalIgnoreCase)) config.ProjectPath = value;
                else if (key.Equals("Port", StringComparison.OrdinalIgnoreCase)) config.Port = value;
                else if (key.Equals("ListenAddress", StringComparison.OrdinalIgnoreCase)) config.ListenAddress = value;
                else if (key.Equals("GitRemote", StringComparison.OrdinalIgnoreCase)) config.GitRemote = value;
                else if (key.Equals("GitBranch", StringComparison.OrdinalIgnoreCase)) config.GitBranch = value;
                else if (key.Equals("RepositoryUrl", StringComparison.OrdinalIgnoreCase)) config.RepositoryUrl = value;
                else if (key.Equals("WatchdogIntervalMinutes", StringComparison.OrdinalIgnoreCase))
                {
                    int minutes;
                    if (Int32.TryParse(value, out minutes) && minutes >= 1 && minutes <= 1440)
                        config.WatchdogIntervalMinutes = minutes;
                }
                else if (key.Equals("AutoStartEnabled", StringComparison.OrdinalIgnoreCase))
                    config.AutoStartEnabled = value == "1" || value.Equals("true", StringComparison.OrdinalIgnoreCase);
                else if (key.Equals("OpenBrowserAfterStart", StringComparison.OrdinalIgnoreCase))
                    config.OpenBrowserAfterStart = value == "1" || value.Equals("true", StringComparison.OrdinalIgnoreCase);
            }
            return config;
        }

        public void Save(string path)
        {
            string[] lines = {
                "# DormStaffLauncher portable settings. Relative paths are resolved from Launcher.exe.",
                "ProjectPath=" + ProjectPath,
                "Port=" + Port,
                "ListenAddress=" + ListenAddress,
                "GitRemote=" + GitRemote,
                "GitBranch=" + GitBranch,
                "RepositoryUrl=" + RepositoryUrl,
                "WatchdogIntervalMinutes=" + WatchdogIntervalMinutes,
                "AutoStartEnabled=" + (AutoStartEnabled ? "1" : "0"),
                "OpenBrowserAfterStart=" + (OpenBrowserAfterStart ? "1" : "0")
            };
            File.WriteAllLines(path, lines, new UTF8Encoding(false));
        }
    }

    internal sealed class PortableEnvironment
    {
        public readonly string BaseDirectory;
        public readonly string ConfigPath;
        public readonly string RuntimeDirectory;
        public readonly string DownloadsDirectory;
        public readonly string LogsDirectory;
        public readonly string PythonDirectory;
        public readonly string GitDirectory;
        public readonly LauncherConfig Config;

        public PortableEnvironment()
        {
            BaseDirectory = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
            ConfigPath = Path.Combine(BaseDirectory, "launcher.ini");
            RuntimeDirectory = Path.Combine(BaseDirectory, ".venv");
            DownloadsDirectory = Path.Combine(RuntimeDirectory, "downloads");
            LogsDirectory = Path.Combine(RuntimeDirectory, "logs");
            PythonDirectory = Path.Combine(RuntimeDirectory, "python");
            GitDirectory = Path.Combine(RuntimeDirectory, "git");
            Config = LauncherConfig.Load(ConfigPath);
        }

        public string ProjectRoot
        {
            get
            {
                string configured = String.IsNullOrWhiteSpace(Config.ProjectPath) ? ".." : Config.ProjectPath;
                if (!Path.IsPathRooted(configured)) configured = Path.Combine(BaseDirectory, configured);
                return Path.GetFullPath(configured);
            }
        }

        public string PythonExe { get { return Path.Combine(PythonDirectory, "python.exe"); } }
        public string GitExe { get { return Path.Combine(GitDirectory, "cmd", "git.exe"); } }
        public string RequirementsFile { get { return Path.Combine(ProjectRoot, "requirements.txt"); } }
        public string WsgiFile { get { return Path.Combine(ProjectRoot, "wsgi.py"); } }
        public string EnvFile { get { return Path.Combine(ProjectRoot, ".env"); } }
        public bool ProjectIsValid { get { return File.Exists(RequirementsFile) && File.Exists(WsgiFile); } }
        public bool PythonIsInstalled { get { return File.Exists(PythonExe); } }
        public bool GitIsInstalled { get { return File.Exists(GitExe); } }

        public string DiagnosticText()
        {
            StringBuilder text = new StringBuilder();
            text.AppendLine("LauncherBase=" + BaseDirectory);
            text.AppendLine("ProjectRoot=" + ProjectRoot);
            text.AppendLine("ProjectValid=" + ProjectIsValid);
            text.AppendLine("PythonExe=" + PythonExe);
            text.AppendLine("PythonInstalled=" + PythonIsInstalled);
            text.AppendLine("GitExe=" + GitExe);
            text.AppendLine("GitInstalled=" + GitIsInstalled);
            text.AppendLine("Listen=" + Config.ListenAddress + ":" + Config.Port);
            return text.ToString();
        }

        public string MakePortableProjectPath(string selectedPath)
        {
            string full = Path.GetFullPath(selectedPath).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            Uri baseUri = new Uri(BaseDirectory.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar);
            Uri targetUri = new Uri(full);
            if (baseUri.Scheme == targetUri.Scheme)
            {
                string relative = Uri.UnescapeDataString(baseUri.MakeRelativeUri(targetUri).ToString()).Replace('/', Path.DirectorySeparatorChar);
                if (!relative.Contains(":")) return relative.TrimEnd(Path.DirectorySeparatorChar);
            }
            return selectedPath;
        }
    }

    internal sealed class CommandResult
    {
        public int ExitCode;
        public string Output;
    }

    internal sealed class FreshSetupOptions
    {
        public string Username;
        public string DisplayName;
        public string Password;
        public bool DeleteOutputs;
    }

    internal sealed class MigrationSourceInfo
    {
        public string SourceType;
        public string Revision;
        public int Users;
        public int Staff;
        public int Shifts;
        public int Documents;
        public string Summary;
    }

    internal sealed class PortableManager
    {
        private const string PythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip";
        private const string PythonSha256 = "4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3";
        private const string GetPipUrl = "https://github.com/pypa/get-pip/raw/dbf0c85f76fb6e1ab42aa672ffca6f0a675d9ee4/public/get-pip.py";
        private const string GetPipSha256 = "DFE9FD5C28DC98B5AC17979A953EA550CEC37AE1B47A5116007395BFACFF2AB9";
        private const string GitReleaseApi = "https://api.github.com/repos/git-for-windows/git/releases/latest";
        private readonly PortableEnvironment environment;
        private readonly Action<string> log;

        public PortableManager(PortableEnvironment environment, Action<string> log)
        {
            this.environment = environment;
            this.log = log;
        }

        public void InstallOrRepair()
        {
            EnsureSupportedWindows();
            Directory.CreateDirectory(environment.RuntimeDirectory);
            Directory.CreateDirectory(environment.DownloadsDirectory);
            Directory.CreateDirectory(environment.LogsDirectory);
            EnsurePython();
            EnsurePip();
            EnsureGit();
            if (!environment.ProjectIsValid)
            {
                log("Python、pip 與 PortableGit 已完成。尚未選擇有效專案，可先使用 Clone。 / Runtime ready; choose or clone a project next.");
                return;
            }
            ConfigureProjectImportPath();
            EnsureEnvironmentFile();
            RunChecked(environment.PythonExe, "-m pip install --disable-pip-version-check --no-warn-script-location --upgrade pip", environment.ProjectRoot);
            RunChecked(environment.PythonExe, "-m pip install --disable-pip-version-check --no-warn-script-location -r " + Quote(environment.RequirementsFile), environment.ProjectRoot);
            UpgradeDatabase();
            int activeAdmins = CountActiveAdministrators();
            if (activeAdmins == 0)
                log("尚未建立管理員。請使用紅色「全新初始化」建立第一位管理員。 / No administrator exists; use Fresh database setup next.");
            RunChecked(environment.PythonExe, "-c \"from app import create_app; create_app(); print('Application import OK')\"", environment.ProjectRoot);
            log("安裝與檢查完成。 / Installation and verification completed.");
        }

        private void EnsureSupportedWindows()
        {
            if (Environment.OSVersion.Platform != PlatformID.Win32NT)
                throw new InvalidOperationException("此啟動器僅支援 Windows。 / Windows is required.");
            if (!Environment.Is64BitOperatingSystem)
                throw new InvalidOperationException("此版本需要 64 位元 Windows。 / 64-bit Windows is required.");
        }

        public void ValidateProject()
        {
            if (!environment.ProjectIsValid)
                throw new InvalidOperationException("找不到 Flask 專案。請按「選擇專案資料夾」。\r\nProject files requirements.txt and wsgi.py were not found at: " + environment.ProjectRoot);
        }

        private void EnsurePython()
        {
            if (environment.PythonIsInstalled)
            {
                log("已找到可攜式 Python。 / Portable Python found.");
                return;
            }
            string archive = Path.Combine(environment.DownloadsDirectory, "python-3.12.10-embed-amd64.zip");
            Download(PythonUrl, archive, "下載 Python 3.12.10");
            VerifySha256(archive, PythonSha256);
            string staging = environment.PythonDirectory + ".installing";
            SafeRecreateDirectory(staging);
            ExtractZipSafely(archive, staging);
            string pth = Path.Combine(staging, "python312._pth");
            if (!File.Exists(pth)) throw new InvalidOperationException("Python runtime 內容不完整。 / Invalid Python archive.");
            string pthText = File.ReadAllText(pth, Encoding.UTF8)
                .Replace("#import site", "import site");
            if (!pthText.Contains("Lib\\site-packages")) pthText += Environment.NewLine + "Lib\\site-packages" + Environment.NewLine;
            File.WriteAllText(pth, pthText, new UTF8Encoding(false));
            if (Directory.Exists(environment.PythonDirectory)) Directory.Delete(environment.PythonDirectory, true);
            Directory.Move(staging, environment.PythonDirectory);
            log("Python 安裝完成（僅在本資料夾）。 / Portable Python installed locally.");
        }

        private void EnsurePip()
        {
            string workingDirectory = environment.ProjectIsValid ? environment.ProjectRoot : environment.BaseDirectory;
            CommandResult check = Run(environment.PythonExe, "-m pip --version", workingDirectory, false);
            if (check.ExitCode == 0)
            {
                log("pip 已可使用。 / pip is available.");
                return;
            }
            string getPip = Path.Combine(environment.DownloadsDirectory, "get-pip.py");
            Download(GetPipUrl, getPip, "下載固定版本的官方 pip bootstrap");
            VerifySha256(getPip, GetPipSha256);
            RunChecked(environment.PythonExe, Quote(getPip) + " --disable-pip-version-check", workingDirectory);
        }

        public void ConfigureProjectImportPath()
        {
            ValidateProject();
            if (!environment.PythonIsInstalled) throw new InvalidOperationException("Portable Python 尚未安裝。 ");
            string sitePackages = Path.Combine(environment.PythonDirectory, "Lib", "site-packages");
            Directory.CreateDirectory(sitePackages);
            string projectPth = Path.Combine(sitePackages, "dorm_staff_project.pth");
            File.WriteAllText(projectPth, environment.ProjectRoot + Environment.NewLine, new UTF8Encoding(false));
            log("已同步目前專案路徑。 / Portable project path synchronized.");
        }

        private void EnsureGit()
        {
            if (environment.GitIsInstalled)
            {
                log("已找到 PortableGit。 / PortableGit found.");
                return;
            }
            log("查詢 Git for Windows 最新正式版… / Checking latest Git for Windows release...");
            string json;
            using (WebClient client = CreateWebClient()) json = client.DownloadString(GitReleaseApi);
            Match asset = Regex.Match(json,
                "\\\"browser_download_url\\\"\\s*:\\s*\\\"(?<url>https://github\\.com/git-for-windows/git/releases/download/[^\\\"]+/MinGit-[^\\\"]+-64-bit\\.zip)\\\"[\\s\\S]{0,900}?\\\"digest\\\"\\s*:\\s*\\\"sha256:(?<sha>[a-fA-F0-9]{64})\\\"",
                RegexOptions.IgnoreCase);
            if (!asset.Success)
            {
                asset = Regex.Match(json,
                    "\\\"digest\\\"\\s*:\\s*\\\"sha256:(?<sha>[a-fA-F0-9]{64})\\\"[\\s\\S]{0,900}?\\\"browser_download_url\\\"\\s*:\\s*\\\"(?<url>https://github\\.com/git-for-windows/git/releases/download/[^\\\"]+/MinGit-[^\\\"]+-64-bit\\.zip)\\\"",
                    RegexOptions.IgnoreCase);
            }
            if (!asset.Success)
                throw new InvalidOperationException("無法從 Git for Windows 官方 release 找到含 SHA-256 的 64-bit MinGit。請稍後重試。");
            string url = asset.Groups["url"].Value.Replace("\\/", "/");
            string archive = Path.Combine(environment.DownloadsDirectory, "MinGit-64-bit.zip");
            Download(url, archive, "下載官方 PortableGit");
            VerifySha256(archive, asset.Groups["sha"].Value);
            string staging = environment.GitDirectory + ".installing";
            SafeRecreateDirectory(staging);
            ExtractZipSafely(archive, staging);
            if (!File.Exists(Path.Combine(staging, "cmd", "git.exe")))
                throw new InvalidOperationException("PortableGit runtime 內容不完整。 / Invalid MinGit archive.");
            if (Directory.Exists(environment.GitDirectory)) Directory.Delete(environment.GitDirectory, true);
            Directory.Move(staging, environment.GitDirectory);
            log("PortableGit 安裝完成（僅在本資料夾）。 / PortableGit installed locally.");
        }

        private void EnsureEnvironmentFile()
        {
            bool created = !File.Exists(environment.EnvFile);
            string content;
            if (created)
            {
                string template = Path.Combine(environment.ProjectRoot, ".env.example");
                if (!File.Exists(template)) template = Path.Combine(environment.ProjectRoot, ".env.production.example");
                if (!File.Exists(template)) throw new InvalidOperationException("找不到 .env 範本。 / No .env template was found.");
                content = File.ReadAllText(template, Encoding.UTF8);
                string secret = Convert.ToBase64String(RandomBytes(48)).Replace("+", "-").Replace("/", "_").TrimEnd('=');
                content = Regex.Replace(content, "(?m)^SECRET_KEY=.*$", "SECRET_KEY=" + secret);
                content = Regex.Replace(content, "(?m)^SESSION_COOKIE_SECURE=.*$", "SESSION_COOKIE_SECURE=0");
                content = Regex.Replace(content, "(?m)^TRUST_PROXY=.*$", "TRUST_PROXY=0");
            }
            else
            {
                content = File.ReadAllText(environment.EnvFile, Encoding.UTF8);
            }

            string normalized = Regex.Replace(
                content,
                "(?im)^\\s*DATABASE_URL\\s*=\\s*[\\\"']?sqlite:///instance/dorm_staff\\.db[\\\"']?\\s*$",
                "DATABASE_URL=sqlite:///dorm_staff.db"
            );
            bool upgradedLegacyDatabasePath = !normalized.Equals(content, StringComparison.Ordinal);
            if (created || upgradedLegacyDatabasePath)
                File.WriteAllText(environment.EnvFile, normalized, new UTF8Encoding(false));

            Directory.CreateDirectory(Path.Combine(environment.ProjectRoot, "instance"));
            BackupEnvironmentFile(created);
            if (created)
                log("已建立 .env、產生隨機密鑰並保存復原備份。若使用 XAMPP HTTPS，請依教學調整。 / .env created and securely backed up.");
            else if (upgradedLegacyDatabasePath)
                log("已自動修正舊版 SQLite 路徑，資料庫將建立於 instance\\dorm_staff.db。 / Legacy SQLite path upgraded.");
        }

        private void BackupEnvironmentFile(bool overwrite)
        {
            if (!File.Exists(environment.EnvFile))
                throw new InvalidOperationException("無法備份不存在的 .env。 / Cannot back up a missing .env file.");
            string backupDirectory = Path.Combine(environment.ProjectRoot, "instance", "private_keys", "backup");
            string backupPath = Path.Combine(backupDirectory, "application-env.backup");
            if (!overwrite && File.Exists(backupPath)) return;

            Directory.CreateDirectory(backupDirectory);
            string temporaryPath = backupPath + ".tmp-" + Guid.NewGuid().ToString("N");
            try
            {
                File.Copy(environment.EnvFile, temporaryPath, true);
                byte[] sourceHash;
                byte[] backupHash;
                using (SHA256 sha = SHA256.Create()) sourceHash = sha.ComputeHash(File.ReadAllBytes(environment.EnvFile));
                using (SHA256 sha = SHA256.Create()) backupHash = sha.ComputeHash(File.ReadAllBytes(temporaryPath));
                if (!HashesEqual(sourceHash, backupHash))
                    throw new IOException(".env 備份驗證失敗。 / Environment backup verification failed.");
                if (File.Exists(backupPath)) File.SetAttributes(backupPath, FileAttributes.Normal);
                File.Copy(temporaryPath, backupPath, true);
                File.SetAttributes(backupPath, File.GetAttributes(backupPath) | FileAttributes.Hidden | FileAttributes.NotContentIndexed);
            }
            finally
            {
                if (File.Exists(temporaryPath)) File.Delete(temporaryPath);
            }
            log("SECRET_KEY 與環境設定備份已更新。 / SECRET_KEY and environment backup updated.");
        }

        private static bool HashesEqual(byte[] left, byte[] right)
        {
            if (left == null || right == null || left.Length != right.Length) return false;
            int difference = 0;
            for (int i = 0; i < left.Length; i++) difference |= left[i] ^ right[i];
            return difference == 0;
        }

        public void UpgradeDatabase()
        {
            ValidateProject();
            if (!environment.PythonIsInstalled) throw new InvalidOperationException("請先安裝 portable runtime。 / Install the runtime first.");
            EnsureEnvironmentFile();
            RunChecked(environment.PythonExe, "-m flask --app wsgi.py db upgrade", environment.ProjectRoot);
            log("資料庫 migration 完成。 / Database migrations completed.");
        }

        public int PrepareDatabaseForStart()
        {
            ValidateProject();
            if (!environment.PythonIsInstalled)
                throw new InvalidOperationException("請先安裝 portable runtime。 / Install the portable runtime first.");
            ConfigureProjectImportPath();
            UpgradeDatabase();
            return CountActiveAdministrators();
        }

        private int CountActiveAdministrators()
        {
            string code = "from app import create_app; from app.extensions import db; from app.models import User,Role; a=create_app(); c=a.app_context(); c.push(); print('ACTIVE_ADMIN_COUNT='+str(db.session.scalar(db.select(db.func.count()).select_from(User).where(User.role==Role.ADMIN,User.is_active.is_(True))) or 0)); c.pop()";
            CommandResult result = RunChecked(environment.PythonExe, "-c " + Quote(code), environment.ProjectRoot);
            int count;
            if (!Int32.TryParse(FindOutputValue(result.Output, "ACTIVE_ADMIN_COUNT="), out count))
                throw new InvalidOperationException("無法確認管理員帳號狀態。 / Could not inspect administrator accounts.");
            return count;
        }

        public void ResetDatabaseAndCreateFirstAdmin(FreshSetupOptions options)
        {
            ValidateProject();
            if (!environment.PythonIsInstalled)
                throw new InvalidOperationException("請先完成「安裝／修復環境」。 / Install the portable runtime first.");
            if (!File.Exists(environment.EnvFile))
                throw new InvalidOperationException("找不到 .env，請先完成環境安裝。 / .env was not found.");
            EnsureConfiguredServiceIsStopped();
            ConfigureProjectImportPath();
            ValidatePortableSqliteDatabase();

            string bootstrapScript = Path.Combine(environment.BaseDirectory, "bootstrap_first_admin.py");
            if (!File.Exists(bootstrapScript))
                throw new InvalidOperationException("找不到建立管理員的安全腳本：" + bootstrapScript);
            string[] unfinished = Directory.GetDirectories(environment.ProjectRoot, ".dorm-reset-staging-*");
            if (unfinished.Length > 0)
                throw new InvalidOperationException("偵測到先前未完成的重置暫存資料。為避免覆蓋可復原資料，請先交由系統管理者檢查：\r\n" + unfinished[0]);

            string instance = Path.Combine(environment.ProjectRoot, "instance");
            string staging = Path.Combine(environment.ProjectRoot, ".dorm-reset-staging-" + Guid.NewGuid().ToString("N"));
            string oldInstance = Path.Combine(staging, "instance-old");
            string oldEnv = Path.Combine(staging, "env-old");
            Directory.CreateDirectory(staging);
            File.Copy(environment.EnvFile, oldEnv, true);
            bool movedOldInstance = false;
            try
            {
                if (Directory.Exists(instance))
                {
                    Directory.Move(instance, oldInstance);
                    movedOldInstance = true;
                }
                Directory.CreateDirectory(instance);
                RotateApplicationSecrets();
                UpgradeDatabase();

                string payload = "{\"username_b64\":\"" + Base64(options.Username.Trim().ToLowerInvariant())
                    + "\",\"display_name_b64\":\"" + Base64(options.DisplayName.Trim())
                    + "\",\"password_b64\":\"" + Base64(options.Password) + "\"}";
                RunWithStandardInputChecked(
                    environment.PythonExe,
                    Quote(bootstrapScript),
                    environment.ProjectRoot,
                    payload
                );
                Directory.Delete(staging, true);
            }
            catch (Exception resetError)
            {
                string rollbackError = "";
                try
                {
                    if (Directory.Exists(instance)) Directory.Delete(instance, true);
                    if (movedOldInstance && Directory.Exists(oldInstance)) Directory.Move(oldInstance, instance);
                    if (File.Exists(oldEnv)) File.Copy(oldEnv, environment.EnvFile, true);
                    if (Directory.Exists(staging)) Directory.Delete(staging, true);
                }
                catch (Exception rollbackException)
                {
                    rollbackError = "\r\n自動還原也失敗，請保留現場並聯絡系統管理者：" + rollbackException.Message;
                }
                throw new InvalidOperationException("全新初始化失敗；系統已嘗試還原舊資料。\r\n" + resetError.Message + rollbackError, resetError);
            }

            if (options.DeleteOutputs)
            {
                string outputs = Path.Combine(environment.ProjectRoot, "outputs");
                try
                {
                    if (Directory.Exists(outputs)) Directory.Delete(outputs, true);
                    log("已清除舊匯出報表。 / Previous exported reports removed.");
                }
                catch (Exception outputError)
                {
                    log("WARNING: 資料庫已重置，但 outputs 清理失敗，請人工檢查：" + outputError.Message);
                }
            }
            log("全新初始化完成，第一位管理員已建立：" + options.Username.Trim().ToLowerInvariant());
        }

        private void EnsureConfiguredServiceIsStopped()
        {
            int port;
            if (!Int32.TryParse(environment.Config.Port, out port)) return;
            using (TcpClient client = new TcpClient())
            {
                try
                {
                    IAsyncResult connection = client.BeginConnect(IPAddress.Loopback, port, null, null);
                    if (connection.AsyncWaitHandle.WaitOne(250) && client.Connected)
                        throw new InvalidOperationException("Port " + port + " 仍有服務執行。請先停止 Launcher、排程或其他 Waitress 服務後再重置。");
                }
                catch (SocketException) { }
            }
        }

        private void ValidatePortableSqliteDatabase()
        {
            string code = "from app import create_app; from app.extensions import db; a=create_app(); c=a.app_context(); c.push(); print('RESET_BACKEND='+db.engine.url.get_backend_name()); print('RESET_DB='+str(db.engine.url.database)); print('RESET_DOC='+str(a.config['DOCUMENT_STORAGE_DIR'])); print('RESET_KEYS='+str(a.config['DOCUMENT_KEY_DIR'])); print('RESET_KEY_BACKUP='+str(a.config['DOCUMENT_KEY_BACKUP_DIR'])); c.pop()";
            CommandResult result = RunChecked(environment.PythonExe, "-c " + Quote(code), environment.ProjectRoot);
            string backend = FindOutputValue(result.Output, "RESET_BACKEND=");
            string database = FindOutputValue(result.Output, "RESET_DB=");
            if (!backend.Equals("sqlite", StringComparison.OrdinalIgnoreCase) || String.IsNullOrWhiteSpace(database))
                throw new InvalidOperationException("全新初始化僅支援 portable SQLite；目前資料庫為 " + backend + "。 ");
            string instanceRoot = Path.GetFullPath(Path.Combine(environment.ProjectRoot, "instance")).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            string databasePath = Path.GetFullPath(database);
            if (!databasePath.StartsWith(instanceRoot, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("為避免誤刪外部資料，僅能重置專案 instance 內的 SQLite：" + databasePath);
            foreach (string marker in new[] { "RESET_DOC=", "RESET_KEYS=", "RESET_KEY_BACKUP=" })
            {
                string configuredPath = FindOutputValue(result.Output, marker);
                string resolvedPath = String.IsNullOrWhiteSpace(configuredPath)
                    ? ""
                    : Path.GetFullPath(Path.IsPathRooted(configuredPath) ? configuredPath : Path.Combine(environment.ProjectRoot, configuredPath));
                if (String.IsNullOrWhiteSpace(resolvedPath) || !resolvedPath.StartsWith(instanceRoot, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("為避免遺留或誤刪外部敏感檔案，重置要求證件與金鑰都位於專案 instance 內：" + configuredPath);
            }
        }

        private void RotateApplicationSecrets()
        {
            string content = File.ReadAllText(environment.EnvFile, Encoding.UTF8);
            string secret = Convert.ToBase64String(RandomBytes(48)).Replace("+", "-").Replace("/", "_").TrimEnd('=');
            if (Regex.IsMatch(content, "(?m)^SECRET_KEY=.*$")) content = Regex.Replace(content, "(?m)^SECRET_KEY=.*$", "SECRET_KEY=" + secret);
            else content += Environment.NewLine + "SECRET_KEY=" + secret + Environment.NewLine;
            if (Regex.IsMatch(content, "(?m)^DOCUMENT_ENCRYPTION_KEY=.*$")) content = Regex.Replace(content, "(?m)^DOCUMENT_ENCRYPTION_KEY=.*$", "DOCUMENT_ENCRYPTION_KEY=");
            else content += "DOCUMENT_ENCRYPTION_KEY=" + Environment.NewLine;
            content = Regex.Replace(content, "(?m)^DATABASE_URL=sqlite:///instance/dorm_staff\\.db$", "DATABASE_URL=sqlite:///dorm_staff.db");
            File.WriteAllText(environment.EnvFile, content, new UTF8Encoding(false));
            BackupEnvironmentFile(true);
            log("已輪替 session 與文件加密密鑰，並更新復原備份。 / Application secrets rotated and backed up.");
        }

        private static string FindOutputValue(string output, string prefix)
        {
            foreach (string line in output.Replace("\r", "").Split('\n'))
                if (line.StartsWith(prefix, StringComparison.Ordinal)) return line.Substring(prefix.Length).Trim();
            return "";
        }

        private static string Base64(string value)
        {
            return Convert.ToBase64String(Encoding.UTF8.GetBytes(value));
        }

        public void CloneProject(string destination)
        {
            if (!environment.GitIsInstalled) throw new InvalidOperationException("請先按「安裝／修復環境」安裝 PortableGit。 ");
            if (String.IsNullOrWhiteSpace(environment.Config.RepositoryUrl)) throw new InvalidOperationException("請先輸入 Git Repository URL。 ");
            Uri repository;
            if (!Uri.TryCreate(environment.Config.RepositoryUrl, UriKind.Absolute, out repository) || repository.Scheme != Uri.UriSchemeHttps)
                throw new InvalidOperationException("為避免憑證外洩，Clone 僅接受 HTTPS repository URL。 ");
            if (Directory.Exists(destination) && Directory.GetFileSystemEntries(destination).Length > 0)
                throw new InvalidOperationException("Clone 目的資料夾必須不存在或為空白。 ");
            Directory.CreateDirectory(destination);
            RunChecked(environment.GitExe, "clone --branch " + Quote(environment.Config.GitBranch) + " --single-branch " + Quote(environment.Config.RepositoryUrl) + " " + Quote(destination), environment.BaseDirectory);
            log("專案 Clone 完成。請選擇該專案資料夾後安裝環境。 ");
        }

        public void PrepareGitUpdate()
        {
            ValidateProject();
            if (!environment.GitIsInstalled || !environment.PythonIsInstalled)
                throw new InvalidOperationException("請先完成環境安裝。 / Install the runtime first.");
            string root = RunChecked(environment.GitExe, "rev-parse --show-toplevel", environment.ProjectRoot).Output.Trim();
            if (!PathsEqual(root, environment.ProjectRoot)) throw new InvalidOperationException("Git repository root 與所選專案資料夾不一致。 ");
            string dirty = RunChecked(environment.GitExe, "status --porcelain", environment.ProjectRoot).Output.Trim();
            if (dirty.Length > 0) throw new InvalidOperationException("Git 工作目錄有未提交變更，為保護資料已停止更新：\r\n" + dirty);
            string branch = RunChecked(environment.GitExe, "branch --show-current", environment.ProjectRoot).Output.Trim();
            if (!branch.Equals(environment.Config.GitBranch, StringComparison.Ordinal))
                throw new InvalidOperationException("目前分支是 " + branch + "，不是設定的 " + environment.Config.GitBranch + "。 ");
            string backupScript = Path.Combine(environment.ProjectRoot, "deployment", "create_portable_backup.py");
            if (!File.Exists(backupScript)) throw new InvalidOperationException("更新前備份腳本不存在，已停止更新。 ");
            string backupDir = Path.Combine(environment.ProjectRoot, "outputs", "portable-backups");
            Directory.CreateDirectory(backupDir);
            string backup = Path.Combine(backupDir, "before-launcher-update-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".zip");
            RunChecked(environment.PythonExe, Quote(backupScript) + " " + Quote(backup), environment.ProjectRoot);
            log("更新前備份：" + backup);
            RunChecked(environment.GitExe, "fetch " + Quote(environment.Config.GitRemote) + " " + Quote(environment.Config.GitBranch), environment.ProjectRoot);
            log("Git 更新已下載並驗證；即將關閉 Launcher 後套用。 / Update fetched and validated.");
        }

        private string MigrationHelperPath()
        {
            string helper = Path.Combine(environment.BaseDirectory, "migrate_portable_data.py");
            if (!File.Exists(helper))
                throw new InvalidOperationException("找不到資料移轉 helper：" + helper);
            return helper;
        }

        public MigrationSourceInfo InspectMigrationSource(string source)
        {
            ValidateProject();
            if (!environment.PythonIsInstalled)
                throw new InvalidOperationException("請先完成安裝／修復環境。 / Install the portable runtime first.");
            ConfigureProjectImportPath();
            CommandResult result = RunChecked(
                environment.PythonExe,
                Quote(MigrationHelperPath()) + " inspect --project-root " + Quote(environment.ProjectRoot) + " --source " + Quote(source),
                environment.ProjectRoot
            );
            MigrationSourceInfo info = new MigrationSourceInfo();
            info.SourceType = FindOutputValue(result.Output, "MIGRATION_SOURCE_TYPE=");
            info.Revision = FindOutputValue(result.Output, "MIGRATION_REVISION=");
            Int32.TryParse(FindOutputValue(result.Output, "MIGRATION_USERS="), out info.Users);
            Int32.TryParse(FindOutputValue(result.Output, "MIGRATION_STAFF="), out info.Staff);
            Int32.TryParse(FindOutputValue(result.Output, "MIGRATION_SHIFTS="), out info.Shifts);
            Int32.TryParse(FindOutputValue(result.Output, "MIGRATION_DOCUMENTS="), out info.Documents);
            string encodedSummary = FindOutputValue(result.Output, "MIGRATION_SUMMARY_B64=");
            try { info.Summary = Encoding.UTF8.GetString(Convert.FromBase64String(encodedSummary)); }
            catch (FormatException) { throw new InvalidOperationException("無法解析來源資料摘要。 / Invalid migration summary."); }
            if (String.IsNullOrWhiteSpace(info.SourceType) || String.IsNullOrWhiteSpace(info.Revision) || String.IsNullOrWhiteSpace(info.Summary))
                throw new InvalidOperationException("來源驗證未傳回完整資訊。 / Incomplete migration inspection result.");
            return info;
        }

        public void RestorePortableData(string source)
        {
            ValidateProject();
            if (!environment.PythonIsInstalled)
                throw new InvalidOperationException("請先完成安裝／修復環境。 / Install the portable runtime first.");
            EnsureConfiguredServiceIsStopped();
            ConfigureProjectImportPath();
            CommandResult result = RunChecked(
                environment.PythonExe,
                Quote(MigrationHelperPath()) + " restore --project-root " + Quote(environment.ProjectRoot) + " --source " + Quote(source),
                environment.ProjectRoot
            );
            if (!FindOutputValue(result.Output, "MIGRATION_RESULT=").Equals("SUCCESS", StringComparison.Ordinal))
                throw new InvalidOperationException("資料移轉未回報成功。 / Migration did not report success.");
            try { BackupEnvironmentFile(true); }
            catch (Exception backupError) { log("WARNING: 資料已移轉，但無法設定 .env 備份的 Windows 隱藏屬性：" + backupError.Message); }
            string previousBackup = FindOutputValue(result.Output, "MIGRATION_PREVIOUS_BACKUP=");
            if (!String.IsNullOrWhiteSpace(previousBackup) && !previousBackup.Equals("NONE", StringComparison.OrdinalIgnoreCase))
                log("移轉前系統備份：" + previousBackup);
            log("資料移轉與驗證完成；來源檔案仍保留。 / Data migration completed; source retained.");
        }

        public void ExportPortableBackup(string destination)
        {
            ValidateProject();
            if (!environment.PythonIsInstalled)
                throw new InvalidOperationException("請先完成安裝／修復環境。 / Install the portable runtime first.");
            EnsureConfiguredServiceIsStopped();
            string backupScript = Path.Combine(environment.ProjectRoot, "deployment", "create_portable_backup.py");
            if (!File.Exists(backupScript)) throw new InvalidOperationException("找不到完整備份程式。 / Backup helper not found.");
            string parent = Path.GetDirectoryName(Path.GetFullPath(destination));
            if (String.IsNullOrWhiteSpace(parent)) throw new InvalidOperationException("備份目的路徑無效。 / Invalid backup destination.");
            Directory.CreateDirectory(parent);
            RunChecked(environment.PythonExe, Quote(backupScript) + " " + Quote(destination) + " --allow-running", environment.ProjectRoot);
            if (!File.Exists(destination)) throw new InvalidOperationException("備份程式未產生檔案。 / Backup file was not created.");
            log("完整系統備份已建立：" + destination);
        }

        public void ConfigureWatchdog(bool enable, int intervalMinutes)
        {
            if (enable)
            {
                ValidateProject();
                if (!environment.PythonIsInstalled)
                    throw new InvalidOperationException("請先完成安裝／修復環境。 / Install the portable runtime first.");
            }
            string script = Path.Combine(environment.BaseDirectory, "configure-watchdog-task.ps1");
            if (!File.Exists(script)) throw new InvalidOperationException("找不到自啟動設定程式：" + script);
            string mode = enable ? "Enable" : "Disable";
            ProcessStartInfo info = new ProcessStartInfo(
                "powershell.exe",
                "-NoProfile -ExecutionPolicy Bypass -File " + Quote(script) + " -Mode " + mode + " -IntervalMinutes " + intervalMinutes
            );
            info.UseShellExecute = true;
            info.Verb = "runas";
            info.WindowStyle = ProcessWindowStyle.Hidden;
            using (Process process = Process.Start(info))
            {
                process.WaitForExit();
                if (process.ExitCode != 0)
                    throw new InvalidOperationException("Windows 工作排程設定失敗（exit " + process.ExitCode + "）。");
            }
            log(enable
                ? "自啟動巡檢已啟用，每 " + intervalMinutes + " 分鐘檢查一次。 / Auto-start watchdog enabled."
                : "自啟動巡檢已停用；目前執行中的系統不會被停止。 / Auto-start watchdog disabled.");
        }

        public void StopConfiguredServer()
        {
            string script = Path.Combine(environment.BaseDirectory, "stop-server.ps1");
            if (!File.Exists(script)) throw new InvalidOperationException("找不到停止系統程式：" + script);
            ProcessStartInfo info = new ProcessStartInfo(
                "powershell.exe",
                "-NoProfile -ExecutionPolicy Bypass -File " + Quote(script) + " -ConfigPath " + Quote(environment.ConfigPath)
            );
            info.UseShellExecute = true;
            info.Verb = "runas";
            info.WindowStyle = ProcessWindowStyle.Hidden;
            using (Process process = Process.Start(info))
            {
                process.WaitForExit();
                if (process.ExitCode != 0)
                    throw new InvalidOperationException("系統停止失敗（exit " + process.ExitCode + "）。可能是 Port 被其他程式占用。");
            }
        }

        public CommandResult RunChecked(string executable, string arguments, string workingDirectory)
        {
            CommandResult result = Run(executable, arguments, workingDirectory, true);
            if (result.ExitCode != 0) throw new InvalidOperationException("指令執行失敗（exit " + result.ExitCode + "）：\r\n" + executable + " " + arguments + "\r\n" + result.Output);
            return result;
        }

        private CommandResult RunWithStandardInputChecked(string executable, string arguments, string workingDirectory, string standardInput)
        {
            CommandResult result = Run(executable, arguments, workingDirectory, true, standardInput);
            if (result.ExitCode != 0)
                throw new InvalidOperationException("安全初始化指令失敗（exit " + result.ExitCode + "）：\r\n" + result.Output);
            return result;
        }

        private CommandResult Run(string executable, string arguments, string workingDirectory, bool writeLog)
        {
            return Run(executable, arguments, workingDirectory, writeLog, null);
        }

        private CommandResult Run(string executable, string arguments, string workingDirectory, bool writeLog, string standardInput)
        {
            if (PathsEqual(executable, environment.PythonExe)) arguments = "-X utf8 " + arguments;
            if (writeLog) log("> " + Path.GetFileName(executable) + " " + arguments);
            ProcessStartInfo info = new ProcessStartInfo(executable, arguments);
            info.WorkingDirectory = workingDirectory;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.RedirectStandardInput = standardInput != null;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            using (Process process = Process.Start(info))
            {
                StringBuilder output = new StringBuilder();
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) { lock (output) output.AppendLine(e.Data); if (writeLog) log(e.Data); } };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) { lock (output) output.AppendLine(e.Data); if (writeLog) log(e.Data); } };
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                if (standardInput != null)
                {
                    process.StandardInput.Write(standardInput);
                    process.StandardInput.Close();
                }
                process.WaitForExit();
                return new CommandResult { ExitCode = process.ExitCode, Output = output.ToString() };
            }
        }

        private static WebClient CreateWebClient()
        {
            WebClient client = new WebClient();
            client.Headers.Add(HttpRequestHeader.UserAgent, "DormStaffPortableLauncher/1.0");
            return client;
        }

        private void Download(string url, string destination, string label)
        {
            Uri uri = new Uri(url);
            if (uri.Scheme != Uri.UriSchemeHttps) throw new InvalidOperationException("拒絕非 HTTPS 下載：" + url);
            Directory.CreateDirectory(Path.GetDirectoryName(destination));
            string partial = destination + ".partial";
            if (File.Exists(partial)) File.Delete(partial);
            log(label + "：" + uri.Host);
            using (WebClient client = CreateWebClient()) client.DownloadFile(uri, partial);
            if (File.Exists(destination)) File.Delete(destination);
            File.Move(partial, destination);
        }

        private void VerifySha256(string path, string expected)
        {
            using (SHA256 sha = SHA256.Create())
            using (FileStream input = File.OpenRead(path))
            {
                string actual = BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "");
                if (!actual.Equals(expected, StringComparison.OrdinalIgnoreCase))
                {
                    try { File.Delete(path); } catch (IOException) { }
                    throw new InvalidOperationException("下載檔案 SHA-256 驗證失敗，已拒絕使用。 / Download hash verification failed; the file was rejected.");
                }
            }
            log("SHA-256 驗證成功。 / SHA-256 verified.");
        }

        private static void ExtractZipSafely(string archive, string destination)
        {
            string root = Path.GetFullPath(destination).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            using (ZipArchive zip = ZipFile.OpenRead(archive))
            {
                foreach (ZipArchiveEntry entry in zip.Entries)
                {
                    string target = Path.GetFullPath(Path.Combine(destination, entry.FullName));
                    if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("ZIP 內含不安全路徑。 ");
                    if (String.IsNullOrEmpty(entry.Name)) Directory.CreateDirectory(target);
                    else
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(target));
                        entry.ExtractToFile(target, true);
                    }
                }
            }
        }

        private static void SafeRecreateDirectory(string path)
        {
            if (Directory.Exists(path)) Directory.Delete(path, true);
            Directory.CreateDirectory(path);
        }

        private static byte[] RandomBytes(int count)
        {
            byte[] data = new byte[count];
            using (RandomNumberGenerator rng = RandomNumberGenerator.Create()) rng.GetBytes(data);
            return data;
        }

        private static bool PathsEqual(string left, string right)
        {
            return Path.GetFullPath(left).TrimEnd(Path.DirectorySeparatorChar).Equals(Path.GetFullPath(right).TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase);
        }

        public static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }
    }

    internal sealed class FreshSetupForm : Form
    {
        private readonly TextBox usernameBox = new TextBox();
        private readonly TextBox displayNameBox = new TextBox();
        private readonly TextBox passwordBox = new TextBox();
        private readonly TextBox confirmationBox = new TextBox();
        private readonly TextBox resetPhraseBox = new TextBox();
        private readonly CheckBox deleteOutputsCheck = new CheckBox();
        private readonly CheckBox showPasswordCheck = new CheckBox();
        public FreshSetupOptions Options;

        public FreshSetupForm()
        {
            Text = "全新初始化 / Fresh database setup";
            ClientSize = new Size(580, 520);
            MinimumSize = new Size(580, 520);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            AutoScaleMode = AutoScaleMode.Dpi;
            Font = new Font("Microsoft JhengHei UI", 9F);

            TableLayoutPanel grid = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(20), ColumnCount = 2, RowCount = 9 };
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 145));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 92));
            for (int i = 1; i <= 5; i++) grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 42));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 35));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 42));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

            Label warning = new Label {
                Text = "警告：這會永久刪除目前的 SQLite、所有帳號、排班、證件、session 與舊文件金鑰，且不建立舊資料備份。\r\nThis permanently removes all current application data.",
                ForeColor = Color.FromArgb(176, 35, 43), Font = new Font(Font.FontFamily, 9.5F, FontStyle.Bold), Dock = DockStyle.Fill, AutoSize = false
            };
            grid.Controls.Add(warning, 0, 0); grid.SetColumnSpan(warning, 2);
            AddField(grid, 1, "管理員帳號\r\nUsername", usernameBox);
            AddField(grid, 2, "顯示名稱\r\nDisplay name", displayNameBox);
            passwordBox.UseSystemPasswordChar = true;
            confirmationBox.UseSystemPasswordChar = true;
            AddField(grid, 3, "管理員密碼\r\nPassword", passwordBox);
            AddField(grid, 4, "再次輸入密碼\r\nConfirm", confirmationBox);
            AddField(grid, 5, "輸入 RESET\r\nType RESET", resetPhraseBox);

            showPasswordCheck.Text = "顯示密碼 Show password"; showPasswordCheck.AutoSize = true;
            showPasswordCheck.CheckedChanged += delegate { passwordBox.UseSystemPasswordChar = !showPasswordCheck.Checked; confirmationBox.UseSystemPasswordChar = !showPasswordCheck.Checked; };
            grid.Controls.Add(showPasswordCheck, 1, 6);
            deleteOutputsCheck.Text = "同時刪除 outputs 內的舊匯出報表 / Delete previous exported reports";
            deleteOutputsCheck.Checked = true; deleteOutputsCheck.AutoSize = true;
            grid.Controls.Add(deleteOutputsCheck, 0, 7); grid.SetColumnSpan(deleteOutputsCheck, 2);

            FlowLayoutPanel buttons = new FlowLayoutPanel { FlowDirection = FlowDirection.RightToLeft, Dock = DockStyle.Fill, Padding = new Padding(0, 9, 0, 0) };
            Button reset = new Button { Text = "永久重置並建立管理員\r\nReset and create admin", Size = new Size(210, 52), BackColor = Color.FromArgb(185, 46, 52), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
            reset.FlatAppearance.BorderSize = 0; reset.Click += ValidateAndClose;
            Button cancel = new Button { Text = "取消 Cancel", Size = new Size(110, 52), DialogResult = DialogResult.Cancel };
            buttons.Controls.Add(reset); buttons.Controls.Add(cancel);
            grid.Controls.Add(buttons, 0, 8); grid.SetColumnSpan(buttons, 2);
            Controls.Add(grid);
            CancelButton = cancel;
        }

        private static void AddField(TableLayoutPanel grid, int row, string label, TextBox input)
        {
            Label caption = new Label { Text = label, AutoSize = true, Anchor = AnchorStyles.Left };
            input.Dock = DockStyle.Fill; input.Margin = new Padding(3, 7, 3, 7);
            input.MaxLength = row == 1 ? 80 : row == 2 ? 100 : 128;
            grid.Controls.Add(caption, 0, row); grid.Controls.Add(input, 1, row);
        }

        private void ValidateAndClose(object sender, EventArgs args)
        {
            string username = usernameBox.Text.Trim();
            string displayName = displayNameBox.Text.Trim();
            string password = passwordBox.Text;
            if (!Regex.IsMatch(username, "^[A-Za-z0-9._-]{3,80}$")) { ShowValidation("帳號需為 3–80 位英數字、句點、底線或連字號。"); return; }
            if (displayName.Length < 1 || displayName.Length > 100) { ShowValidation("顯示名稱需為 1–100 字。"); return; }
            if (password.Length < 8 || password.Length > 128) { ShowValidation("密碼長度需為 8–128 個字元。"); return; }
            if (password != confirmationBox.Text) { ShowValidation("兩次輸入的密碼不一致。"); return; }
            if (resetPhraseBox.Text != "RESET") { ShowValidation("請正確輸入大寫 RESET 才能繼續。"); return; }
            Options = new FreshSetupOptions { Username = username, DisplayName = displayName, Password = password, DeleteOutputs = deleteOutputsCheck.Checked };
            DialogResult = DialogResult.OK;
            Close();
        }

        private void ShowValidation(string message)
        {
            MessageBox.Show(this, message, "資料不完整 / Invalid input", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    internal sealed class MigrationConfirmationForm : Form
    {
        private readonly TextBox phraseBox = new TextBox();

        public MigrationConfirmationForm(string source, MigrationSourceInfo info)
        {
            Text = "資料移轉確認 / Confirm data migration";
            ClientSize = new Size(650, 460);
            MinimumSize = new Size(650, 460);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            AutoScaleMode = AutoScaleMode.Dpi;
            Font = new Font("Microsoft JhengHei UI", 9F);

            TableLayoutPanel grid = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(20), ColumnCount = 1, RowCount = 7 };
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 50));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 50));
            grid.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 58));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 28));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
            grid.RowStyles.Add(new RowStyle(SizeType.Absolute, 48));

            grid.Controls.Add(new Label {
                Text = "目前目的系統的資料會被整套取代。Launcher 會先建立完整備份，失敗時自動回復；來源檔案不會刪除。\r\nCurrent data will be replaced after a safety backup.",
                ForeColor = Color.FromArgb(176, 35, 43), Font = new Font(Font.FontFamily, 9.5F, FontStyle.Bold), Dock = DockStyle.Fill
            }, 0, 0);
            grid.Controls.Add(new Label { Text = "來源 / Source：" + source, AutoEllipsis = true, Dock = DockStyle.Fill, Padding = new Padding(0, 8, 0, 0) }, 0, 1);
            grid.Controls.Add(new TextBox { Text = info.Summary, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill, BackColor = Color.White }, 0, 2);
            grid.Controls.Add(new Label {
                Text = "完整 ZIP 會同時還原證件與金鑰；單一 DB 不會。此功能不合併兩套資料。\r\nFull ZIP restores documents and keys; database-only import does not merge data.",
                Dock = DockStyle.Fill, Padding = new Padding(0, 8, 0, 0)
            }, 0, 3);
            grid.Controls.Add(new Label { Text = "請輸入大寫 MIGRATE 確認 / Type MIGRATE to confirm", Dock = DockStyle.Fill }, 0, 4);
            phraseBox.Dock = DockStyle.Fill; phraseBox.MaxLength = 7;
            grid.Controls.Add(phraseBox, 0, 5);

            FlowLayoutPanel buttons = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.RightToLeft };
            Button migrate = new Button { Text = "開始移轉 / Migrate", Width = 150, Height = 34, BackColor = Color.FromArgb(139, 31, 38), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
            Button cancel = new Button { Text = "取消 / Cancel", Width = 110, Height = 34, DialogResult = DialogResult.Cancel };
            migrate.Click += delegate
            {
                if (phraseBox.Text != "MIGRATE")
                {
                    MessageBox.Show(this, "請正確輸入大寫 MIGRATE。", "尚未確認", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                DialogResult = DialogResult.OK;
                Close();
            };
            buttons.Controls.Add(migrate); buttons.Controls.Add(cancel); grid.Controls.Add(buttons, 0, 6);
            AcceptButton = migrate; CancelButton = cancel; Controls.Add(grid);
        }
    }

    internal sealed class LauncherForm : Form
    {
        private readonly PortableEnvironment environment;
        private readonly PortableManager manager;
        private readonly Label statusLabel = new Label();
        private readonly TextBox projectBox = new TextBox();
        private readonly TextBox portBox = new TextBox();
        private readonly TextBox repositoryBox = new TextBox();
        private readonly TextBox branchBox = new TextBox();
        private readonly NumericUpDown watchdogMinutesBox = new NumericUpDown();
        private readonly CheckBox lanCheck = new CheckBox();
        private readonly CheckBox browserCheck = new CheckBox();
        private readonly RichTextBox logBox = new RichTextBox();
        private readonly List<Button> actionButtons = new List<Button>();
        private readonly System.Windows.Forms.Timer statusTimer = new System.Windows.Forms.Timer();
        private Process serverProcess;
        private bool operationRunning;
        private bool updateInProgress;
        private bool? lastRunningState;
        private long watchdogLogPosition;

        public LauncherForm(PortableEnvironment environment, string updateResult)
        {
            this.environment = environment;
            manager = new PortableManager(environment, WriteLog);
            Text = "宿舍工讀生系統啟動器 / Dorm Staff Launcher";
            Width = 980;
            Height = 900;
            MinimumSize = new Size(850, 620);
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            Font = new Font("Microsoft JhengHei UI", 9F);
            BackColor = Color.FromArgb(244, 247, 251);
            FormClosing += OnFormClosing;
            BuildUi();
            LoadSettingsIntoUi();
            UpdateStatus();
            ImportWatchdogLog();
            statusTimer.Interval = 2000;
            statusTimer.Tick += delegate { UpdateStatus(); ImportWatchdogLog(); };
            statusTimer.Start();
            WriteLog("Launcher 路徑：" + environment.BaseDirectory);
            WriteLog("專案路徑：" + environment.ProjectRoot);
            if (!String.IsNullOrWhiteSpace(updateResult))
            {
                string updateLog = Path.Combine(environment.LogsDirectory, "self-update.log");
                WriteLog(updateResult.Equals("success", StringComparison.OrdinalIgnoreCase)
                    ? "Git 安全更新完成，已開啟新版 Launcher。 / Safe update completed."
                    : "Git 安全更新失敗；詳細資訊請查看：" + updateLog);
                Shown += delegate { MessageBox.Show(this,
                    updateResult.Equals("success", StringComparison.OrdinalIgnoreCase)
                        ? "Git 安全更新完成，Launcher 與相關檔案均已更新。\r\nSafe update completed."
                        : "Git 安全更新失敗，已重新開啟 Launcher。請查看 self-update.log。\r\nSafe update failed.",
                    "Git 安全更新 / Safe update", MessageBoxButtons.OK,
                    updateResult.Equals("success", StringComparison.OrdinalIgnoreCase) ? MessageBoxIcon.Information : MessageBoxIcon.Error); };
            }
        }

        private void BuildUi()
        {
            Panel header = new Panel { Dock = DockStyle.Top, Height = 82, BackColor = Color.FromArgb(14, 55, 104), Padding = new Padding(24, 14, 24, 10) };
            Label title = new Label { Text = "宿舍工讀生系統啟動器", ForeColor = Color.White, Font = new Font(Font.FontFamily, 18F, FontStyle.Bold), AutoSize = true, Location = new Point(22, 12) };
            Label subtitle = new Label { Text = "Portable setup, update and service control", ForeColor = Color.FromArgb(190, 215, 242), Font = new Font(Font.FontFamily, 9F), AutoSize = true, Location = new Point(25, 48) };
            statusLabel.AutoSize = false; statusLabel.Width = 230; statusLabel.Dock = DockStyle.Right; statusLabel.Padding = new Padding(0, 0, 14, 0); statusLabel.TextAlign = ContentAlignment.MiddleRight; statusLabel.ForeColor = Color.White; statusLabel.Font = new Font(Font.FontFamily, 10F, FontStyle.Bold);
            header.Controls.Add(title); header.Controls.Add(subtitle); header.Controls.Add(statusLabel);
            Controls.Add(header);

            TableLayoutPanel body = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(18), ColumnCount = 2, RowCount = 2 };
            body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 48)); body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 52));
            body.RowStyles.Add(new RowStyle(SizeType.Absolute, 470)); body.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            Controls.Add(body); body.BringToFront();

            GroupBox settings = new GroupBox { Text = "設定 / Settings", Dock = DockStyle.Fill, Padding = new Padding(14) };
            TableLayoutPanel settingGrid = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 7 };
            settingGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 110)); settingGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); settingGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 76));
            for (int i = 0; i < 6; i++) settingGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
            settingGrid.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            AddSettingRow(settingGrid, 0, "專案資料夾", projectBox, MakeButton("選擇", delegate { ChooseProject(); }));
            AddSettingRow(settingGrid, 1, "監聽 Port", portBox, null);
            AddSettingRow(settingGrid, 2, "Git 分支", branchBox, null);
            AddSettingRow(settingGrid, 3, "Git URL", repositoryBox, MakeButton("Clone", delegate { CloneProject(); }));
            lanCheck.Text = "允許區域網路連線（0.0.0.0；需自行設定防火牆）"; lanCheck.AutoSize = true;
            settingGrid.Controls.Add(lanCheck, 1, 4); settingGrid.SetColumnSpan(lanCheck, 2);
            browserCheck.Text = "啟動後自動開啟瀏覽器"; browserCheck.AutoSize = true;
            settingGrid.Controls.Add(browserCheck, 1, 5);
            Button save = MakeButton("儲存", delegate { SaveSettings(); }); settingGrid.Controls.Add(save, 2, 5);
            watchdogMinutesBox.Minimum = 1; watchdogMinutesBox.Maximum = 1440; watchdogMinutesBox.DecimalPlaces = 0;
            AddSettingRow(settingGrid, 6, "巡檢分鐘", watchdogMinutesBox, null);
            settings.Controls.Add(settingGrid); body.Controls.Add(settings, 0, 0);

            GroupBox actions = new GroupBox { Text = "操作 / Actions", Dock = DockStyle.Fill, Padding = new Padding(14) };
            TableLayoutPanel actionGrid = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 7 };
            actionGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50)); actionGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
            for (int i = 0; i < 7; i++) actionGrid.RowStyles.Add(new RowStyle(SizeType.Percent, 14.285F));
            AddAction(actionGrid, 0, 0, "① 安裝／修復環境\r\nInstall / Repair", Color.FromArgb(21, 101, 192), delegate { RunBackground("安裝環境", manager.InstallOrRepair); });
            AddAction(actionGrid, 1, 0, "② 啟動系統\r\nStart", Color.FromArgb(24, 135, 84), StartServer);
            AddAction(actionGrid, 0, 1, "停止系統\r\nStop", Color.FromArgb(185, 46, 52), StopServer);
            AddAction(actionGrid, 1, 1, "開啟系統\r\nOpen", Color.FromArgb(60, 89, 140), OpenBrowser);
            AddAction(actionGrid, 0, 2, "Git 安全更新\r\nSafe update", Color.FromArgb(94, 67, 160), BeginGitUpdate);
            AddAction(actionGrid, 1, 2, "資料庫升級\r\nDatabase upgrade", Color.FromArgb(75, 101, 122), delegate { RunBackground("資料庫升級", manager.UpgradeDatabase); });
            AddAction(actionGrid, 0, 3, "編輯 .env\r\nEdit settings", Color.FromArgb(75, 101, 122), delegate { OpenTextFile(environment.EnvFile); });
            AddAction(actionGrid, 1, 3, "XAMPP 設定教學\r\nSetup guide", Color.FromArgb(210, 125, 35), delegate { OpenTextFile(Path.Combine(environment.BaseDirectory, "XAMPP_GUIDE.md")); });
            AddAction(actionGrid, 0, 4, "匯出／備份系統\r\nExport / Backup", Color.FromArgb(0, 112, 123), ExportSystemBackup);
            AddAction(actionGrid, 1, 4, "移轉／還原資料\r\nMigrate / Restore", Color.FromArgb(130, 88, 20), MigrateOrRestoreData);
            AddAction(actionGrid, 0, 5, "啟用自啟動巡檢\r\nEnable auto-start", Color.FromArgb(24, 135, 84), delegate { ConfigureWatchdog(true); });
            AddAction(actionGrid, 1, 5, "停用自啟動巡檢\r\nDisable auto-start", Color.FromArgb(100, 107, 117), delegate { ConfigureWatchdog(false); });
            AddAction(actionGrid, 0, 6, "全新初始化：清空資料並建立第一位管理員\r\nFresh database setup", Color.FromArgb(139, 31, 38), FreshDatabaseSetup);
            actionGrid.SetColumnSpan(actionButtons[actionButtons.Count - 1], 2);
            actions.Controls.Add(actionGrid); body.Controls.Add(actions, 1, 0);

            GroupBox logs = new GroupBox { Text = "執行紀錄 / Log", Dock = DockStyle.Fill, Padding = new Padding(10) };
            logBox.Dock = DockStyle.Fill; logBox.ReadOnly = true; logBox.BackColor = Color.FromArgb(24, 31, 42); logBox.ForeColor = Color.FromArgb(220, 230, 240); logBox.Font = new Font("Consolas", 9F); logBox.BorderStyle = BorderStyle.None;
            logs.Controls.Add(logBox); body.Controls.Add(logs, 0, 1); body.SetColumnSpan(logs, 2);
        }

        private void AddSettingRow(TableLayoutPanel grid, int row, string label, Control input, Control button)
        {
            Label caption = new Label { Text = label, AutoSize = true, Anchor = AnchorStyles.Left };
            input.Dock = DockStyle.Fill; input.Margin = new Padding(3, 6, 3, 6);
            grid.Controls.Add(caption, 0, row); grid.Controls.Add(input, 1, row); if (button != null) grid.Controls.Add(button, 2, row);
        }

        private Button MakeButton(string text, EventHandler click)
        {
            Button button = new Button { Text = text, Dock = DockStyle.Fill, FlatStyle = FlatStyle.Flat, Margin = new Padding(4) };
            button.Click += click; return button;
        }

        private void AddAction(TableLayoutPanel grid, int column, int row, string text, Color color, EventHandler click)
        {
            Button button = MakeButton(text, click); button.BackColor = color; button.ForeColor = Color.White; button.FlatAppearance.BorderSize = 0; button.Font = new Font(Font.FontFamily, 9.5F, FontStyle.Bold); actionButtons.Add(button); grid.Controls.Add(button, column, row);
        }

        private void LoadSettingsIntoUi()
        {
            projectBox.Text = environment.ProjectRoot;
            portBox.Text = environment.Config.Port;
            branchBox.Text = environment.Config.GitBranch;
            repositoryBox.Text = environment.Config.RepositoryUrl;
            lanCheck.Checked = environment.Config.ListenAddress == "0.0.0.0";
            browserCheck.Checked = environment.Config.OpenBrowserAfterStart;
            watchdogMinutesBox.Value = environment.Config.WatchdogIntervalMinutes;
        }

        private bool SaveSettings()
        {
            int port;
            if (!Int32.TryParse(portBox.Text.Trim(), out port) || port < 1 || port > 65535) { MessageBox.Show("Port 必須介於 1–65535。 ", "設定錯誤", MessageBoxButtons.OK, MessageBoxIcon.Warning); return false; }
            if (!Regex.IsMatch(branchBox.Text.Trim(), "^[A-Za-z0-9._/-]+$")) { MessageBox.Show("Git 分支名稱格式不正確。 ", "設定錯誤", MessageBoxButtons.OK, MessageBoxIcon.Warning); return false; }
            environment.Config.ProjectPath = environment.MakePortableProjectPath(projectBox.Text.Trim());
            environment.Config.Port = port.ToString();
            environment.Config.ListenAddress = lanCheck.Checked ? "0.0.0.0" : "127.0.0.1";
            environment.Config.GitBranch = branchBox.Text.Trim();
            environment.Config.RepositoryUrl = repositoryBox.Text.Trim();
            environment.Config.WatchdogIntervalMinutes = Decimal.ToInt32(watchdogMinutesBox.Value);
            environment.Config.OpenBrowserAfterStart = browserCheck.Checked;
            environment.Config.Save(environment.ConfigPath);
            WriteLog("設定已儲存。 / Settings saved.");
            UpdateStatus();
            return true;
        }

        private void ChooseProject()
        {
            using (FolderBrowserDialog dialog = new FolderBrowserDialog())
            {
                dialog.Description = "選擇包含 wsgi.py 與 requirements.txt 的專案資料夾";
                dialog.SelectedPath = Directory.Exists(projectBox.Text) ? projectBox.Text : environment.BaseDirectory;
                if (dialog.ShowDialog(this) == DialogResult.OK) { projectBox.Text = dialog.SelectedPath; SaveSettings(); }
            }
        }

        private void CloneProject()
        {
            if (!SaveSettings()) return;
            using (FolderBrowserDialog dialog = new FolderBrowserDialog())
            {
                dialog.Description = "選擇空白資料夾作為 Clone 目的地";
                if (dialog.ShowDialog(this) == DialogResult.OK)
                {
                    string destination = dialog.SelectedPath;
                    RunBackground("Clone 專案", delegate { manager.CloneProject(destination); BeginInvoke((MethodInvoker)delegate { projectBox.Text = destination; SaveSettings(); }); });
                }
            }
        }

        private void ExportSystemBackup(object sender, EventArgs args)
        {
            if (operationRunning) return;
            if (!SaveSettings()) return;
            if (!environment.ProjectIsValid || !environment.PythonIsInstalled)
            {
                MessageBox.Show("請先完成安裝／修復環境。", "尚未就緒", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            using (SaveFileDialog dialog = new SaveFileDialog())
            {
                dialog.Title = "匯出完整系統備份 / Export full system backup";
                dialog.Filter = "Portable backup ZIP (*.zip)|*.zip";
                dialog.DefaultExt = "zip";
                dialog.AddExtension = true;
                dialog.FileName = "dorm-staff-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".zip";
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                string destination = dialog.FileName;
                StopServer();
                RunBackground("完整系統備份", delegate { manager.ExportPortableBackup(destination); });
            }
        }

        private void MigrateOrRestoreData(object sender, EventArgs args)
        {
            if (operationRunning) return;
            if (!SaveSettings()) return;
            if (!environment.ProjectIsValid || !environment.PythonIsInstalled)
            {
                MessageBox.Show("請先完成安裝／修復環境。", "尚未就緒", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            using (OpenFileDialog dialog = new OpenFileDialog())
            {
                dialog.Title = "選擇完整備份或 SQLite / Select backup or database";
                dialog.Filter = "支援的資料來源 (*.zip;*.db;*.sqlite;*.sqlite3)|*.zip;*.db;*.sqlite;*.sqlite3|Portable backup ZIP (*.zip)|*.zip|SQLite (*.db;*.sqlite;*.sqlite3)|*.db;*.sqlite;*.sqlite3";
                dialog.CheckFileExists = true;
                dialog.Multiselect = false;
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                string source = dialog.FileName;
                MigrationSourceInfo info;
                Cursor previousCursor = Cursor;
                try
                {
                    Cursor = Cursors.WaitCursor;
                    WriteLog("正在驗證移轉來源（不會修改資料）… / Inspecting source…");
                    info = manager.InspectMigrationSource(source);
                }
                catch (Exception ex)
                {
                    MessageBox.Show(this, ex.Message, "來源驗證失敗 / Invalid source", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }
                finally { Cursor = previousCursor; }
                using (MigrationConfirmationForm confirmation = new MigrationConfirmationForm(source, info))
                {
                    if (confirmation.ShowDialog(this) != DialogResult.OK) return;
                }
                StopServer();
                RunBackground("資料移轉／還原", delegate { manager.RestorePortableData(source); });
            }
        }

        private void ConfigureWatchdog(bool enable)
        {
            if (operationRunning || !SaveSettings()) return;
            try
            {
                manager.ConfigureWatchdog(enable, environment.Config.WatchdogIntervalMinutes);
                environment.Config.AutoStartEnabled = enable;
                environment.Config.Save(environment.ConfigPath);
                MessageBox.Show(
                    this,
                    enable
                        ? "自啟動巡檢已啟用。Windows 登入後會顯示 Launcher，並在背景定期檢查系統。\r\nAuto-start watchdog and Launcher enabled."
                        : "自啟動巡檢已停用。目前已執行的系統不會被停止。\r\nAuto-start watchdog disabled.",
                    "完成 / Completed",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );
            }
            catch (Exception ex)
            {
                WriteLog("ERROR: " + ex.Message);
                MessageBox.Show(this, ex.Message, "自啟動設定失敗 / Auto-start setup failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void RunBackground(string name, Action operation)
        {
            if (operationRunning) return;
            if (!SaveSettings()) return;
            operationRunning = true; SetActionsEnabled(false); WriteLog("--- " + name + " ---");
            ThreadPool.QueueUserWorkItem(delegate
            {
                try { operation(); BeginInvoke((MethodInvoker)delegate { MessageBox.Show(this, name + "完成。", "完成", MessageBoxButtons.OK, MessageBoxIcon.Information); }); }
                catch (Exception ex) { WriteLog("ERROR: " + ex.Message); BeginInvoke((MethodInvoker)delegate { MessageBox.Show(this, ex.Message, name + "失敗", MessageBoxButtons.OK, MessageBoxIcon.Error); }); }
                finally { BeginInvoke((MethodInvoker)delegate { operationRunning = false; SetActionsEnabled(true); UpdateStatus(); }); }
            });
        }

        private void BeginGitUpdate(object sender, EventArgs args)
        {
            if (operationRunning || !SaveSettings()) return;
            string updater = Path.Combine(environment.BaseDirectory, "self-update.ps1");
            if (!File.Exists(updater))
            {
                MessageBox.Show(this, "找不到 Launcher 外部更新程式：" + updater, "無法更新", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            DialogResult confirmation = MessageBox.Show(this,
                "更新時會暫停系統、關閉 Launcher，完成後自動開啟新版 Launcher。是否繼續？\r\nThe server and Launcher will restart during update.",
                "Git 安全更新 / Safe update", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2);
            if (confirmation != DialogResult.Yes) return;
            string marker = Path.Combine(environment.RuntimeDirectory, "update-in-progress");
            Directory.CreateDirectory(environment.RuntimeDirectory);
            File.WriteAllText(marker, DateTime.UtcNow.ToString("O"), Encoding.ASCII);
            StopServer();
            operationRunning = true; SetActionsEnabled(false); WriteLog("--- Git 安全更新 ---");
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    manager.PrepareGitUpdate();
                    BeginInvoke((MethodInvoker)StartExternalUpdaterAndClose);
                }
                catch (Exception ex)
                {
                    try { File.Delete(marker); } catch { }
                    WriteLog("ERROR: " + ex.Message);
                    BeginInvoke((MethodInvoker)delegate
                    {
                        operationRunning = false; SetActionsEnabled(true); UpdateStatus();
                        MessageBox.Show(this, ex.Message, "Git 更新失敗", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    });
                }
            });
        }

        private void StartExternalUpdaterAndClose()
        {
            string source = Path.Combine(environment.BaseDirectory, "self-update.ps1");
            if (!File.Exists(source)) throw new InvalidOperationException("找不到 Launcher 外部更新程式：" + source);
            string temporary = Path.Combine(Path.GetTempPath(), "DormStaffSelfUpdate-" + Guid.NewGuid().ToString("N") + ".ps1");
            File.Copy(source, temporary, true);
            ProcessStartInfo info = new ProcessStartInfo("powershell.exe",
                "-NoProfile -ExecutionPolicy Bypass -File " + PortableManager.Quote(temporary) +
                " -LauncherDirectory " + PortableManager.Quote(environment.BaseDirectory) +
                " -ParentProcessId " + Process.GetCurrentProcess().Id);
            info.UseShellExecute = false; info.CreateNoWindow = true;
            Process.Start(info);
            updateInProgress = true;
            Close();
        }

        private void FreshDatabaseSetup(object sender, EventArgs args)
        {
            if (operationRunning) return;
            if (!SaveSettings()) return;
            if (!environment.ProjectIsValid || !environment.PythonIsInstalled)
            {
                MessageBox.Show("請先完成「安裝／修復環境」。", "尚未就緒", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            FreshSetupOptions options;
            using (FreshSetupForm dialog = new FreshSetupForm())
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                options = dialog.Options;
            }
            DialogResult finalConfirmation = MessageBox.Show(
                this,
                "最後確認：即將永久刪除目前所有系統資料、帳號、排班、證件與金鑰，並建立新的管理員「" + options.Username.Trim().ToLowerInvariant() + "」。\r\n\r\n此操作不能復原，是否確定繼續？",
                "永久重置 / Irreversible reset",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Stop,
                MessageBoxDefaultButton.Button2
            );
            if (finalConfirmation != DialogResult.Yes) return;
            RunBackground("全新初始化", delegate { StopServer(); manager.ResetDatabaseAndCreateFirstAdmin(options); });
        }

        private void StartServer(object sender, EventArgs args)
        {
            if (!SaveSettings()) return;
            if (!environment.ProjectIsValid || !environment.PythonIsInstalled)
            {
                MessageBox.Show("請先選擇專案並執行「安裝／修復環境」。", "尚未安裝", MessageBoxButtons.OK, MessageBoxIcon.Warning); return;
            }
            int administratorCount;
            try { administratorCount = manager.PrepareDatabaseForStart(); }
            catch (Exception ex) { MessageBox.Show(ex.Message + "\r\n\r\n請先執行「安裝／修復環境」。", "資料庫初始化失敗", MessageBoxButtons.OK, MessageBoxIcon.Error); return; }
            if (administratorCount == 0)
            {
                DialogResult createAdministrator = MessageBox.Show(
                    this,
                    "資料庫已自動建立並完成 migration，但目前沒有可登入的管理員。\r\n\r\n是否現在建立第一位管理員？\r\nThe database is ready, but no administrator exists.",
                    "需要建立管理員 / Administrator required",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Information
                );
                if (createAdministrator == DialogResult.Yes) FreshDatabaseSetup(sender, args);
                return;
            }
            if (GetRunningServerProcess() != null) { MessageBox.Show("系統已在執行。 "); return; }
            int port = Int32.Parse(environment.Config.Port);
            if (!PortIsAvailable(port, environment.Config.ListenAddress == "0.0.0.0")) { MessageBox.Show("Port " + port + " 已被其他程式使用。請更換 Port。 ", "無法啟動", MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
            string logFile = Path.Combine(environment.LogsDirectory, "server-" + DateTime.Now.ToString("yyyy-MM-dd") + ".log");
            Directory.CreateDirectory(environment.LogsDirectory);
            ProcessStartInfo info = new ProcessStartInfo(environment.PythonExe, "-m waitress --listen=" + environment.Config.ListenAddress + ":" + port + " --threads=8 --ident=dorm-staff-system wsgi:app");
            info.WorkingDirectory = environment.ProjectRoot; info.UseShellExecute = false; info.CreateNoWindow = true; info.RedirectStandardOutput = true; info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8; info.StandardErrorEncoding = Encoding.UTF8; info.EnvironmentVariables["PYTHONUTF8"] = "1";
            serverProcess = new Process(); serverProcess.StartInfo = info; serverProcess.EnableRaisingEvents = true;
            serverProcess.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) AppendServerLog(logFile, e.Data); };
            serverProcess.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) AppendServerLog(logFile, e.Data); };
            serverProcess.Exited += delegate { WriteLog("系統程序已停止。 / Server stopped."); BeginInvoke((MethodInvoker)UpdateStatus); };
            serverProcess.Start(); serverProcess.BeginOutputReadLine(); serverProcess.BeginErrorReadLine();
            File.WriteAllText(Path.Combine(environment.RuntimeDirectory, "server.pid"), serverProcess.Id.ToString(), Encoding.ASCII);
            WriteLog("系統已啟動：http://127.0.0.1:" + port);
            UpdateStatus();
            if (environment.Config.OpenBrowserAfterStart) { ThreadPool.QueueUserWorkItem(delegate { Thread.Sleep(900); BeginInvoke((MethodInvoker)OpenBrowser); }); }
        }

        private void StopServer(object sender, EventArgs args) { StopServer(); }
        private void StopServer()
        {
            Process process = GetRunningServerProcess();
            try
            {
                if (process != null && !process.HasExited)
                {
                    try { process.Kill(); process.WaitForExit(5000); }
                    catch { manager.StopConfiguredServer(); }
                    WriteLog("系統已停止。 / Server stopped.");
                }
                else if (AppIsHealthy() || ConfiguredPortIsOccupied())
                {
                    manager.StopConfiguredServer();
                    WriteLog("已停止由背景巡檢啟動的系統。 / Background server stopped.");
                }
                else WriteLog("系統目前未執行。 / Server is not running.");
            }
            catch (Exception ex) { WriteLog("停止失敗：" + ex.Message); }
            finally { serverProcess = null; string pid = Path.Combine(environment.RuntimeDirectory, "server.pid"); if (File.Exists(pid)) File.Delete(pid); if (IsHandleCreated) BeginInvoke((MethodInvoker)UpdateStatus); }
        }

        private Process GetRunningServerProcess()
        {
            if (serverProcess != null && !serverProcess.HasExited) return serverProcess;
            string pidFile = Path.Combine(environment.RuntimeDirectory, "server.pid");
            int pid;
            if (!File.Exists(pidFile) || !Int32.TryParse(File.ReadAllText(pidFile).Trim(), out pid)) return null;
            try
            {
                Process process = Process.GetProcessById(pid);
                if (process.HasExited) return null;
                string actual = process.MainModule.FileName;
                return String.Equals(Path.GetFullPath(actual), Path.GetFullPath(environment.PythonExe), StringComparison.OrdinalIgnoreCase) ? process : null;
            }
            catch { return null; }
        }

        private bool AppIsHealthy()
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + environment.Config.Port + "/auth/login");
                request.Timeout = 700; request.ReadWriteTimeout = 700;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                {
                    string content = reader.ReadToEnd();
                    return response.StatusCode == HttpStatusCode.OK && (content.Contains("宿舍工讀生") || content.Contains("Dormitory Student Worker System"));
                }
            }
            catch { return false; }
        }

        private bool ConfiguredPortIsOccupied()
        {
            int port;
            return Int32.TryParse(environment.Config.Port, out port) && !PortIsAvailable(port, environment.Config.ListenAddress == "0.0.0.0");
        }

        private void OpenBrowser(object sender, EventArgs args) { OpenBrowser(); }
        private void OpenBrowser()
        {
            try { Process.Start(new ProcessStartInfo("http://127.0.0.1:" + environment.Config.Port) { UseShellExecute = true }); }
            catch (Exception ex) { MessageBox.Show(ex.Message, "無法開啟瀏覽器"); }
        }

        private void OpenTextFile(string path)
        {
            if (!File.Exists(path)) { MessageBox.Show("找不到檔案：" + path, "檔案不存在"); return; }
            try { Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
            catch { Process.Start("notepad.exe", PortableManager.Quote(path)); }
        }

        private void AppendServerLog(string file, string message)
        {
            try { lock (this) File.AppendAllText(file, DateTime.Now.ToString("HH:mm:ss ") + message + Environment.NewLine, Encoding.UTF8); } catch { }
            WriteLog(message);
        }

        private void WriteLog(string message)
        {
            if (InvokeRequired) { BeginInvoke((MethodInvoker)delegate { WriteLog(message); }); return; }
            string line = "[" + DateTime.Now.ToString("HH:mm:ss") + "] " + message + Environment.NewLine;
            logBox.AppendText(line); logBox.SelectionStart = logBox.TextLength; logBox.ScrollToCaret();
            try { Directory.CreateDirectory(environment.LogsDirectory); File.AppendAllText(Path.Combine(environment.LogsDirectory, "launcher-" + DateTime.Now.ToString("yyyy-MM-dd") + ".log"), line, Encoding.UTF8); } catch { }
        }

        private void ImportWatchdogLog()
        {
            string path = Path.Combine(environment.LogsDirectory, "watchdog.log");
            try
            {
                if (!File.Exists(path)) return;
                using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    if (watchdogLogPosition > stream.Length) watchdogLogPosition = 0;
                    stream.Position = watchdogLogPosition;
                    using (StreamReader reader = new StreamReader(stream, Encoding.UTF8, true, 1024, true))
                    {
                        string text = reader.ReadToEnd();
                        watchdogLogPosition = stream.Position;
                        if (!String.IsNullOrWhiteSpace(text))
                            foreach (string line in text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries))
                                logBox.AppendText("[Watchdog] " + line + Environment.NewLine);
                    }
                }
                logBox.SelectionStart = logBox.TextLength; logBox.ScrollToCaret();
            }
            catch { }
        }

        private void SetActionsEnabled(bool enabled) { foreach (Button button in actionButtons) button.Enabled = enabled; }
        private void UpdateStatus()
        {
            bool healthy = AppIsHealthy();
            bool running = healthy || GetRunningServerProcess() != null;
            bool portOccupied = !running && ConfiguredPortIsOccupied();
            bool runtimeReady = environment.PythonIsInstalled && environment.GitIsInstalled;
            string serviceStatus = healthy ? "● 執行中 Running" : running ? "● 啟動中／無回應 Check server" : portOccupied ? "● Port 已占用／系統無回應" : (runtimeReady && environment.ProjectIsValid ? "● 已就緒 Ready" : runtimeReady ? "● 請選擇專案 Select project" : "○ 尚未安裝 Not installed");
            statusLabel.Text = serviceStatus + (environment.Config.AutoStartEnabled ? "\r\n自啟動：開 Auto-start: On" : "\r\n自啟動：關 Auto-start: Off");
            statusLabel.ForeColor = healthy ? Color.FromArgb(91, 224, 151) : running ? Color.FromArgb(255, 200, 87) : Color.White;
            if (lastRunningState.HasValue && lastRunningState.Value != running)
                WriteLog(running ? "偵測到系統已啟動。 / Server detected running." : "偵測到系統已停止。 / Server detected stopped.");
            lastRunningState = running;
        }

        private static bool PortIsAvailable(int port, bool allInterfaces)
        {
            TcpListener listener = null;
            try { listener = new TcpListener(allInterfaces ? IPAddress.Any : IPAddress.Loopback, port); listener.Start(); return true; }
            catch { return false; }
            finally { if (listener != null) listener.Stop(); }
        }

        private void OnFormClosing(object sender, FormClosingEventArgs args)
        {
            statusTimer.Stop();
            if (updateInProgress) return;
            if (GetRunningServerProcess() != null)
            {
                DialogResult result = MessageBox.Show("關閉啟動器也會停止系統，是否繼續？\r\nClosing the launcher will stop the server.", "確認關閉", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (result != DialogResult.Yes) { args.Cancel = true; statusTimer.Start(); return; }
                StopServer();
            }
        }
    }
}
