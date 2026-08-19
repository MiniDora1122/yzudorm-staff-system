using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace DormAttendancePortable
{
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            if (args.Length > 0 && args[0] == "--diagnose")
            {
                string root = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
                string project = Path.GetFullPath(Path.Combine(root, ".."));
                string report = "TerminalRoot=" + root + Environment.NewLine
                    + "ProjectRoot=" + project + Environment.NewLine
                    + "MainLauncher=" + File.Exists(Path.Combine(project, "portable-windows-launcher", "DormStaffLauncher.exe")) + Environment.NewLine
                    + "Kiosk=" + File.Exists(Path.Combine(root, "DormAttendanceKiosk.exe")) + Environment.NewLine
                    + "TerminalScript=" + File.Exists(Path.Combine(root, "attendance_terminal.py")) + Environment.NewLine;
                string output = args.Length > 1 ? Path.GetFullPath(args[1]) : Path.Combine(root, "terminal-diagnostic.txt");
                File.WriteAllText(output, report, new UTF8Encoding(false));
                Environment.Exit(report.Contains("MainLauncher=True") && report.Contains("TerminalScript=True") && report.Contains("Kiosk=True") ? 0 : 2);
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new TerminalLauncherForm(args));
        }
    }

    internal sealed class TerminalLauncherForm : Form
    {
        private readonly string terminalRoot = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory);
        private readonly string projectRoot;
        private readonly string mainLauncher;
        private readonly string kiosk;
        private readonly string script;
        private readonly string controlFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DormAttendanceTerminal", "kiosk-control.token");
        private readonly Label status = new Label();
        private readonly RichTextBox log = new RichTextBox();
        private Process terminalProcess;
        private bool busy;

        public TerminalLauncherForm(string[] args)
        {
            projectRoot = Path.GetFullPath(Path.Combine(terminalRoot, ".."));
            mainLauncher = Path.Combine(projectRoot, "portable-windows-launcher", "DormStaffLauncher.exe");
            kiosk = Path.Combine(terminalRoot, "DormAttendanceKiosk.exe");
            script = Path.Combine(terminalRoot, "attendance_terminal.py");
            Text = "宿舍工讀生打卡終端 / Dorm Attendance Terminal";
            Width = 780; Height = 675; MinimumSize = new Size(700, 590);
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            Font = new Font("Microsoft JhengHei UI", 9F);
            BackColor = Color.FromArgb(244, 247, 251);
            BuildUi();
            FormClosing += OnClosing;
            foreach (string arg in args)
                if (arg == "--update-result=success") WriteLog("Git 安全更新完成。 / Safe update completed.");
            UpdateStatus();
        }

        private void BuildUi()
        {
            Panel header = new Panel { Dock = DockStyle.Top, Height = 94, BackColor = Color.FromArgb(14, 55, 104), Padding = new Padding(24, 12, 24, 10) };
            header.Controls.Add(new Label { Text = "宿舍工讀生打卡終端", ForeColor = Color.White, Font = new Font(Font.FontFamily, 18F, FontStyle.Bold), AutoSize = true, Location = new Point(22, 12) });
            header.Controls.Add(new Label { Text = "Card reader, account punch and offline synchronization", ForeColor = Color.FromArgb(190, 215, 242), AutoSize = true, Location = new Point(25, 50) });
            status.ForeColor = Color.White; status.Font = new Font(Font.FontFamily, 10F, FontStyle.Bold); status.TextAlign = ContentAlignment.MiddleRight; status.Dock = DockStyle.Right; status.Width = 230;
            header.Controls.Add(status); Controls.Add(header);

            TableLayoutPanel body = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(20), RowCount = 3, ColumnCount = 1 };
            body.RowStyles.Add(new RowStyle(SizeType.Absolute, 66)); body.RowStyles.Add(new RowStyle(SizeType.Absolute, 195)); body.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            Label help = new Label { Dock = DockStyle.Fill, Text = "首次使用：先安裝／修復環境，再啟動打卡畫面並匯入管理員提供的 .dormclock 註冊包。\r\nFirst use: install the runtime, start the kiosk, then import the encrypted registration package.", ForeColor = Color.FromArgb(70, 83, 99) };
            body.Controls.Add(help, 0, 0);
            TableLayoutPanel buttons = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 3 };
            for (int i = 0; i < 3; i++) buttons.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33.33F));
            AddButton(buttons, 0, 0, "① 安裝／修復\r\nInstall / Repair", Color.FromArgb(21, 101, 192), delegate { RunInstall(); });
            AddButton(buttons, 1, 0, "② 啟動打卡\r\nStart kiosk", Color.FromArgb(24, 135, 84), delegate { StartTerminal(); });
            AddButton(buttons, 2, 0, "停止打卡\r\nStop kiosk", Color.FromArgb(185, 46, 52), delegate { StopTerminal(); });
            AddButton(buttons, 0, 1, "開啟打卡網頁\r\nOpen kiosk page", Color.FromArgb(12, 105, 154), delegate { OpenKiosk(); });
            AddButton(buttons, 1, 1, "裝置資料夾\r\nDevice data", Color.FromArgb(60, 89, 140), delegate { OpenDeviceData(); });
            AddButton(buttons, 2, 1, "使用說明\r\nGuide", Color.FromArgb(210, 125, 35), delegate { OpenGuide(); });
            Button updateButton = AddButton(buttons, 0, 2, "Git 安全更新 / Safe update", Color.FromArgb(94, 67, 160), delegate { BeginUpdate(); });
            buttons.SetColumnSpan(updateButton, 3);
            body.Controls.Add(buttons, 0, 1);
            GroupBox logs = new GroupBox { Text = "執行紀錄 / Log", Dock = DockStyle.Fill, Padding = new Padding(10) };
            log.Dock = DockStyle.Fill; log.ReadOnly = true; log.BackColor = Color.FromArgb(24, 31, 42); log.ForeColor = Color.FromArgb(220, 230, 240); log.Font = new Font("Consolas", 9F); log.BorderStyle = BorderStyle.None;
            logs.Controls.Add(log); body.Controls.Add(logs, 0, 2); Controls.Add(body); body.BringToFront();
        }

        private Button AddButton(TableLayoutPanel grid, int column, int row, string text, Color color, EventHandler action)
        {
            Button button = new Button { Text = text, Dock = DockStyle.Fill, Margin = new Padding(5), BackColor = color, ForeColor = Color.White, FlatStyle = FlatStyle.Flat, Font = new Font(Font.FontFamily, 9.5F, FontStyle.Bold) };
            button.FlatAppearance.BorderSize = 0; button.Click += action; grid.Controls.Add(button, column, row);
            return button;
        }

        private void RunInstall()
        {
            if (busy) return;
            if (!File.Exists(mainLauncher)) { MessageBox.Show("找不到 DormStaffLauncher.exe，請保留完整專案資料夾。", "缺少檔案", MessageBoxButtons.OK, MessageBoxIcon.Error); return; }
            busy = true; UpdateStatus(); WriteLog("開始安裝打卡終端環境…");
            ThreadPool.QueueUserWorkItem(delegate
            {
                int code = -1;
                try
                {
                    using (Process process = Process.Start(new ProcessStartInfo(mainLauncher, "--install-terminal-headless") { WorkingDirectory = Path.GetDirectoryName(mainLauncher), UseShellExecute = false, CreateNoWindow = true })) { process.WaitForExit(); code = process.ExitCode; }
                }
                catch (Exception ex) { BeginInvoke((MethodInvoker)delegate { WriteLog(ex.Message); }); }
                BeginInvoke((MethodInvoker)delegate { busy = false; WriteLog(code == 0 ? "安裝完成。 / Runtime ready." : "安裝失敗，請查看 portable-windows-launcher\\terminal-install.log。"); UpdateStatus(); });
            });
        }

        private void StartTerminal()
        {
            if (IsKioskRunning()) { WriteLog("打卡服務已在執行，已重新開啟網頁。 / Kiosk already running."); OpenKiosk(); return; }
            if (!File.Exists(kiosk) || !File.Exists(script)) { MessageBox.Show("打卡畫面執行檔不完整，請重新取得完整版本。", "缺少檔案", MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
            try
            {
                string token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
                Directory.CreateDirectory(Path.GetDirectoryName(controlFile));
                File.WriteAllText(controlFile, token, new UTF8Encoding(false));
                terminalProcess = Process.Start(new ProcessStartInfo(kiosk, "--control-token=" + token) { WorkingDirectory = terminalRoot, UseShellExecute = false });
                terminalProcess.EnableRaisingEvents = true; terminalProcess.Exited += delegate { if (!IsDisposed) BeginInvoke((MethodInvoker)delegate { terminalProcess = null; UpdateStatus(); }); };
                WriteLog("打卡畫面已啟動。 / Kiosk started."); UpdateStatus();
                ThreadPool.QueueUserWorkItem(delegate { for (int i = 0; i < 20 && !IsKioskRunning(); i++) Thread.Sleep(150); BeginInvoke((MethodInvoker)delegate { OpenKiosk(); UpdateStatus(); }); });
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "無法啟動", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        }

        private void StopTerminal()
        {
            if (!IsKioskRunning() && (terminalProcess == null || terminalProcess.HasExited)) { WriteLog("打卡服務目前未執行。 / Kiosk is not running."); UpdateStatus(); return; }
            try
            {
                string token = File.Exists(controlFile) ? File.ReadAllText(controlFile, Encoding.UTF8).Trim() : "";
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:47831/__shutdown");
                request.Method = "POST"; request.Timeout = 1500; request.Headers["X-Control-Token"] = token; request.ContentLength = 0;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse()) { }
            }
            catch { }
            for (int i = 0; i < 15 && IsKioskRunning(); i++) { Thread.Sleep(100); Application.DoEvents(); }
            try
            {
                if (terminalProcess != null && !terminalProcess.HasExited)
                {
                    using (Process killer = Process.Start(new ProcessStartInfo("taskkill.exe", "/PID " + terminalProcess.Id + " /T /F") { UseShellExecute = false, CreateNoWindow = true })) { killer.WaitForExit(3000); }
                }
            }
            catch { }
            if (!IsKioskRunning()) { try { File.Delete(controlFile); } catch { } WriteLog("打卡服務已停止。 / Kiosk stopped."); }
            else { WriteLog("無法停止打卡服務，請重新啟動電腦或洽管理員。 / Kiosk stop failed."); }
            UpdateStatus();
        }

        private void OpenKiosk()
        {
            if (!IsKioskRunning()) { MessageBox.Show("打卡服務尚未啟動，請先按「啟動打卡」。", "尚未啟動", MessageBoxButtons.OK, MessageBoxIcon.Information); UpdateStatus(); return; }
            try { Process.Start(new ProcessStartInfo("http://127.0.0.1:47831/") { UseShellExecute = true }); WriteLog("已開啟打卡網頁。 / Kiosk page opened."); }
            catch (Exception ex) { MessageBox.Show(ex.Message, "無法開啟網頁", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        }

        private bool IsKioskRunning()
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:47831/health");
                request.Method = "GET"; request.Timeout = 250; request.Proxy = null;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse()) return response.StatusCode == HttpStatusCode.OK;
            }
            catch { return false; }
        }

        private void BeginUpdate()
        {
            if (busy) return;
            if (IsKioskRunning()) { MessageBox.Show("請先停止打卡畫面再更新。", "打卡仍在執行", MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
            string updater = Path.Combine(terminalRoot, "terminal-self-update.ps1");
            if (!File.Exists(updater)) { MessageBox.Show("找不到更新程式。", "缺少檔案"); return; }
            if (MessageBox.Show("更新期間約數分鐘不能打卡；離線佇列不會被刪除。是否繼續？", "Git 安全更新", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
            string copied = Path.Combine(Path.GetTempPath(), "DormAttendanceUpdate-" + Guid.NewGuid().ToString("N") + ".ps1");
            File.Copy(updater, copied, true);
            string args = "-NoProfile -ExecutionPolicy Bypass -File " + Quote(copied) + " -ProjectRoot " + Quote(projectRoot) + " -ParentProcessId " + Process.GetCurrentProcess().Id;
            Process.Start(new ProcessStartInfo("powershell.exe", args) { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = terminalRoot });
            Application.Exit();
        }

        private void OpenDeviceData()
        {
            string directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DormAttendanceTerminal");
            Directory.CreateDirectory(directory); Process.Start(new ProcessStartInfo(directory) { UseShellExecute = true });
        }

        private void OpenGuide() { string path = Path.Combine(terminalRoot, "README.md"); if (File.Exists(path)) Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
        private void UpdateStatus() { bool running = IsKioskRunning(); status.Text = busy ? "處理中 / Working" : running ? "打卡執行中 / Running" : File.Exists(kiosk) ? "已就緒 / Ready" : "檔案不完整 / Incomplete"; }
        private void WriteLog(string message) { log.AppendText("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + message + Environment.NewLine); log.SelectionStart = log.TextLength; log.ScrollToCaret(); }
        private void OnClosing(object sender, FormClosingEventArgs args) { if (IsKioskRunning() && MessageBox.Show("關閉管理程式也會關閉打卡畫面，是否繼續？", "確認關閉", MessageBoxButtons.YesNo) != DialogResult.Yes) { args.Cancel = true; return; } StopTerminal(); }
        private static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }
    }
}
