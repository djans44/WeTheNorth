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

  // ---- a sticky manager header condenses once it sticks ----
  // Enhancement only: without this the header still sticks, it just stays at
  // full height. The class goes on past the header's own height so the change
  // happens after it has actually reached the top, not while it is still
  // scrolling towards it.
  var stuck = document.querySelector(".page-head.stuck");
  if (stuck) {
    var fullHeight = 0;
    var frozen = function () {
      return window.getComputedStyle(stuck).position === "sticky";
    };

    var measure = function () {
      stuck.classList.remove("condensed");
      stuck.style.marginBottom = "";
      fullHeight = stuck.offsetHeight;
    };

    // The header keeps its place in the flow while stuck, so shrinking it
    // pulled everything below up by the difference -- a 177px jolt. The
    // margin gives that space back: the visible bar is small, the space it
    // occupies is unchanged, and nothing moves.
    //
    // A wrapper would have been the obvious way to hold that space, and it
    // does not work: a sticky element cannot travel outside its parent, so a
    // wrapper the height of the header stops it sticking at all.
    //
    // Condensed the moment it starts to stick, rather than part way down the
    // page: whole at the top, a bar once you have left it, nothing between.
    var syncStuck = function () {
      if (!frozen()) {
        stuck.classList.remove("condensed");
        stuck.style.marginBottom = "";
        return;
      }
      var want = window.scrollY > 4;
      if (want === stuck.classList.contains("condensed")) { return; }
      stuck.classList.toggle("condensed", want);
      stuck.style.marginBottom =
          want ? (fullHeight - stuck.offsetHeight) + "px" : "";
    };

    measure();
    syncStuck();
    window.addEventListener("scroll", syncStuck, { passive: true });
    window.addEventListener("resize", function () {
      if (window.scrollY <= 4) { measure(); }
      syncStuck();
    });
  }

  // ---- head-to-head: click a row to leave it lit ----
  // Enhancement only: hovering already highlights a row in CSS. This is for
  // a touch screen, which has no hover, and for reading down a column
  // without losing which row you are on.
  var h2h = document.querySelector("#h2h .grid tbody");
  if (h2h) {
    h2h.addEventListener("click", function (e) {
      var tr = e.target.closest("tr");
      if (!tr || !h2h.contains(tr)) { return; }
      var already = tr.classList.contains("lit");
      var i;
      var lit = h2h.querySelectorAll("tr.lit");
      for (i = 0; i < lit.length; i++) { lit[i].classList.remove("lit"); }
      if (!already) { tr.classList.add("lit"); }
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
    var nav = stripSel ? document.querySelector(stripSel) : null;
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
  strips(null, ".keeper-season", "data-season", "keeperpick");

  // ---- a picker that loads when you choose ----
  // Opt in with class="loadonpick". The Load button stays in the markup and
  // is hidden here, so the form still works with the script off -- the same
  // bargain the sign-in button makes.
  var pickers = document.querySelectorAll("form.loadonpick");
  for (var p = 0; p < pickers.length; p++) {
    (function (form) {
      var sel = form.querySelector("select");
      var btn = form.querySelector("button[type=submit]");
      if (!sel) { return; }
      if (btn) { btn.hidden = true; }
      // requestSubmit, not submit: the plain method skips validation and
      // fires no submit event, so nothing else on the page can see it go.
      sel.addEventListener("change", function () {
        if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
      });
    }(pickers[p]));
  }
  // Table columns rather than panels, and a dropdown with no strip beside
  // it. The helper cares about neither: it hides whatever the selector
  // matches, and it drives from the select when there is no strip.
  strips(null, ".yr", "data-year", "titlepick");

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
