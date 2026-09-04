(function () {
  var el = document.getElementById("players");
  if (!el) { return; }
  var players = JSON.parse(el.textContent);
  var season = JSON.parse(document.getElementById("meta").textContent).season;
  var byId = {};
  players.forEach(function (p) { byId[String(p.id)] = p; });

  var radios = Array.prototype.slice.call(document.querySelectorAll(".assign"));
  var voids = Array.prototype.slice.call(document.querySelectorAll(".void-box"));
  var modal = document.getElementById("modal");
  var openFor = null;

  function field(cls, phase) {
    return document.querySelector("." + cls + "[data-phase='" + phase + "']");
  }

  function assigned() {
    var out = {};
    radios.forEach(function (r) {
      if (r.checked) { out[r.dataset.phase] = r.dataset.id; }
    });
    return out;
  }

  function openModal(phase, id) {
    var p = byId[id];
    if (!p) { return; }
    openFor = phase;
    document.getElementById("modal-title").textContent = p.name;
    document.getElementById("modal-sub").textContent =
      p.pos + " \u00b7 keeper year two \u00b7 R" + p.cost + " in " + season +
      " either way. The choice is what happens after.";
    document.getElementById("modal-options").innerHTML =
      "<button type='button' class='opt' data-term='1'><b>1 year</b><span>R" +
      p.cost + " in " + season + ", then ineligible in " + (season + 1) +
      ".</span></button><button type='button' class='opt' data-term='3'><b>3 years</b>" +
      "<span>R" + p.cost + " in " + season + ", then R" + p.later + " in " +
      (season + 1) + " and " + (season + 2) + ".</span></button>";
    document.querySelectorAll("#modal-options .opt").forEach(function (b) {
      b.addEventListener("click", function () {
        field("term-field", phase).value = b.dataset.term;
        modal.hidden = true;
        openFor = null;
        render();
      });
    });
    modal.hidden = false;
    document.getElementById("modal-cancel").focus();
  }

  function cancelModal() {
    if (openFor !== null && !field("term-field", openFor).value) {
      radios.forEach(function (r) {
        if (r.dataset.phase === openFor) { r.checked = false; r.dataset.wasChecked = "0"; }
      });
    }
    modal.hidden = true;
    openFor = null;
    render();
  }

  document.getElementById("modal-cancel").addEventListener("click", cancelModal);
  modal.addEventListener("click", function (e) { if (e.target === modal) { cancelModal(); } });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !modal.hidden) { cancelModal(); }
  });

  function lockedRounds() {
    var out = [];
    document.querySelectorAll(".settled b").forEach(function (b) {
      var m = /^R(\d+)/.exec(b.textContent.trim());
      if (m) { out.push(parseInt(m[1], 10)); }
    });
    return out;
  }

  function firstFree(from, taken) {
    for (var r = from; r >= 1; r--) {
      if (taken.indexOf(r) === -1) { return r; }
    }
    return null;
  }

  function render() {
    var taken = lockedRounds();
    var lines = [];
    var problems = [];
    var picked = assigned();
    var seen = [];

    document.querySelectorAll(".settled b").forEach(function (b) {
      lines.push({ round: 0, text: b.textContent.trim() + " <span class='tm'>locked</span>" });
    });
    document.querySelectorAll(".term-line").forEach(function (t) { t.innerHTML = ""; });
    document.querySelectorAll(".slot-fill").forEach(function (s) {
      s.innerHTML = "<span class='tm'>nothing planned</span>";
    });
    document.querySelectorAll(".picker-table tbody tr").forEach(function (tr) {
      tr.classList.remove("picked");
    });

    Object.keys(picked).sort().forEach(function (phase) {
      var id = picked[phase];
      var p = byId[id];
      var pf = field("pick-field", phase);
      if (pf) { pf.value = id; }

      if (seen.indexOf(id) !== -1) {
        problems.push(p.name + " is assigned to more than one phase.");
        return;
      }
      seen.push(id);

      var rd = firstFree(p.cost, taken);
      if (rd === null) {
        problems.push(p.name + " cannot be kept, no free round at or below R" + p.cost + ".");
        return;
      }
      taken.push(rd);

      var row = document.querySelector(".picker-table tr[data-id='" + id + "']");
      if (row) { row.classList.add("picked"); }

      var slot = document.querySelector(".slot-fill[data-phase='" + phase + "']");
      if (slot) { slot.innerHTML = "<b>R" + rd + " " + p.name + "</b>"; }

      var note = "R" + rd;
      if (rd !== p.cost) { note += " <span class='tm'>from R" + p.cost + "</span>"; }
      if (p.state === "must_sign") {
        var t = field("term-field", phase).value;
        if (!t) {
          problems.push(p.name + " needs a contract term.");
          note += " <button type='button' class='term-btn needed' data-phase='" +
                  phase + "' data-id='" + id + "'>Choose term</button>";
        } else {
          note += " &middot; " + (t === "1" ? "1 yr" : "3 yr then R" + p.later) +
                  " <button type='button' class='term-btn edit' data-phase='" +
                  phase + "' data-id='" + id + "'>Edit</button>";
        }
      }
      var cell = document.querySelector(".term-line[data-id='" + id + "']");
      if (cell) { cell.innerHTML = note; }

      lines.push({ round: rd, text: "R" + rd + " " + p.name });
    });

    ["1", "2", "3"].forEach(function (n) {
      if (!picked[n]) {
        var pf = field("pick-field", n);
        if (pf) { pf.value = ""; }
      }
    });

    document.querySelectorAll(".term-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        openModal(b.dataset.phase, b.dataset.id);
      });
    });

    voids.forEach(function (v) {
      if (v.checked) {
        lines.push({ round: parseInt(v.dataset.penalty, 10),
                     text: "R" + v.dataset.penalty +
                           " forced defence <span class='tm'>void penalty</span>" });
      }
    });

    var list = document.getElementById("chosen");
    list.innerHTML = "";
    lines.sort(function (a, b) { return a.round - b.round; }).forEach(function (l) {
      list.innerHTML += "<li>" + l.text + "</li>";
    });

    var warn = document.getElementById("warn");
    warn.innerHTML = problems.join("<br>");
    warn.className = problems.length ? "note error" : "note";
  }

  radios.forEach(function (r) {
    r.addEventListener("click", function () {
      if (r.dataset.wasChecked === "1") {
        r.checked = false;
        r.dataset.wasChecked = "0";
        field("term-field", r.dataset.phase).value = "";
        render();
        return;
      }
      radios.forEach(function (o) {
        if (o.dataset.phase === r.dataset.phase) { o.dataset.wasChecked = "0"; }
      });
      r.dataset.wasChecked = "1";
      field("term-field", r.dataset.phase).value = "";
      render();
      var p = byId[r.dataset.id];
      if (p && p.state === "must_sign") { openModal(r.dataset.phase, r.dataset.id); }
    });
    if (r.checked) { r.dataset.wasChecked = "1"; }
  });

  voids.forEach(function (v) { v.addEventListener("change", render); });

  var clear = document.getElementById("clear");
  if (clear) {
    clear.addEventListener("click", function () {
      radios.forEach(function (r) { r.checked = false; r.dataset.wasChecked = "0"; });
      document.querySelectorAll(".term-field").forEach(function (t) { t.value = ""; });
      render();
    });
  }

  render();
})();
