/* Листларни белгилаш → жуфтлаш → усул → «Бошлаш» (jobs/_picker.html).
   window.narxPicker.mount(root) — юклаш саҳифасида (файл юклангач) ва объект саҳифасида бир хил.
   Ҳар бир танлов дарҳол серверга сақланади; лист тури ўзгарса — блок қайта чизилади (қайта жуфтланади). */
(function () {
  "use strict";

  function csrf(form) { return form.querySelector("[name=csrfmiddlewaretoken]").value; }
  function toast(text, ok) {
    var t = document.createElement("div");
    t.className = "pk-toast" + (ok ? " is-ok" : "");
    t.textContent = text;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, ok ? 1600 : 4500);
  }
  function post(url, form, data) {
    var fd = new FormData();
    Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
    return fetch(url, {method: "POST", body: fd, credentials: "same-origin",
                       headers: {"X-CSRFToken": csrf(form), "X-Requested-With": "XMLHttpRequest"}})
      .then(function (r) {
        return r.json().catch(function () { return {ok: false, error: "сервер хатоси (" + r.status + ")"}; })
          .then(function (d) { if (!r.ok || !d.ok) throw new Error(d.error || "хато"); return d; });
      });
  }
  function tone(v) { return v == null ? "t-none" : v >= 0.7 ? "t-good" : v >= 0.4 ? "t-mid" : "t-low"; }
  function ovOf(opt) { return opt && opt.dataset.ov ? parseFloat(opt.dataset.ov.replace(",", ".")) : null; }

  function mount(root, opts) {
    opts = opts || {};
    var form = root.querySelector(".pk-form"), D = root.dataset;
    var SHEETS = JSON.parse(root.querySelector("script[type='application/json']").textContent), BY = {};
    SHEETS.forEach(function (s) { BY[s.k] = s; });
    var mine = D.mine === "1";
    var revealed = opts.revealed || {};  // аввал очилган қадамлар қайта чизилганда анимациясиз туради

    // ---------- ҳолат: қайси қадам тайёр, қайсиниси ҳозир ----------
    function pairs() { return Array.prototype.slice.call(root.querySelectorAll(".pk-pair")); }
    function update() {
      var hasLocal = !!root.querySelector(".pk-sheet.is-local:not(.is-dup)");
      var hasRes = !!root.querySelector(".pk-sheet.is-resource:not(.is-dup)") || !!root.querySelector("option[data-base]");
      var ready = 0, need = 0, used = {};
      pairs().forEach(function (row) {
        var sel = row.querySelector(".pk-select"), inc = row.querySelector("input[type=checkbox]"), opt = sel.options[sel.selectedIndex];
        var ov = row.querySelector(".pk-ov"), v = ovOf(opt);
        row.classList.toggle("is-off", !inc.checked);
        row.classList.toggle("is-ok", inc.checked && !!sel.value);
        row.classList.toggle("is-need", inc.checked && !sel.value && hasRes);
        if (opt && opt.dataset.base) { ov.textContent = "база"; ov.className = "pk-ov t-none"; }
        else if (!sel.value) { ov.textContent = "?"; ov.className = "pk-ov t-low"; }
        else { ov.textContent = v == null ? "—" : Math.round(v * 100) + "%"; ov.className = "pk-ov " + tone(v); }
        ov.title = "Локалдаги ресурсларнинг неча фоизи шу RES листида аниқ бор";
        if (inc.checked && sel.value) ready++;
        if (inc.checked && !sel.value) need++;
        if (inc.checked && sel.value) (used[sel.value] = used[sel.value] || []).push(row.dataset.tag || row.dataset.title);
      });
      // ресурс қаторида — қайси локаллар шу ердан нарх олади
      root.querySelectorAll(".pk-used").forEach(function (u) {
        var t = used[u.dataset.res];
        u.textContent = t ? "нарх беради: " + t.join(", ") : "ҳеч қайси локалга танланмаган";
        u.classList.toggle("is-idle", !t);
      });
      var nb = root.querySelector('.pk-filters [data-f="need"]');
      if (nb) { nb.hidden = !need; nb.querySelector(".pk-need-n").textContent = need; }
      // «Қўлда»: ҳамма лист смета­чи томонидан белгилангандагина кейинги қадам очилади
      var left = root.querySelectorAll(".pk-sheet.is-none:not(.is-dup)").length;
      var n = root.querySelector(".pk-left-n");
      if (n) n.textContent = left;
      var s1 = hasLocal && hasRes && !left, s2 = s1 && ready > 0 && need === 0;
      set(1, s2 ? "done" : "now");
      set("go", s2 ? "now" : "wait");
      filter();
      guide(left, hasLocal, hasRes, need, ready);
      root.classList.toggle("is-ready", s2);
      var c = root.querySelector(".pk-count");
      if (c) c.textContent = ready;
    }
    // ---------- йўлбошчи: битта аниқ кейинги қадам ва ўша жойга олиб борувчи тугма ----------
    function guide(left, hasLocal, hasRes, need, ready) {
      var box = root.querySelector("[data-now]");
      if (!box) return;
      var t, go = null, tone = "todo";
      if (left) {
        t = "<b>1-қадам.</b> Тизим " + left + " та листни сизга қолдирди. Ҳар бирини <b>«Кўриш»</b> билан очинг ва турини босинг: " +
            "<b class='t-loc'>Локал</b> — иш ҳажмлари, <b class='t-res'>Ресурс</b> — нархлар, <b>Ўтказиш</b> — керак эмас.";
        go = {text: "Биринчисига ўтиш ↓", el: root.querySelector(".pk-sheet.is-none")};
      } else if (!hasLocal) {
        t = "<b>Локал йўқ.</b> Иш ҳажмлари турган листни (смета) <b>«Локал»</b> деб белгиланг — пастдаги «ўтказилган листлар»да бўлиши мумкин.";
        go = {text: "Листларни очиш ↓", el: root.querySelector(".pk-skip"), open: true};
      } else if (!hasRes) {
        t = "<b>Ресурс йўқ.</b> Нархлар турган листни (RES) <b>«Ресурс»</b> деб белгиланг ёки RES файлни қўшиб юкланг.";
        go = {text: "Листларни очиш ↓", el: root.querySelector(".pk-skip"), open: true};
      } else if (need) {
        t = "<b>" + need + " та локал</b> учун тизим ресурсни топа олмади. Қизил қаторда <b>«Ресурсни танлаш»</b>ни босинг — " +
            "локал ва ресурслар ёнма-ён очилади, тўғрисини кўриб <b>«Шуни танлаш»</b>ни босасиз.";
        go = {text: "Ўша қаторга ўтиш ↓", el: root.querySelector(".pk-pair.is-need")};
      } else {
        tone = "ok";
        t = "<b>Ҳаммаси тайёр:</b> " + ready + " та смета нархланади. Жуфтликлар тўғрими — бир қараб чиқинг" +
            (D.embed === "1" || root.classList.contains("is-embed") ? ", кейин пастдаги <b>«Ҳаммасини бошлаш»</b>ни босинг." : ", кейин <b>«Бошлаш»</b>ни босинг.");
        var g = root.querySelector(".pk-go");
        if (g) go = {text: "«Бошлаш»га ўтиш ↓", el: g};
      }
      box.hidden = false;
      box.className = "pk-now is-" + tone;
      box.querySelector(".pk-now-ic").textContent = tone === "ok" ? "✓" : "→";
      box.querySelector(".pk-now-t").innerHTML = t;
      var b = box.querySelector(".pk-now-go");
      b.hidden = !(go && go.el);
      if (go && go.el) {
        b.textContent = go.text;
        b.onclick = function () {
          if (go.open) go.el.open = true;
          go.el.scrollIntoView({behavior: "smooth", block: "center"});
          go.el.classList.remove("is-flash"); void go.el.offsetWidth; go.el.classList.add("is-flash");
        };
      }
    }
    function set(step, state) {
      var el = root.querySelector('[data-step="' + step + '"]');
      if (!el) return;
      el.classList.remove("is-done", "is-now", "is-wait");
      el.classList.add("is-" + state);
      if (state === "wait") { delete revealed[step]; return; }  // яна очилганда — яна ёниб чиқади
      if (revealed[step]) return;
      revealed[step] = true;
      // кетма-кет ёниб очилади: биринчи чизилишда — навбатма-навбат, кейин — «Бошлаш» усулдан бироз кейин
      var order = {1: 0, go: 1}[step];
      el.style.animationDelay = (initial ? order * 0.15 : step === "go" ? 0.3 : 0) + "s";
      el.classList.remove("is-in"); void el.offsetWidth; el.classList.add("is-in");
      if (!initial && step === "go") {
        setTimeout(function () { el.scrollIntoView({behavior: "smooth", block: step === "go" ? "center" : "nearest"}); }, 400);
      }
    }
    var initial = !opts.revealed;

    // ---------- сузгич ва қидирув (кўп файл бўлса — керагини тез топиш) ----------
    var view = opts.view || {f: "", q: ""};
    var search = root.querySelector(".pk-search");
    if (search) {
      search.value = view.q;
      search.addEventListener("input", function () { view.q = search.value; filter(); });
    }
    root.querySelectorAll(".pk-filters button").forEach(function (b) {
      b.addEventListener("click", function () { view.f = view.f === b.dataset.f ? "" : b.dataset.f; filter(); });
    });
    function filter() {
      var q = (view.q || "").trim().toLowerCase(), shown = 0;
      root.querySelectorAll(".pk-filters button").forEach(function (b) { b.classList.toggle("is-on", b.dataset.f === view.f); });
      root.querySelectorAll(".pk-group [data-q]").forEach(function (row) {
        var ok = !view.f || (view.f === "need" ? row.classList.contains("is-need") : row.dataset.type === view.f);
        if (ok && q) ok = (row.dataset.q || "").indexOf(q) >= 0;
        row.hidden = !ok;
        if (ok) shown++;
      });
      var none = root.querySelector(".pk-nores");
      if (none) none.hidden = shown > 0;
    }

    // ---------- лист тури (Локал / Ресурс / Ўтказиш) ----------
    root.querySelectorAll(".pk-seg button").forEach(function (b) {
      b.addEventListener("click", function () {
        if (b.classList.contains("is-on") || b.disabled) return;
        if (!mine) { toast("Бу иш сизники эмас — фақат кўриш мумкин"); return; }
        var row = b.closest(".pk-sheet"), k = row.dataset.k;
        row.classList.add("is-busy");
        post(D.pick, form, {what: "role", key: k, role: b.dataset.role})
          .then(function (d) { if (opts.onSaved) opts.onSaved(d); return reload({flash: k}); })
          .then(function () { toast("✓ Сақланди — жуфтликлар янгиланди", true); })
          .catch(function (e) { row.classList.remove("is-busy"); toast(e.message); });
      });
    });

    // ---------- жуфтлик: ресурс танлови ва «нархлансин» белгиси ----------
    function savePair(row, redraw) {
      var sel = row.querySelector(".pk-select"), inc = row.querySelector("input[type=checkbox]");
      if (!mine) return;
      post(D.pick, form, {what: "pair", n: row.dataset.n, res: sel.value, include: inc.checked ? "1" : ""})
        .then(function (d) {
          if (opts.onSaved) opts.onSaved(d);
          toast("✓ Сақланди", true);
          if (redraw) return reload({flash: row.dataset.local});
        })
        .catch(function (e) { toast(e.message); });
    }
    pairs().forEach(function (row) {
      var sel = row.querySelector(".pk-select"), inc = row.querySelector("input[type=checkbox]");
      sel.addEventListener("change", function () { if (sel.value) inc.checked = true; update(); savePair(row, true); });
      inc.addEventListener("change", function () { update(); savePair(row); });
      row.querySelector(".pk-compare").addEventListener("click", function () { compare(row); });
    });

    // ---------- Excel кўриниши ----------
    var V = window.narxViewer;
    function cands(row) {
      return Array.prototype.slice.call(row.querySelector(".pk-select").options)
        .filter(function (o) { return o.value && !o.dataset.base; })
        .map(function (o) { return {k: o.value, ov: ovOf(o), same: !!o.dataset.same}; });
    }
    function pickFor(row) {
      var sel = row.querySelector(".pk-select");
      return {
        label: function (k, s) {
          if (!s || s.role !== "resource" || !Array.prototype.some.call(sel.options, function (o) { return o.value === k; })) return null;
          return sel.value === k ? {text: "✓ Шу ресурс танланган", chosen: true} : {text: "Шу ресурсни танлаш"};
        },
        choose: function (k) { sel.value = k; sel.dispatchEvent(new Event("change")); },
      };
    }
    function compare(row) {
      if (!V) return;
      var sel = row.querySelector(".pk-select"), c = cands(row), start = sel.value && !sel.options[sel.selectedIndex].dataset.base ? sel.value : (c[0] && c[0].k);
      var panes = [{url: D.sheet, k: row.dataset.local, tabs: V.fileTabs(SHEETS, row.dataset.local), markFor: window.narxMarkFor || null}];
      if (start) panes.push({url: D.sheet, k: start, tabs: V.candTabs(c, BY, row.dataset.local), pick: pickFor(row), markFor: window.narxMarkFor || null});
      V.open(row.dataset.title + (sel.value ? " — локал ва ресурс ёнма-ён" : " — ресурс танланмаган: ўнгдан кўриб танланг"), panes);
    }
    root.querySelectorAll("[data-view]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (!V) return;
        var k = b.dataset.view;
        V.open("Excel кўриниши — " + (BY[k] ? BY[k].file_name : ""), [{url: D.sheet, k: k, tabs: V.fileTabs(SHEETS, k), markFor: window.narxMarkFor || null}]);
      });
    });

    // ---------- усул ва фоизлар (эслаб қолинади) ----------
    root.querySelectorAll(".pk-mode input").forEach(function (inp) {
      inp.addEventListener("change", function () {
        root.querySelectorAll(".pk-mode").forEach(function (m) { m.classList.toggle("is-on", m.querySelector("input").checked); });
        // усул листларни ким белгилашини ўзгартиради — блок қайта чизилади
        post(D.nf, form, {nf_mode: inp.value}).then(function () { return reload(); })
          .then(function () { toast(inp.value === "auto" ? "✓ Автоматик: листларни тизим ажратди" : "✓ Қўлда: ҳар бир листни ўзингиз белгиланг", true); })
          .catch(function (e) { toast(e.message); });
      });
    });
    var pctBox = root.querySelector(".pk-pct");
    if (pctBox) pctBox.querySelectorAll("input").forEach(function (inp) {
      inp.addEventListener("change", function () {
        var data = {};
        pctBox.querySelectorAll("input").forEach(function (x) { data[x.name] = x.value; });
        post(D.pct, form, data).then(function () {
          var s = pctBox.querySelector(".pk-saved"); s.hidden = false; setTimeout(function () { s.hidden = true; }, 1500);
        }).catch(function (e) { toast("Фоизлар сақланмади: " + e.message); });
      });
    });

    // ---------- Бошлаш: аниқ прогресс ----------
    var go = root.querySelector(".pk-go"), prog = root.querySelector(".pk-prog");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!go) return;
      if (go.disabled) return;
      var old = root.querySelector(".pk-err"); if (old) old.remove();
      go.disabled = true;
      go.querySelector(".pk-go-t").textContent = "Нархланяпти…";
      prog.hidden = false;
      var bar = prog.querySelector(".pk-bar i"), txt = prog.querySelector(".pk-prog-t"), pc = prog.querySelector(".pk-prog-p");
      function show(p, t) { bar.style.width = p + "%"; pc.textContent = Math.round(p) + "%"; if (t) txt.textContent = t; }
      show(3, D.project ? "Навбатга қўйилмоқда…" : "Танловингиз сақланмоқда…");
      prog.scrollIntoView({behavior: "smooth", block: "center"});
      var finished = false, since = Date.now();
      function poll() {
        if (finished || D.project) return;
        fetch(D.progress, {cache: "no-store", credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (d) {
          if (finished) return;
          if (d.total) {
            // ҳар бир смета ичида ҳам секин силжийди (узун смета узоқ ўқилади)
            var inside = Math.min(0.9, (Date.now() - since) / 20000);
            show(Math.min(97, 5 + 92 * (d.at - 1 + inside) / d.total), d.at + " / " + d.total + " смета нархланяпти: " + d.text);
          }
          setTimeout(poll, 900);
        }).catch(function () { setTimeout(poll, 1500); });
      }
      var last = 0;
      setInterval(function () {  // янги смета бошланса — ичидаги вақт ҳисоби янгидан
        var m = /^(\d+) \//.exec(txt.textContent);
        if (m && +m[1] !== last) { last = +m[1]; since = Date.now(); }
      }, 300);
      setTimeout(poll, 600);
      var fd = new FormData(form);
      fetch(form.action, {method: "POST", body: fd, credentials: "same-origin", headers: {"X-Requested-With": "XMLHttpRequest"}})
        .then(function (r) { return r.json().catch(function () { return {ok: false, error: "сервер хатоси (" + r.status + ")"}; }); })
        .then(function (d) {
          finished = true;
          if (!d.ok) throw new Error(d.error || "хато");
          show(100, D.project ? "Навбатга қўйилди — папка саҳифаси очиляпти…" : "Тайёр! Натижа очиляпти…");
          bar.style.animation = "none";
          setTimeout(function () { location.href = d.url; }, 500);
        })
        .catch(function (err) {
          finished = true;
          go.disabled = false;
          go.querySelector(".pk-go-t").textContent = "Қайта уриниш";
          prog.hidden = true;
          var box = document.createElement("div");
          box.className = "pk-err";
          box.textContent = err.message || String(err);
          go.parentNode.appendChild(box);
        });
    });

    // ---------- қайта чизиш (лист тури ўзгарганда) ----------
    function reload(o) {
      return fetch(D.partial, {cache: "no-store", credentials: "same-origin"}).then(function (r) {
        if (!r.ok) throw new Error("Янгилаб бўлмади (" + r.status + ")");
        return r.text();
      }).then(function (html) {
        var y = window.scrollY, box = document.createElement("div");
        box.innerHTML = html;
        var fresh = box.firstElementChild;
        root.replaceWith(fresh);
        mount(fresh, {revealed: revealed, view: view, onSaved: opts.onSaved, onReload: opts.onReload});
        window.scrollTo(0, y);
        if (o && o.flash) {  // тури ўзгарган қатор ўз гуруҳига кўчади — кўриниб турсин
          var row = fresh.querySelector('.pk-sheet[data-k="' + CSS.escape(o.flash) + '"]');
          if (row) { row.classList.add("is-flash"); if (!row.hidden) row.scrollIntoView({behavior: "smooth", block: "nearest"}); }
        }
        if (opts.onReload) opts.onReload(fresh);
      });
    }

    update();
    initial = false;
    return {reload: reload};
  }

  window.narxPicker = {mount: mount};
})();
