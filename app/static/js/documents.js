(() => {
  const type = document.getElementById("documentType");
  const residence = document.getElementById("residenceUploadFields");
  const work = document.getElementById("workPermitUploadFields");
  if (!type || !residence || !work) return;

  const residenceInputs = residence.querySelectorAll("input[type=file]");
  const workPage1 = document.getElementById("workPermitPage1");
  const sync = () => {
    const isResidence = type.value === "RESIDENCE_PERMIT";
    residence.classList.toggle("d-none", !isResidence);
    work.classList.toggle("d-none", isResidence);
    residenceInputs.forEach((input) => { input.required = isResidence; });
    if (workPage1) workPage1.required = !isResidence;
  };
  type.addEventListener("change", sync);
  sync();
})();
