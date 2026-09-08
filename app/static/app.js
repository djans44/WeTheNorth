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

  // ---- narrow screens: collapse the site nav behind a hamburger ----
  // The button is added here rather than in the template so that without this
  // script the nav simply stays open, as it did before.
  var siteNav = document.querySelector(".sitenav");
  if (siteNav && siteNav.querySelector("ul")) {
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "navtoggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Menu");
    toggle.innerHTML =
      '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">' +
      '<path d="M3 5.5h14M3 10h14M3 14.5h14" fill="none" ' +
      'stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

    // After the wordmark, before the links: margin-left:auto then pushes it
    // to the right-hand end of the bar.
    siteNav.classList.add("collapsible");
    siteNav.insertBefore(toggle, siteNav.querySelector("ul"));

    // The section strip sticks directly below the site nav, so it needs the
    // nav's real height -- which changes when the menu opens. Published as a
    // custom property rather than hard-coded in the stylesheet.
    var publishNavHeight = function () {
      document.documentElement.style.setProperty(
        "--nav-h", siteNav.getBoundingClientRect().height + "px");
    };
    publishNavHeight();
    window.addEventListener("resize", publishNavHeight);

    toggle.addEventListener("click", function () {
      var open = siteNav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      publishNavHeight();
    });
  }

  // ---- season page: one week at a time ----
  // The server renders every week and marks one open. Without this script
  // they all stay visible and the nav links simply jump to them, so the
  // page still works.
  //
  // Two controls choose the week -- a strip of seventeen on wide screens, a
  // select on narrow ones -- and CSS shows one at a time. Both are updated on
  // every change rather than only the visible one, so resizing or rotating
  // never reveals a control pointing at a different week.
  var weekNav = document.querySelector(".week-nav");
  var weekPick = document.getElementById("weekpick");
  if (weekNav || weekPick) {
    var weeks = document.querySelectorAll(".week");
    var links = weekNav ? weekNav.querySelectorAll("a") : [];

    var show = function (n) {
      var i;
      for (i = 0; i < weeks.length; i++) {
        weeks[i].hidden = weeks[i].getAttribute("data-week") !== n;
      }
      for (i = 0; i < links.length; i++) {
        var on = links[i].getAttribute("data-week") === n;
        links[i].className = links[i].className.replace(/\s*\bon\b/, "");
        if (on) { links[i].className += " on"; }
      }
      if (weekPick) { weekPick.value = n; }
    };

    var opening = (weekNav && weekNav.querySelector("a.on")) || links[0];
    show(weekPick ? weekPick.value : opening.getAttribute("data-week"));

    if (weekNav) {
      weekNav.addEventListener("click", function (e) {
        var a = e.target.closest("a[data-week]");
        if (!a) { return; }
        e.preventDefault();
        show(a.getAttribute("data-week"));
      });
    }
    if (weekPick) {
      weekPick.addEventListener("change", function () { show(weekPick.value); });
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
