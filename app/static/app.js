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

  // ---- season page: close the year picker on an outside click ----
  // <details> stays open until it is clicked again, which is right for a
  // disclosure and wrong for a menu. Enhancement only: without this the
  // picker still opens, still navigates, and still closes on a second click.
  var headpick = document.querySelector(".headpick");
  if (headpick) {
    document.addEventListener("click", function (e) {
      if (headpick.open && !headpick.contains(e.target)) { headpick.open = false; }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && headpick.open) { headpick.open = false; }
    });
  }

  // ---- season page: bracket or standings ----
  // Both panels are on the page and visible until this runs, so without the
  // script nothing is lost -- you simply get the bracket above the table.
  // The buttons are hidden in the markup for the same reason: a control that
  // cannot do anything should not be offered.
  var sw = document.querySelector(".switch");
  if (sw) {
    var panels = document.querySelectorAll("[data-panel]");
    var buttons = sw.querySelectorAll("button");

    var showPanel = function (which) {
      var i;
      for (i = 0; i < panels.length; i++) {
        panels[i].hidden = panels[i].getAttribute("data-panel") !== which;
      }
      for (i = 0; i < buttons.length; i++) {
        var on = buttons[i].getAttribute("data-shows") === which;
        buttons[i].classList.toggle("on", on);
        buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
      }
    };

    sw.hidden = false;
    // Whichever button comes first, which is the championship bracket when
    // there is one: it only exists once a playoff game does, and when it
    // exists it is the more interesting half.
    showPanel(buttons[0].getAttribute("data-shows"));

    sw.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-shows]");
      if (b) { showPanel(b.getAttribute("data-shows")); }
    });
  }

  // ---- one panel at a time, chosen from a strip ----
  // The server renders every panel and marks one open. Without this script
  // they all stay visible and the strip's links simply jump to them, so the
  // page still works.
  //
  // A season's weeks have two controls -- a strip of seventeen on wide
  // screens, a select on narrow ones -- and CSS shows one at a time. Both are
  // updated on every change rather than only the visible one, so resizing or
  // rotating never reveals a control pointing at a different panel. A
  // manager's keeper seasons have the strip alone; three buttons need no
  // dropdown.
  var strips = function (stripSel, panelSel, attr, selectId) {
    var nav = document.querySelector(stripSel);
    var pick = selectId ? document.getElementById(selectId) : null;
    if (!nav && !pick) { return; }

    var panels = document.querySelectorAll(panelSel);
    var links = nav ? nav.querySelectorAll("a") : [];

    var show = function (n) {
      var i;
      for (i = 0; i < panels.length; i++) {
        panels[i].hidden = panels[i].getAttribute(attr) !== n;
      }
      for (i = 0; i < links.length; i++) {
        var on = links[i].getAttribute(attr) === n;
        links[i].className = links[i].className.replace(/\s*\bon\b/, "");
        if (on) { links[i].className += " on"; }
      }
      if (pick) { pick.value = n; }
    };

    var opening = (nav && nav.querySelector("a.on")) || links[0];
    if (!pick && !opening) { return; }
    show(pick ? pick.value : opening.getAttribute(attr));

    if (nav) {
      nav.addEventListener("click", function (e) {
        var a = e.target.closest("a[" + attr + "]");
        if (!a) { return; }
        e.preventDefault();
        show(a.getAttribute(attr));
      });
    }
    if (pick) {
      pick.addEventListener("change", function () { show(pick.value); });
    }
  };

  strips("#weekstrip", ".week", "data-week", "weekpick");
  strips("#keeperstrip", ".keeper-season", "data-season", null);

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
