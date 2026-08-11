(() => {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%";

  const randomPassword = (length = 16) => {
    const values = new Uint32Array(length);
    crypto.getRandomValues(values);
    return Array.from(values, (value) => alphabet[value % alphabet.length]).join("");
  };

  document.querySelectorAll(".generate-password").forEach((button) => {
    button.addEventListener("click", () => {
      const form = button.closest("form");
      const password = form?.querySelector(".temporary-password");
      const confirmation = form?.querySelector(".temporary-password-confirm");
      if (!password || !confirmation) return;
      const generated = randomPassword();
      password.value = generated;
      confirmation.value = generated;
      password.type = "text";
      password.focus();
      password.select();
    });
  });

  document.querySelectorAll(".password-visibility").forEach((button) => {
    button.addEventListener("click", () => {
      const input = button.closest(".input-group")?.querySelector("input");
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.setAttribute("aria-label", show ? "隱藏臨時密碼" : "顯示臨時密碼");
      const icon = button.querySelector("i");
      if (icon) icon.className = show ? "bi bi-eye-slash" : "bi bi-eye";
    });
  });
})();
