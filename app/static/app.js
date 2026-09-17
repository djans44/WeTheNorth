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

    // Each group gets its own chevron, closed to start with. Fully unfolded
    // the menu is about thirty-seven rows for an admin -- twelve managers and
    // five seasons on their own are seventeen -- which is why it had to cap
    // its height and scroll inside itself. Closed, it is seven.
    //
    // A button beside the parent rather than the parent itself: every one of
    // these labels is a real link to a real page, and Seasons going nowhere
    // until you have opened and chosen would cost a tap to reach the page it
    // already points at.
    //
    // Built here for the same reason the hamburger is: with the script off
    // .collapsible is never set, the CSS that hides the submenus never
    // applies, and the nav stays fully open exactly as it was.
    var groups = siteNav.querySelectorAll(".has-menu");
    for (var g = 0; g < groups.length; g++) {
      (function (li) {
        var sub = li.querySelector(".submenu");
        // Document order, so this is the group's own link rather than one of
        // the submenu's.
        var parent = li.querySelector("a");
        if (!sub || !parent) { return; }
        var b = document.createElement("button");
        b.type = "button";
        b.className = "subtoggle";
        b.setAttribute("aria-expanded", "false");
        b.setAttribute("aria-label", parent.textContent.trim());
        b.innerHTML =
          '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">' +
          '<path d="M6 8l4 4 4-4" fill="none" stroke="currentColor" ' +
          'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        b.addEventListener("click", function () {
          var open = li.classList.toggle("open");
          b.setAttribute("aria-expanded", open ? "true" : "false");
          publishNavHeight();
        });
        li.insertBefore(b, sub);
      }(groups[g]));
    }
  }

  // ---- a sticky manager header condenses once it sticks ----
  // Enhancement only: without this the header still sticks, it just stays at
  // full height. The class goes on past the header's own height so the change
  // happens after it has actually reached the top, not while it is still
  // scrolling towards it.
  var stuck = document.querySelector(".page-head.stuck");
  if (stuck) {
    var fullHeight = 0;
    // How far down the document the header sits. It is inside the page panel,
    // which starts below the site nav, so it has ninety-odd pixels to travel
    // before it touches the top of the window.
    var reaches = 0;
    var frozen = function () {
      return window.getComputedStyle(stuck).position === "sticky";
    };

    var measure = function () {
      stuck.classList.remove("condensed");
      stuck.style.marginBottom = "";
      fullHeight = stuck.offsetHeight;
      reaches = stuck.getBoundingClientRect().top + window.scrollY;
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
    //
    // "Starts to stick" is when it reaches the top of the window, not when
    // you begin to scroll. scrollY > 4 was the latter, so the header shrank
    // while it was still travelling down the page and arrived already small.
    // Measured rather than guessed, because the distance depends on the nav
    // above it, which changes height on a phone.
    var syncStuck = function () {
      if (!frozen()) {
        stuck.classList.remove("condensed");
        stuck.style.marginBottom = "";
        return;
      }
      var want = window.scrollY + 1 >= reaches;
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

  // ---- the bar that arrives once the header has gone ----
  // The other model, .page-head.stuck above, keeps a tall header pinned and
  // shrinks it the moment it sticks. That does not move the content -- it
  // uncovers 130px of it in one frame, which is what reads as a lurch. Here
  // the header scrolls away like anything else and this slides in after it,
  // so nothing is ever covered and then uncovered.
  //
  // Nothing to compensate for: the bar is fixed, so it never occupied a place
  // in the flow to give back.
  var bar = document.querySelector(".topbar");
  if (bar) {
    var barHead = document.querySelector(".page-head");
    var showAt = 0;
    var measureBar = function () {
      // The foot of the header in document coordinates. Read at rest and on
      // resize, never mid-scroll, so a reflow cannot creep into a scroll
      // handler that runs on every frame.
      showAt = barHead ? barHead.getBoundingClientRect().bottom + window.scrollY : 0;
    };
    var syncBar = function () {
      bar.classList.toggle("up", window.scrollY >= showAt);
    };
    measureBar();
    syncBar();
    window.addEventListener("scroll", syncBar, { passive: true });
    window.addEventListener("resize", function () {
      measureBar();
      syncBar();
    });
    // The portrait and the sigils settle after this runs, and the header's
    // foot moves when they do. Re-measure once everything has loaded.
    window.addEventListener("load", function () {
      measureBar();
      syncBar();
    });
  }

  // ---- a live draft reloads itself ----
  // Twelve people watch /draft-order during the one session a year where the
  // order is chosen, and nothing on it used to change until they reloaded.
  //
  // A whole reload rather than polling a state endpoint and swapping the
  // board in: this runs for twenty minutes a year, and code that rare is
  // untested code, so what matters is how it fails. This fails by not
  // reloading, which is what every other day of the year looks like. A swap
  // fails by showing a board where a taken slot still looks free.
  var live = document.querySelector("[data-live]");
  if (live) {
    // Bounded, because a tab left open on a draft that stopped half way
    // would hold Render's free instance awake indefinitely for nobody. Per
    // tab, and the hour starts again in a new one.
    var LIMIT = 60 * 60 * 1000;
    var began = 0;
    try { began = Number(sessionStorage.getItem("draftlive")) || 0; } catch (e) {}
    if (!began) {
      began = Date.now();
      try { sessionStorage.setItem("draftlive", began); } catch (e) {}
    }

    if (Date.now() - began < LIMIT) {
      // location.reload() and not a fixed URL: app.js has already dropped
      // ?msg= from the address by now, so a pick's toast does not come back
      // every ten seconds.
      var beat = setInterval(function () { window.location.reload(); }, 10000);

      // Anything chosen or typed stops it, because a reload would throw the
      // entry away. Capturing, so it does not matter which form it was.
      // Submitting starts it again: the post redirects to a fresh page.
      var hold = function () {
        clearInterval(beat);
        live.hidden = false;
      };
      document.addEventListener("change", hold, true);
      document.addEventListener("input", hold, true);
    }
  }

  // ---- a form that asks before it acts ----
  // data-confirm on the form, its text the question. For the few posts that
  // destroy something and have no undo. Enhancement only: without the script
  // the form still submits, so the button's own wording has to be plain.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) {
        e.preventDefault();
      }
    });
  });

  // ---- timestamps in the reader's own clock ----
  // The server writes UTC because it cannot know where anyone is. Any <time>
  // with a datetime attribute is rewritten here to the reader's zone and
  // their locale's own way of writing it, so a 9:33pm pick in Toronto does
  // not read as 02:33 the next morning. Enhancement only: the words already
  // in the element say UTC, so the script being off is honest rather than
  // wrong.
  document.querySelectorAll("time[datetime]").forEach(function (el) {
    var when = new Date(el.getAttribute("datetime"));
    if (isNaN(when.getTime())) { return; }
    el.textContent = when.toLocaleString([], {
      month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit"
    });
    el.title = when.toLocaleString();
  });

  // ---- the draft board is the chooser ----
  // Enhancement only. Without the script the confirm button is simply always
  // live, and the radio's own required attribute stops an empty submission.
  // With it the button waits until a number is chosen and then says which,
  // so a live pick in front of eleven other people is confirmed by name.
  var confirmRow = document.querySelector(".pickconfirm");
  if (confirmRow) {
    var confirmBtn = confirmRow.querySelector("button");
    var forWhom = confirmBtn.getAttribute("data-for");
    var hint = confirmRow.querySelector(".tm");

    var syncConfirm = function () {
      var picked = document.querySelector(".slot.open input:checked");
      confirmBtn.disabled = !picked;
      confirmBtn.textContent = picked
          ? "Take slot " + picked.value + (forWhom ? " for " + forWhom : "")
          : "Take this slot" + (forWhom ? " for " + forWhom : "");
      // The hint changes rather than going away, because the second thing it
      // says -- that the gesture undoes itself -- is the part nobody would
      // guess. A radio group has no way back to nothing on its own.
      if (hint) {
        hint.textContent = picked
            ? "Click slot " + picked.value + " again to clear it."
            : "Choose a number above first.";
      }
    };

    // A radio cannot be unchecked by clicking it, so the second click is
    // handled here. click fires after the browser has already checked it,
    // which is why the previous choice is remembered rather than read.
    var lastPicked = null;
    document.querySelectorAll(".slot.open input").forEach(function (input) {
      input.addEventListener("click", function () {
        if (lastPicked === input) {
          input.checked = false;
          lastPicked = null;
        } else {
          lastPicked = input;
        }
        syncConfirm();
      });
    });
    syncConfirm();
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
  // A manager's keeper seasons are chosen this way, and so are a season's
  // titles. A season's weeks are not: the week selector there changes the
  // whole shape of the page, not just which scores are on it, so it is
  // ordinary links and the server draws the week.
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

  strips(null, ".keeper-season", "data-season", "keeperpick");

  // ---- a button that says it is working ----
  // Some posts take most of a minute -- writing a summary is a call out to a
  // model. The form posts normally and the answer is the next page, so there
  // is nothing to poll; what was missing was any sign that the press had
  // landed, and a second press would start a second generation. Opt in with
  // data-working on the form, whose value is what the button should say
  // while it waits.
  var slow = document.querySelectorAll("form[data-working]");
  for (var w = 0; w < slow.length; w++) {
    (function (form) {
      form.addEventListener("submit", function () {
        var btn = form.querySelector("button[type=submit]");
        if (!btn || btn.className.indexOf("working") !== -1) { return; }
        // Pinned before the label is swapped: "Writing" is half the width of
        // "Write a 2026 preview", and a button that shrinks under the cursor
        // reads as a mis-click rather than as progress.
        btn.style.minWidth = btn.offsetWidth + "px";
        btn.className += " working";
        btn.textContent = form.getAttribute("data-working");
        // Disabled after the event rather than inside it: a control disabled
        // during its own submit handler is not always sent with the form.
        window.setTimeout(function () { btn.disabled = true; }, 0);
      });
    }(slow[w]));
  }

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
        // data-needs-value: the blank option is this form's resting state,
        // not a choice. Without the opt-in an empty value still posts,
        // because elsewhere -- the seat picker -- blank is how you clear one.
        if (form.hasAttribute("data-needs-value") && !sel.value) { return; }
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
