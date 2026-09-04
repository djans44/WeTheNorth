(function () {
  var players = JSON.parse(document.getElementById("players").textContent);
  var meta = JSON.parse(document.getElementById("meta").textContent);
  var season = meta.season;
  var byId = {};
  players.forEach(function (p) { byId[p.id] = p; });

  var voids = document.querySelectorAll(".void-box");
  var picks = document.querySelectorAll(".pick-box");
  var modal = document.getElementById("modal");
  var terms = {};
  var openFor = null;
  var bumped = {};

  function openModal(id) {
    var p = byId[id];
    document.getElementById("modal-title").textContent = p.name;
    document.getElementById("modal-sub").textContent =
      p.pos + " \u00b7 keeper year two \u00b7 costs R" + p.cost + " in " + season +
      " either way. The choice is what happens after.";

    var opts = document.getElementById("modal-options");
    opts.innerHTML =
      "<button type='button' class='opt' data-term='1'>" +
        "<b>1 year</b>" +
        "<span>R" + p.cost + " in " + season + ", then ineligible in " + (season + 1) + ".</span>" +
      "</button>" +
      "<button type='button' class='opt' data-term='3'>" +
        "<b>3 years</b>" +
        "<span>R" + p.cost + " in " + season + ", then R" + p.later + " in " +
        (season + 1) + " and " + (season + 2) + ". Ineligible in " + (season + 3) + ".</span>" +
      "</button>";

    opts.querySelectorAll(".opt").forEach(function (b) {
      b.addEventListener("click", function () {
        terms[id] = b.dataset.term;
        closeModal();
      });
    });

    openFor = id;
    modal.hidden = false;
    document.getElementById("modal-cancel").focus();
  }

  function closeModal() {
    modal.hidden = true;
    if (openFor && !terms[openFor]) {
      var b = document.querySelector(".pick-box[data-id='" + openFor + "']");
      if (b) { b.checked = false; }
    }
    openFor = null;
    render();
  }

  document.getElementById("modal-cancel").addEventListener("click", closeModal);
  modal.addEventListener("click", function (e) {
    if (e.target === modal) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeModal();
  });

  function activeContracts() {
    var out = [];
    players.forEach(function (p) {
      if (p.state !== "contract") return;
      var box = document.querySelector(".void-box[data-id='" + p.id + "']");
      if (!box || !box.checked) out.push(p);
    });
    return out;
  }

  function chosen() {
    var out = [];
    picks.forEach(function (b) { if (b.checked) out.push(byId[b.dataset.id]); });
    return out;
  }

  function firstFree(from, taken) {
    for (var r = from; r >= 1; r--) {
      if (taken.indexOf(r) === -1) return r;
    }
    return null;
  }

  function renderTermCells() {
    document.querySelectorAll(".term-cell").forEach(function (cell) {
      var id = cell.dataset.id;
      var p = byId[id];
      if (!p || p.state !== "must_sign") return;
      var box = document.querySelector(".pick-box[data-id='" + id + "']");
      var picked = box && box.checked;
      var t = terms[id];

      if (!picked) {
        cell.innerHTML = "<span class='tm'>select to choose a term</span>";
        return;
      }
      if (!t) {
        cell.innerHTML = "<button type='button' class='term-btn needed' data-id='" +
          id + "'>Choose term</button>";
      } else {
        var label = t === "1" ? "1 year" : "3 years, then R" + p.later;
        cell.innerHTML = "<span class='chosen-term'>" + label + "</span>" +
          "<button type='button' class='term-btn edit' data-id='" + id + "'>Edit</button>";
      }
      cell.querySelectorAll(".term-btn").forEach(function (b) {
        b.addEventListener("click", function () { openModal(b.dataset.id); });
      });
    });
  }

  function render() {
    var held = activeContracts();
    var sel = chosen();
    var all = held.concat(sel);
    document.getElementById("used").textContent = all.length;

    var taken = held.map(function (p) { return p.cost; });
    var resolved = held.map(function (p) {
      return { p: p, round: p.cost, locked: true };
    });
    var conflicts = [];

    sel.forEach(function (p) {
      if (taken.indexOf(p.cost) === -1) {
        taken.push(p.cost);
        resolved.push({ p: p, round: p.cost, locked: false });
      } else {
        conflicts.push(p);
      }
    });

    var box = document.getElementById("conflicts");
    box.innerHTML = "";
    var impossible = false;

    conflicts.forEach(function (p) {
      var rival = resolved.filter(function (r) { return r.round === p.cost; })[0];
      var target = firstFree(p.cost - 1, taken);

      if (target === null) {
        impossible = true;
        box.innerHTML += "<div class='errors'><b>" + p.name +
          " cannot be kept.</b> No earlier round is free" +
          (p.cost === 1 ? ", and two round 1 keepers are not allowed." : ".") + "</div>";
        return;
      }

      var canMoveRival = rival && !rival.locked;
      var pick = bumped[p.id];
      var html = "<div class='conflict'><b>Round " + p.cost + " conflict.</b> " +
        p.name + " and " + rival.p.name + " both cost R" + p.cost +
        ". One moves to R" + target + ".";

      if (!canMoveRival) {
        html += " " + rival.p.name + " is under contract and cannot move, so " +
          p.name + " goes to R" + target + ".";
      } else {
        html += "<div class='choose'>" +
          "<label><input type='radio' name='bump" + p.id + "' value='self'" +
          (pick !== "rival" ? " checked" : "") + "> " + p.name + " &rarr; R" + target + "</label>" +
          "<label><input type='radio' name='bump" + p.id + "' value='rival'" +
          (pick === "rival" ? " checked" : "") + "> " + rival.p.name + " &rarr; R" + target + "</label>" +
          "</div>";
      }
      box.innerHTML += html + "</div>";

      taken.push(target);
      if (pick === "rival" && canMoveRival) {
        rival.round = target;
        resolved.push({ p: p, round: p.cost, locked: false });
      } else {
        resolved.push({ p: p, round: target, locked: false });
      }
    });

    box.querySelectorAll("input[type=radio]").forEach(function (r) {
      r.addEventListener("change", function () {
        bumped[r.name.replace("bump", "")] = r.value;
        render();
      });
    });

    var list = document.getElementById("chosen");
    list.innerHTML = "";
    resolved.slice().sort(function (a, b) { return a.round - b.round; })
      .forEach(function (r) {
        var extra = r.locked ? " <span class='tm'>contract</span>" : "";
        if (r.p.state === "must_sign" && terms[r.p.id]) {
          extra += " <span class='tm'>" +
            (terms[r.p.id] === "1" ? "1 year" : "3 years") + "</span>";
        }
        list.innerHTML += "<li><b>R" + r.round + "</b> " + r.p.name + extra + "</li>";
      });

    voids.forEach(function (v) {
      if (v.checked) {
        list.innerHTML += "<li><b>R" + v.dataset.penalty +
          "</b> forced defence pick <span class='tm'>void penalty</span></li>";
      }
    });

    renderTermCells();

    var needTerm = sel.filter(function (p) {
      return p.state === "must_sign" && !terms[p.id];
    });

    var warn = document.getElementById("warn");
    if (all.length > 3) {
      warn.textContent = "Too many keepers. Deselect one, or void a contract.";
      warn.className = "note error";
    } else if (impossible) {
      warn.textContent = "Resolve the conflict above.";
      warn.className = "note error";
    } else if (needTerm.length) {
      warn.textContent = "Choose a contract term for " +
        needTerm.map(function (p) { return p.name; }).join(", ") + ".";
      warn.className = "note error";
    } else {
      warn.textContent = all.length + " of 3 slots used.";
      warn.className = "note";
    }
  }

  voids.forEach(function (v) { v.addEventListener("change", render); });
  picks.forEach(function (b) {
    b.addEventListener("change", function () {
      var p = byId[b.dataset.id];
      render();
      if (b.checked && p && p.state === "must_sign" && !terms[b.dataset.id]) {
        openModal(b.dataset.id);
      }
    });
  });
  render();
})();

