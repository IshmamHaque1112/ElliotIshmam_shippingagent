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

// Print button for the claim packet.
document.querySelectorAll("[data-print]").forEach(function (button) {
  button.addEventListener("click", function () { window.print(); });
});

// Event replay play button: asks the server to step the replay clock once a second and redraws the numbers.
(function () {
  var panel = document.getElementById("replay");
  var play = document.getElementById("replay-play");
  if (!panel || !play) return;
  var pause = document.getElementById("replay-pause"), speed = document.getElementById("replay-speed"), status = document.getElementById("replay-status");
  var timer = null;
  function money(v) { return "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function draw(s) {
    document.getElementById("replay-mode").textContent = s.active ? "active" : "off";
    document.getElementById("replay-now").textContent = s.now_utc;
    document.getElementById("replay-visible").textContent = s.shipments_visible;
    document.getElementById("replay-exceptions").textContent = s.active_exceptions;
    document.getElementById("replay-exposed").textContent = money(s.revenue_exposed_usd);
    var list = document.getElementById("replay-top");
    while (list.firstChild) list.removeChild(list.firstChild);
    s.top.forEach(function (r) {
      var li = document.createElement("li");
      li.textContent = r.shipment_id + " · " + r.state.replace(/_/g, " ") + " · " + money(r.exposure_usd);
      list.appendChild(li);
    });
  }
  function stop(message) {
    if (timer) clearInterval(timer);
    timer = null; play.disabled = false; pause.disabled = true; status.textContent = message || "";
  }
  function tick() {
    fetch("/api/replay/step", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": panel.getAttribute("data-csrf") },
      body: JSON.stringify({ hours: Number(speed.value) }), credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (s) { if (s.error) { stop(s.error); return; } draw(s); if (s.at_end) stop("Reached the end of the data."); })
      .catch(function () { stop("Could not reach the server."); });
  }
  play.addEventListener("click", function () { play.disabled = true; pause.disabled = false; status.textContent = "Playing…"; tick(); timer = setInterval(tick, 1000); });
  pause.addEventListener("click", function () { stop("Paused."); });
})();
