(function () {
  var el = document.getElementById("players");
  if (!el) { return; }
  var players = JSON.parse(el.textContent);
  var season = JSON.parse(document.getElementById("meta").textContent).season;
  var byId = {};
  players.forEach(function (p) { byId[String(p.id)] = p; });

  // One select per round, in the round's own card. Choosing is done there
  // now; the table below is a list of who is left and where they have gone.
  var picks = Array.prototype.slice.call(document.querySelectorAll(".phase-pick"));
  var voids = Array.prototype.slice.call(document.querySelectorAll(".void-box"));
  var modal = document.getElementById("modal");
  var openFor = null;

  // The chosen player per round, keyed by round number. Seeded from the
  // hidden fields the server renders from a saved plan, so a reload comes
  // back with the plan you left.
  var chosen = {};
  picks.forEach(function (s) {
    var f = document.querySelector(".pick-field[data-phase='" + s.dataset.phase + "']");
    if (f && f.value) { chosen[s.dataset.phase] = f.value; }
  });

  function field(cls, phase) {
    return document.querySelector("." + cls + "[data-phase='" + phase + "']");
  }

  function assigned() {
    return chosen;
  }

  // Rebuild every round's options from the players not spoken for elsewhere,
  // which is what makes the list shrink as you fill rounds in.
  function fillSelects() {
    picks.forEach(function (s) {
      var phase = s.dataset.phase;
      var mine = chosen[phase] || "";
      var html = "<option value=''>choose a keeper…</option>";
      players.forEach(function (p) {
        var id = String(p.id);
        var elsewhere = Object.keys(chosen).some(function (n) {
          return n !== phase && chosen[n] === id;
        });
        if (elsewhere) { return; }
        html += "<option value='" + id + "'" + (mine === id ? " selected" : "") +
                ">R" + p.cost + " " + p.name + "</option>";
      });
      s.innerHTML = html;
      s.value = mine;
    });
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
    // Backing out of the term question undoes the choice that asked it: a
    // must-sign keeper with no term is not a keeper.
    if (openFor !== null && !field("term-field", openFor).value) {
      delete chosen[openFor];
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
    document.querySelectorAll(".voided-mark").forEach(function (v) {
      out.push(parseInt(v.dataset.penalty, 10));
    });
    voids.forEach(function (v) {
      if (v.checked) { out.push(parseInt(v.dataset.penalty, 10)); }
    });
    return out;
  }

  // The round a keeper actually costs when its own is spoken for: no two of
  // your keepers may cost the same round, and the later arrival moves up to
  // the next free, more expensive one. Up the board is down the number.
  //
  // This mirrors next_free_round() in app/main.py and takes its arguments in
  // the same order so the two read alike. It is the one rule this file
  // restates rather than being told, and it has to be: the card redraws on
  // every click and cannot ask the server between them. The server decides,
  // so a divergence shows up as a preview that lied, not as a keeper in the
  // wrong round. Change one and change the other.
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
    document.querySelectorAll(".slot-fill").forEach(function (s) {
      s.innerHTML = "<span class='tm'>nothing planned</span>";
    });
    document.querySelectorAll(".moved-note, .term-note").forEach(function (n) {
      n.hidden = true;
      n.innerHTML = "";
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

      // A keeper whose own round is spoken for moves up, and the card says
      // so. It used to happen silently: you chose an R13 and the card showed
      // R12 with nothing to explain the difference, which reads as a bug
      // rather than as the rule it is. The line sits under the chooser,
      // because it is a consequence of the choice rather than part of the
      // name above it.
      var moved = rd !== p.cost;
      var slot = document.querySelector(".slot-fill[data-phase='" + phase + "']");
      if (slot) { slot.innerHTML = "<b>R" + rd + " " + p.name + "</b>"; }

      var why = document.querySelector(".moved-note[data-phase='" + phase + "']");
      if (why) {
        why.hidden = !moved;
        why.textContent = moved
            ? "R" + p.cost + " is taken, so this one moves up to R" + rd + "."
            : "";
      }

      // The contract goes in the round's own card, because that is where the
      // question is being asked. The table keeps the round and the cost and
      // nothing that needs answering.
      var term = document.querySelector(".term-note[data-phase='" + phase + "']");
      if (term) {
        if (p.state !== "must_sign") {
          term.hidden = true;
          term.innerHTML = "";
        } else {
          var t = field("term-field", phase).value;
          term.hidden = false;
          if (!t) {
            problems.push(p.name + " needs a contract term.");
            term.innerHTML = "Needs a contract term " +
              "<button type='button' class='term-btn needed' data-phase='" +
              phase + "' data-id='" + id + "'>Choose term</button>";
          } else {
            term.innerHTML = "contract " +
              (t === "1" ? "1/1" : "1/3, then R" + p.later) +
              " <button type='button' class='term-btn edit' data-phase='" +
              phase + "' data-id='" + id + "'>Edit</button>";
          }
        }
      }

      lines.push({ round: rd,
                   text: "R" + rd + " " + p.name +
                         (moved ? " <span class='tm'>up from R" + p.cost +
                                  "</span>" : "") });
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

    document.querySelectorAll(".voided-mark").forEach(function (m) {
      lines.push({ round: parseInt(m.dataset.penalty, 10),
                   text: "R" + m.dataset.penalty +
                         " forced defence <span class='tm'>void, binding</span>" });
    });

    // The plan had a summary list of its own; the round cards say the same
    // thing in the place you chose it, so the list is gone and this tolerates
    // its absence rather than assuming it.
    var list = document.getElementById("chosen");
    if (list) {
      list.innerHTML = "";
      lines.sort(function (a, b) { return a.round - b.round; }).forEach(function (l) {
        list.innerHTML += "<li>" + l.text + "</li>";
      });
    }

    var warn = document.getElementById("warn");
    warn.innerHTML = problems.join("<br>");
    warn.className = problems.length ? "note error" : "note";

    // Forfeiting and choosing are opposite answers to the same question, so
    // only one of them is ever on offer for a round.
    document.querySelectorAll(".forfeit-btn").forEach(function (b) {
      b.hidden = !!chosen[b.dataset.phase];
    });

    // Last, so the options reflect what was just decided: a player placed in
    // one round drops out of the others' lists.
    fillSelects();
  }

  picks.forEach(function (s) {
    s.addEventListener("change", function () {
      var phase = s.dataset.phase;
      if (s.value) {
        chosen[phase] = s.value;
      } else {
        delete chosen[phase];
      }
      field("term-field", phase).value = "";
      render();
      var p = byId[s.value];
      if (p && p.state === "must_sign") { openModal(phase, s.value); }
    });
  });

  // A button that asks before it acts. The page is one big form, so this is
  // per button rather than per form the way app.js does it.
  document.querySelectorAll("button[data-confirm]").forEach(function (b) {
    b.addEventListener("click", function (e) {
      if (!window.confirm(b.getAttribute("data-confirm"))) { e.preventDefault(); }
    });
  });

  voids.forEach(function (v) { v.addEventListener("change", render); });

  var clear = document.getElementById("clear");
  if (clear) {
    clear.addEventListener("click", function () {
      chosen = {};
      document.querySelectorAll(".term-field").forEach(function (t) { t.value = ""; });
      render();
    });
  }

  render();
})();

// ---- void warning, live ----
(function () {
  var boxes = Array.prototype.slice.call(document.querySelectorAll(".void-box"));
  var banner = document.getElementById("void-warning");
  if (!boxes.length || !banner) { return; }

  var namesEl = document.getElementById("void-names");
  var submitWrap = document.getElementById("void-submit-wrap");


  function update() {
    var ticked = boxes.filter(function (b) { return b.checked; });
    if (!ticked.length) {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
    namesEl.textContent = ticked.map(function (b) {
      return b.dataset.name + " (DEF at R" + b.dataset.penalty + ")";
    }).join(", ");

    submitWrap.hidden = false;
  }

  boxes.forEach(function (b) { b.addEventListener("change", update); });
  update();
})();




// ---- confirm before submitting a void that wipes plans ----
(function () {
  var btn = document.getElementById("void-submit-btn");
  if (!btn) { return; }
  btn.addEventListener("click", function (e) {
    if (btn.dataset.hasplans !== "1") { return; }
    var ok = window.confirm(
      "Submitting this void will erase your saved keeper plan for every phase, " +
      "because voiding changes which rounds are available.\n\n" +
      "Your plan will need to be built again. Continue?");
    if (!ok) { e.preventDefault(); }
  });
})();
