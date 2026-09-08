(function () {
  // ---- keep scroll position across form posts ----
  var KEY = "wtn:scroll:" + window.location.pathname;

  document.addEventListener("submit", function () {
    try { sessionStorage.setItem(KEY, String(window.scrollY)); } catch (e) {}
  }, true);

  window.addEventListener("load", function () {
    var y = null;
    try { y = sessionStorage.getItem(KEY); } catch (e) {}
    if (y !== null) {
      window.scrollTo(0, parseInt(y, 10) || 0);
      try { sessionStorage.removeItem(KEY); } catch (e) {}
    }
  });

  // ---- sign in stays disabled until the field has something in it ----
  // Disabled from here rather than in the markup on purpose: if this script
  // fails to load, the button must still work or nobody can sign in.
  var gate = document.querySelector(".gate");
  if (gate) {
    var field = gate.querySelector("input[name=email]");
    var submit = gate.querySelector("button[type=submit]");
    if (field && submit) {
      var sync = function () { submit.disabled = field.value.trim() === ""; };
      sync();
      field.addEventListener("input", sync);
      field.addEventListener("change", sync);
      // Autofill can populate the field without firing either event.
      window.setTimeout(sync, 200);
    }
  }

  // ---- toast ----
  var params = new URLSearchParams(window.location.search);
  var msg = params.get("msg");
  var err = params.get("error");
  if (!msg && !err) { return; }

  // The landing page uses ?error=1 as a flag and renders its own message
  // in the form. Without this it also toasts a bare "1".
  if (window.location.pathname === "/" && !msg) { return; }

  var bar = document.createElement("div");
  bar.className = "toast" + (err ? " bad" : "");
  bar.setAttribute("role", "status");
  bar.textContent = err || msg;

  var close = document.createElement("button");
  close.type = "button";
  close.className = "toast-x";
  close.textContent = "\u00d7";
  close.addEventListener("click", function () { bar.remove(); });
  bar.appendChild(close);

  document.body.appendChild(bar);
  requestAnimationFrame(function () { bar.classList.add("in"); });

  if (!err) {
    setTimeout(function () {
      bar.classList.remove("in");
      setTimeout(function () { bar.remove(); }, 300);
    }, 4500);
  }

  // drop the params so a refresh does not repeat the message
  params.delete("msg");
  params.delete("error");
  var rest = params.toString();
  history.replaceState({}, "",
    window.location.pathname + (rest ? "?" + rest : ""));
})();
