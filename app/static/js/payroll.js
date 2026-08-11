document.addEventListener("DOMContentLoaded", () => {
  const app = document.getElementById("payrollApp");
  if (!app) return;
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
  const monthInput = document.getElementById("payrollMonth");
  const staffModal = new bootstrap.Modal(document.getElementById("staffPayrollModal"));
  const settingsModal = new bootstrap.Modal(document.getElementById("payrollSettingsModal"));
  let reportRows = [];

  const today = new Date();
  monthInput.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
  const currency = (value) => `$${Number(value || 0).toLocaleString("zh-TW")}`;
  const showError = (element, message = "") => {
    element.textContent = message;
    element.classList.toggle("d-none", !message);
  };
  const showAlert = (message, category = "success") => {
    const alert = document.getElementById("payrollAlert");
    alert.className = `alert alert-${category}`;
    alert.textContent = message;
  };
  const readError = async (response) => {
    try { return (await response.json()).error?.message || "操作失敗。"; }
    catch (_error) { return "操作失敗，請重新整理後再試。"; }
  };

  const appendMoneyCell = (row, value, detail = "") => {
    const cell = row.insertCell();
    const amount = document.createElement("strong");
    amount.textContent = currency(value);
    cell.append(amount);
    if (detail) {
      const small = document.createElement("div");
      small.className = "small text-secondary";
      small.textContent = detail;
      cell.append(small);
    }
  };

  const renderReport = (payload) => {
    reportRows = payload.rows;
    document.getElementById("effectiveDateLabel").textContent = payload.effective_date;
    document.getElementById("payrollHours").textContent = Number(payload.totals.hours).toLocaleString("zh-TW");
    document.getElementById("grossWageTotal").textContent = currency(payload.totals.gross_wage);
    document.getElementById("benefitsTotal").textContent = currency(payload.totals.employer_benefits);
    document.getElementById("supplementaryHealth").textContent = currency(payload.totals.supplementary_health);
    document.getElementById("employerTotal").textContent = currency(payload.totals.employer_total);
    const body = document.getElementById("payrollTableBody");
    body.replaceChildren();

    payload.rows.forEach((item) => {
      const row = body.insertRow();
      const nameCell = row.insertCell();
      nameCell.className = "ps-4";
      const name = document.createElement("strong");
      name.textContent = item.name;
      const number = document.createElement("div");
      number.className = "small text-secondary";
      number.textContent = item.student_number;
      nameCell.append(name, number);
      const hoursCell = row.insertCell();
      hoursCell.textContent = `${item.hours} × ${currency(item.hourly_wage)}`;
      appendMoneyCell(row, item.gross_wage);
      appendMoneyCell(row, item.labor_insurance + item.employment_insurance + item.occupational_accident, `勞 ${currency(item.labor_insurance)}｜就 ${currency(item.employment_insurance)}｜災 ${currency(item.occupational_accident)}`);
      appendMoneyCell(row, item.health_insurance);
      appendMoneyCell(row, item.labor_pension);
      appendMoneyCell(row, item.employer_total);
      const actionCell = row.insertCell();
      const button = document.createElement("button");
      button.className = `btn btn-sm ${item.insurance_configured ? "btn-outline-primary" : "btn-warning"}`;
      button.type = "button";
      button.dataset.staffId = item.staff_id;
      button.innerHTML = '<i class="bi bi-pencil-square"></i><span class="visually-hidden">編輯投保設定</span>';
      button.addEventListener("click", () => openStaff(item.staff_id));
      actionCell.append(button);
    });
  };

  const loadReport = async () => {
    try {
      const response = await fetch(`${app.dataset.reportUrl}?month=${encodeURIComponent(monthInput.value)}`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(await readError(response));
      renderReport(await response.json());
    } catch (error) { showAlert(error.message, "danger"); }
  };

  const openStaff = (staffId) => {
    const item = reportRows.find((row) => row.staff_id === Number(staffId));
    if (!item) return;
    document.getElementById("staffPayrollTitle").textContent = `${item.name}｜薪資與投保設定`;
    document.getElementById("payrollStaffId").value = item.staff_id;
    document.getElementById("staffHourlyWage").value = item.hourly_wage_override ?? "";
    document.getElementById("laborSalary").value = item.labor_insured_salary || "";
    document.getElementById("healthSalary").value = item.health_insured_salary || "";
    document.getElementById("pensionSalary").value = item.pension_salary || "";
    document.getElementById("laborEnabled").checked = item.flags.labor;
    document.getElementById("employmentEnabled").checked = item.flags.employment;
    document.getElementById("healthEnabled").checked = item.flags.health;
    document.getElementById("pensionEnabled").checked = item.flags.pension;
    showError(document.getElementById("staffPayrollError"));
    staffModal.show();
  };

  document.getElementById("loadPayrollButton").addEventListener("click", loadReport);
  monthInput.addEventListener("change", loadReport);
  document.getElementById("payrollSettingsButton").addEventListener("click", () => settingsModal.show());

  document.getElementById("staffPayrollForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const staffId = document.getElementById("payrollStaffId").value;
    const payload = {
      hourly_wage: document.getElementById("staffHourlyWage").value,
      labor_insured_salary: document.getElementById("laborSalary").value,
      health_insured_salary: document.getElementById("healthSalary").value,
      pension_salary: document.getElementById("pensionSalary").value,
      labor_insurance_enabled: document.getElementById("laborEnabled").checked,
      employment_insurance_enabled: document.getElementById("employmentEnabled").checked,
      health_insurance_enabled: document.getElementById("healthEnabled").checked,
      labor_pension_enabled: document.getElementById("pensionEnabled").checked,
    };
    const errorBox = document.getElementById("staffPayrollError");
    try {
      const url = app.dataset.staffUrlTemplate.replace("999999", staffId);
      const response = await fetch(url, {
        method: "PUT", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await readError(response));
      staffModal.hide();
      showAlert("人員薪資與投保設定已更新。");
      loadReport();
    } catch (error) { showError(errorBox, error.message); }
  });

  document.getElementById("payrollSettingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      effective_date: document.getElementById("effectiveDate").value,
      default_hourly_wage: document.getElementById("defaultHourlyWage").value,
      labor_insurance_rate: document.getElementById("laborRate").value,
      employment_insurance_rate: document.getElementById("employmentRate").value,
      employer_labor_share: document.getElementById("employerLaborShare").value,
      occupational_accident_rate: document.getElementById("occupationalRate").value,
      health_insurance_rate: document.getElementById("healthRate").value,
      employer_health_share: document.getElementById("employerHealthShare").value,
      average_dependents: document.getElementById("averageDependents").value,
      supplementary_health_rate: document.getElementById("supplementaryRate").value,
      employer_pension_rate: document.getElementById("pensionRate").value,
    };
    const errorBox = document.getElementById("payrollSettingsError");
    try {
      const response = await fetch(app.dataset.settingsUrl, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await readError(response));
      settingsModal.hide();
      showAlert("費率設定已儲存。");
      loadReport();
    } catch (error) { showError(errorBox, error.message); }
  });

  loadReport();
});
