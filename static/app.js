// Copy buttons for the read-only draft boxes. Nothing is sent anywhere.
document.querySelectorAll("[data-copy]").forEach(function (button) {
  button.addEventListener("click", function () {
    var box = document.getElementById(button.getAttribute("data-copy"));
    if (!box) return;
    box.select();
    try { navigator.clipboard.writeText(box.value); button.textContent = "Copied"; } catch (e) { document.execCommand("copy"); }
  });
});
// Confirm before anything that spends API credits.
document.querySelectorAll("form[data-confirm]").forEach(function (form) {
  form.addEventListener("submit", function (event) {
    if (!window.confirm(form.getAttribute("data-confirm"))) event.preventDefault();
  });
});
