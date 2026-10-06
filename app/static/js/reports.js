// Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
(() => {
  const month = document.getElementById("reportMonth");
  if (!month) return;
  const syncLinks = () => {
    document.querySelectorAll(".report-month-link").forEach((link) => {
      const baseUrl = link.dataset.baseUrl;
      link.href = `${baseUrl}?month=${encodeURIComponent(month.value)}`;
    });
  };
  month.addEventListener("change", syncLinks);
  syncLinks();
})();
