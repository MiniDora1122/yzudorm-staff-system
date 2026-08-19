(() => {
  const translations = new Map(Object.entries({
    "登入成功。": "Signed in successfully.",
    "您已安全登出。": "You have signed out safely.",
    "帳號資料": "Account information",
    "基本資料": "Basic information",
    "姓名": "Name",
    "學號": "Student ID",
    "帳號": "Username",
    "登入帳號": "Username",
    "密碼": "Password",
    "臨時密碼": "Temporary password",
    "新臨時密碼": "New temporary password",
    "再次輸入臨時密碼": "Confirm temporary password",
    "顯示名稱": "Display name",
    "聯絡方式": "Contact",
    "聯絡電話": "Phone",
    "國籍": "Nationality",
    "日期": "Date",
    "月份": "Month",
    "地點": "Location",
    "班別": "Shift type",
    "時間": "Time",
    "時數": "Hours",
    "狀態": "Status",
    "操作": "Actions",
    "文件": "Documents",
    "類型": "Type",
    "版本": "Version",
    "頁面": "Pages",
    "上傳時間": "Uploaded",
    "建立時間": "Created",
    "最後登入": "Last login",
    "帳號狀態": "Account status",
    "帳號操作": "Account actions",
    "工讀生": "Student worker",
    "工讀生／學號": "Student / ID",
    "管理員": "Administrator",
    "全部": "All",
    "指定月份": "Selected month",
    "套用": "Apply",
    "取消": "Cancel",
    "關閉": "Close",
    "儲存": "Save",
    "儲存資料": "Save information",
    "儲存基本資料": "Save profile",
    "儲存設定": "Save settings",
    "儲存費率設定": "Save rate settings",
    "儲存人員設定": "Save staff settings",
    "刪除": "Delete",
    "編輯資料": "Edit profile",
    "重設密碼": "Reset password",
    "確認重設密碼": "Confirm reset",
    "產生安全臨時密碼": "Generate secure password",
    "建立工讀生帳號": "Create student account",
    "建立帳號": "Create account",
    "新增工讀生": "Add student",
    "新增管理員": "Add administrator",
    "新增排班": "Add shift",
    "編輯排班": "Edit shift",
    "儲存排班": "Save shift",
    "新增地點": "Add location",
    "新增班別": "Add shift type",
    "地點與班別": "Locations / Shifts",
    "地點與班別設定": "Location and shift type settings",
    "新增工作地點": "Add work location",
    "新增工作班別": "Add shift type",
    "顯示地點": "Locations shown",
    "全部班別": "All shift types",
    "工讀時數": "Working hours",
    "小時": "Hours",
    "我的資料": "My profile",
    "我的近期排班": "My upcoming shifts",
    "班表詳細資料": "Shift details",
    "申請狀態": "Request status",
    "知道了": "Close",
    "提出請假": "Request leave",
    "提出換班": "Request a swap",
    "排班": "Shift",
    "原因": "Reason",
    "備註": "Note",
    "說明": "Description",
    "我的班": "My shift",
    "換班對象": "Swap target",
    "對方班表": "Target shift",
    "送出請假申請": "Submit leave request",
    "送出換班邀請": "Send swap invitation",
    "我的請假紀錄": "My leave history",
    "換班進度": "Swap progress",
    "角色": "Role",
    "接受": "Accept",
    "拒絕": "Reject",
    "核准": "Approve",
    "審核": "Review",
    "申請時間": "Submitted",
    "原因與備註": "Reason / Note",
    "申請人／排班": "Applicant / Shift",
    "申請人與原班": "Applicant / Original shift",
    "對象與交換班表": "Target / Swap shift",
    "排班月份篩選": "Schedule month filter",
    "紀錄月份篩選": "History month filter",
    "目前證件資料": "Current document information",
    "居留證": "Residence permit",
    "工作證": "Work permit",
    "證號": "ID number",
    "開始日": "Start date",
    "截止日": "Expiry date",
    "有效期限": "Valid until",
    "證件類型": "Document type",
    "居留證正面": "Residence permit front",
    "居留證反面": "Residence permit back",
    "工作證第 1 頁": "Work permit page 1",
    "工作證第 2 頁（選填）": "Work permit page 2 (optional)",
    "上傳整份文件": "Upload full document",
    "下載整份": "Download full document",
    "下載此頁": "Download page",
    "預覽": "Preview",
    "確認整份文件並更新資料": "Confirm document and update profile",
    "刪除整份待確認文件": "Delete pending document",
    "保存期限與排程清理": "Retention and scheduled cleanup",
    "保存天數": "Retention days",
    "每日清理時間": "Daily cleanup time",
    "立即執行到期清理": "Run cleanup now",
    "加密金鑰備份": "Encryption key backup",
    "主金鑰": "Primary key",
    "自動備份": "Automatic backup",
    "最近清理稽核": "Recent cleanup audit",
    "時間": "Time",
    "執行依據": "Basis",
    "紀錄人": "Actor",
    "報表月份": "Report month",
    "工讀生約用時數月報": "Monthly working-hours report",
    "排班明細": "Shift details",
    "薪資與雇主成本": "Payroll and employer cost",
    "請假／換班紀錄": "Leave / swap history",
    "證件效期與完整性": "Document expiry and completeness",
    "下載 XLSX": "Download XLSX",
    "下載 CSV": "Download CSV",
    "費率設定": "Rate settings",
    "計算月份": "Calculation month",
    "重新計算": "Recalculate",
    "總時數": "Total hours",
    "工資合計": "Total wages",
    "雇主保險／勞退": "Employer insurance / Pension",
    "雇主總成本": "Total employer cost",
    "工資": "Wage",
    "時數 × 時薪": "Hours × Rate",
    "健保": "Health insurance",
    "勞退": "Pension",
    "人員薪資與投保設定": "Staff payroll and insurance settings",
    "個別時薪": "Individual hourly wage",
    "本單位加保勞保／災保": "Labor / accident insurance by this unit",
    "勞（災）保月投保薪資": "Monthly labor insurance salary",
    "適用就業保險": "Employment insurance applies",
    "本單位投保健保": "Health insurance by this unit",
    "健保月投保金額": "Monthly health insurance amount",
    "本單位提繳勞退": "Pension contribution by this unit",
    "勞退月提繳工資": "Monthly pension salary",
    "薪資與法定費率設定": "Payroll and statutory rates",
    "生效日期": "Effective date",
    "預設時薪（元）": "Default hourly wage (NTD)",
    "勞保普通事故費率（%）": "Labor insurance rate (%)",
    "就保費率（%）": "Employment insurance rate (%)",
    "雇主分攤（%）": "Employer share (%)",
    "職災費率（%）": "Occupational accident rate (%)",
    "健保費率（%）": "Health insurance rate (%)",
    "雇主健保分攤（%）": "Employer health share (%)",
    "平均眷口數": "Average dependents",
    "補充保費率（%）": "Supplementary premium rate (%)",
    "雇主勞退提繳率（%）": "Employer pension rate (%)"
    ,"目前密碼": "Current password"
    ,"新密碼": "New password"
    ,"確認新密碼": "Confirm new password"
    ,"更新密碼": "Update password"
    ,"工作地點": "Work location"
    ,"班別名稱": "Shift name"
    ,"代碼": "Code"
    ,"開始時間": "Start time"
    ,"結束時間": "End time"
    ,"編輯": "Edit"
    ,"取消編輯": "Cancel edit"
    ,"儲存地點": "Save location"
    ,"儲存班別": "Save shift type"
    ,"批量匯入": "Bulk import"
    ,"批量匯入排班": "Bulk import shifts"
    ,"CSV 檔案": "CSV file"
    ,"下載 CSV 範本": "Download CSV template"
    ,"開始匯入": "Import shifts"
    ,"今天排班": "Today shifts"
    ,"明天排班": "Tomorrow shifts"
    ,"目前沒有排班。": "No shifts scheduled."
    ,"尚無請假紀錄。": "No leave history."
    ,"尚無換班紀錄。": "No swap history."
    ,"我提出的": "Requested by me"
    ,"等我回覆": "Awaiting my response"
    ,"直接承接原班": "Cover the original shift"
    ,"有效": "Valid"
    ,"60 天內到期": "Expires within 60 days"
    ,"30 天內到期": "Expires within 30 days"
    ,"已到期": "Expired"
    ,"如需更正請聯絡管理員。": "Contact an administrator to correct this information."
    ,"我已閱讀並同意上述蒐集、使用與保存說明。": "I have read and agree to the collection, use and retention notice above."
    ,"每頁接受 JPG、PNG、WEBP，最多 8MB；上傳後會移除 EXIF 並重新編碼。居留證兩面必須同時上傳，工作證第 1 頁必填。": "Each page accepts JPG, PNG or WEBP up to 8MB. EXIF is removed and images are re-encoded. Both residence permit sides and work permit page 1 are required."
    ,"照片僅供宿舍工讀生資格、居留與工作許可查核，由本人及具管理權限人員透過登入後頁面存取。影像會加密保存在非公開目錄並依校方保存政策保留；證件日期由本人逐頁人工核對。": "Images are used only to verify student employment, residence and work authorization. Access requires sign-in, files are encrypted outside the public directory, and dates must be checked manually."
    ,"目前顯示：全部月份": "Showing: all months"
    ,"尚無請假申請。": "No leave requests."
    ,"尚無換班申請。": "No swap requests."
    ,"由對方直接承接申請人的班": "The target student covers the requester's shift."
    ,"已完成處理": "Completed"
    ,"尚未填寫": "Not provided"
    ,"居留證 · 1 頁": "Residence permit · 1 page"
    ,"工作證 · 1 頁": "Work permit · 1 page"
    ,"3–80 位英數字、句點、底線或連字號；系統會轉為小寫。": "Use 3–80 letters, numbers, periods, underscores or hyphens; the system converts it to lowercase."
    ,"密碼只會在建立時由管理員交付，不會在系統內再次顯示。": "The password is shown only when the account is created and cannot be viewed again."
    ,"請使用安全管道交付臨時密碼，不要透過公開群組或名冊傳送。": "Deliver temporary passwords through a secure channel, never through public groups or rosters."
    ,"會出現在月報與所有匯出檔。": "This appears in monthly reports and all exports."
    ,"儲存後原密碼立即失效，學生下次操作時必須先修改臨時密碼。": "After saving, the old password becomes invalid and the student must change the temporary password."
    ,"Excel 月曆格式，依附件版型呈現每人每日時數、每週小計與月總計，並包含學號。": "Excel calendar format with daily hours, weekly subtotals, monthly totals and student IDs."
    ,"逐筆列出學號、姓名、日期、地點、班別、起訖時間與時數，適合核對或匯入其他系統。": "Lists each student ID, name, date, location, shift, start/end time and hours for verification or import."
    ,"含應發工資、勞保／就保／災保、健保、勞退與雇主總成本；僅管理員可下載。": "Includes gross wages, labor/employment/accident insurance, health insurance, pension and total employer cost; administrators only."
    ,"彙整當月申請人、原排班、對象、原因、備註與最終狀態，供行政追蹤。": "Summarizes applicants, original shifts, targets, reasons, notes and final status for administrative tracking."
    ,"列出到期狀態、居留證正反面及工作證頁數是否完整；不輸出完整居留證號。": "Lists expiry status and document completeness without exporting full residence permit numbers."
  }));

  const selectors = [
    "h1", "h2", "h3", "p", "label", "legend", "button", "a", "th", "dt", "dd",
    "strong", "span", "small", ".eyebrow", ".form-text", ".alert", ".text-secondary", ".list-group-item"
  ].join(",");

  const addTranslation = (element) => {
    if (element.dataset.en || element.dataset.bilingualProcessed || element.classList.contains("nav-primary-label")) return;
    const textNodes = Array.from(element.childNodes).filter((node) => node.nodeType === Node.TEXT_NODE);
    const sourceNode = textNodes.find((node) => translations.has(node.textContent.trim()));
    if (!sourceNode) return;
    const english = translations.get(sourceNode.textContent.trim());
    const span = document.createElement("span");
    span.className = "bilingual-english";
    span.lang = "en";
    span.textContent = english;
    if (element.matches("h1,h2,h3,p,.form-label,.form-text,th,dt,legend")) {
      span.classList.add("bilingual-english-block");
    }
    element.append(span);
    element.dataset.bilingualProcessed = "true";
  };

  const enhance = (root = document) => {
    if (root.nodeType === Node.ELEMENT_NODE && root.matches(selectors)) addTranslation(root);
    root.querySelectorAll?.(selectors).forEach(addTranslation);
  };

  enhance();
  new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) enhance(node);
    }));
  }).observe(document.body, { childList: true, subtree: true });
})();
