document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-confirm-action]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (!window.confirm(button.dataset.confirmAction)) event.preventDefault();
    });
  });

  const targetStaff = document.getElementById("targetStaff");
  const targetShift = document.getElementById("targetShift");
  if (targetStaff && targetShift) {
    const filterTargetShifts = () => {
      const selectedStaff = targetStaff.value;
      [...targetShift.options].forEach((option, index) => {
        if (index === 0) return;
        option.hidden = !selectedStaff || option.dataset.staffId !== selectedStaff;
      });
      if (targetShift.selectedOptions[0]?.hidden) targetShift.value = "";
    };
    targetStaff.addEventListener("change", filterTargetShifts);
    filterTargetShifts();
  }

  const requestTypeButtons = document.querySelectorAll(".request-type-button");
  const requestPanels = document.querySelectorAll(".request-form-panel");
  requestTypeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const targetId = button.dataset.requestTarget;
      requestPanels.forEach((panel) => panel.classList.toggle("d-none", panel.id !== targetId));
      requestTypeButtons.forEach((item) => {
        const selected = item === button;
        item.setAttribute("aria-expanded", selected ? "true" : "false");
        item.classList.toggle("active", selected);
      });
      const target = document.getElementById(targetId);
      target?.querySelector("select, input, textarea")?.focus({ preventScroll: true });
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  const requestedTab = window.location.hash;
  if (requestedTab === "#leaveReview" || requestedTab === "#swapReview") {
    document.querySelector(`[data-bs-target="${requestedTab}"]`)?.click();
  }
});
