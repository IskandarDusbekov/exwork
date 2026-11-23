/* Листларни белгилаш → жуфтлаш → усул → «Бошлаш» (jobs/_picker.html).
   window.narxPicker.mount(root) — юклаш саҳифасида (файл юклангач) ва объект саҳифасида бир хил.
   Ҳар бир танлов дарҳол серверга сақланади; лист тури ўзгарса — блок қайта чизилади (қайта жуфтланади). */
(function () {
  "use strict";

  // таржима (web/i18n.py, /jsi18n/): каталог юкланмаган бўлса — асл матн
  var gettext = window.gettext || function (s) { return s; };
  function fmt(s, args) { var i = 0; return s.replace(/%s/g, function () { return args[i++]; }); }

  function csrf(form) { return form.querySelector("[name=csrfmiddlewaretoken]").value; }
  // act — {text, fn}: хабар ичидаги тугма (масалан «Ортга қайтариш»); бундай хабар узоқроқ туради
  function toast(text, ok, act) {
    document.querySelectorAll(".pk-toast.has-act").forEach(function (x) { x.remove(); });
    var t = document.createElement("div");
    t.className = "pk-toast" + (ok ? " is-ok" : "") + (act ? " has-act" : "");
    var span = document.createElement("span");
    span.textContent = text;
    t.appendChild(span);
    if (act) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "pk-toast-act"; b.textContent = act.text;
      b.addEventListener("click", function () { t.remove(); act.fn(); });
      t.appendChild(b);
    }
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, act ? 8000 : !ok ? 4500 : text.length > 60 ? 6000 : 1600);  // узун хабар — ўқишга улгурсин
  }
  function post(url, form, data) {
    var fd = new FormData();
    Object.keys(data).forEach(function (k) {
      [].concat(data[k]).forEach(function (v) { fd.append(k, v); });  // рўйхат — бир нечта қиймат (key=…&key=…)
    });
    return fetch(url, {method: "POST", body: fd, credentials: "same-origin",
                       headers: {"X-CSRFToken": csrf(form), "X-Requested-With": "XMLHttpRequest"}})
      .then(function (r) {
        return r.json().catch(function () { return {ok: false, error: fmt(gettext("сервер хатоси (%s)"), [r.status])}; })
          .then(function (d) { if (!r.ok || !d.ok) throw new Error(d.error || gettext("хато")); return d; });
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
    // жуфтлик остидаги битта сатр — оддий тилда: тўғрими, нима қилиш керак
    function say(row, on, val, opt, v) {
      var el = row.querySelector(".pk-say");
      if (!el) return;
      var t, c;
      if (!on) { t = gettext("Бу локал нархланмайди. Нархлаш керак бўлса — чапдаги белгини қўйинг."); c = "t-off"; }
      else if (!val) { t = gettext("Ресурс файли танланмаган — ўнгдаги рўйхатдан танланг ёки «Ресурсни танлаш»ни босиб ёнма-ён кўринг."); c = "t-bad"; }
      else if (opt && opt.dataset.np) { t = gettext("Танланган ресурсда нарх йўқ — бошқа ресурс файлини танланг, акс ҳолда нархланмай қолади."); c = "t-bad"; }
      else if (opt && opt.dataset.base) { t = gettext("Нархлар ресурс базасидан олинади — тайёр, ҳеч нарса қилиш шарт эмас."); c = "t-ok"; }
      else if (opt && opt.dataset.struct) {
        // номлар мос эмас (смета­чи RES'да тавсифни ўзгартирган), лекин битта файл, лист номи ва смета рақами бир хил
        t = gettext("Номлар ресурсда бошқача ёзилган, лекин бу локалнинг ўзиники: битта файлда, лист номи ва смета рақами бир хил. Нархлар шифр бўйича таклиф қилинади — нархлангандан кейин «ТЕКШИРИНГ» қаторларини тасдиқланг.");
        c = "t-mid";
      }
      else if (v == null || v >= 0.7) { t = gettext("Тўғри жуфтланган — ҳеч нарса қилиш шарт эмас."); c = "t-ok"; }
      else if (v >= 0.4) { t = gettext("Мослик ўртача — «Ёнма-ён кўриш» билан текшириб кўринг; нотўғри бўлса бошқасини танланг."); c = "t-mid"; }
      else { t = gettext("Мослик паст — бу ресурс бошқа объектники бўлиши мумкин. «Ёнма-ён кўриш» билан текширинг."); c = "t-low"; }
      el.textContent = t;
      el.className = "pk-say " + c;
    }
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
        if (opt && opt.dataset.base) { ov.textContent = gettext("база"); ov.className = "pk-ov t-none"; }
        else if (!sel.value) { ov.textContent = "?"; ov.className = "pk-ov t-low"; }
        else if (opt && opt.dataset.struct) { ov.textContent = gettext("№ мос"); ov.className = "pk-ov t-mid"; }
        else { ov.textContent = v == null ? "—" : Math.round(v * 100) + "%"; ov.className = "pk-ov " + tone(v); }
        ov.title = opt && opt.dataset.struct
          ? fmt(gettext("Номлар мослиги %s% — лекин файл, лист номи ва смета рақами бир хил"), [Math.round((v || 0) * 100)])
          : gettext("Локалдаги ресурсларнинг неча фоизи шу RES листида аниқ бор");
        say(row, inc.checked, sel.value, opt, v);
        if (inc.checked && sel.value) ready++;
        if (inc.checked && !sel.value) need++;
        if (inc.checked && sel.value) (used[sel.value] = used[sel.value] || []).push(row.dataset.tag || row.dataset.title);
      });
      // ресурс қаторида — қайси локаллар шу ердан нарх олади
      root.querySelectorAll(".pk-used").forEach(function (u) {
        var t = used[u.dataset.res];
        u.textContent = t ? fmt(gettext("нарх беради: %s"), [t.join(", ")]) : gettext("ҳеч қайси локалга танланмаган");
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
        t = fmt(gettext("<b>1-қадам.</b> %s та лист белгиланмаган. Ҳар бирини <b>«Кўриш»</b> билан очинг ва турини босинг: <b class='t-loc'>Локал</b> — иш ҳажмлари, <b class='t-res'>Ресурс</b> — нархлар, <b>Ўтказиш</b> — керак эмас."), [left]) + " " +
            (root.querySelector(".pk-auto")
              ? gettext("Бир нечтасини чапдаги катакча билан танлаб, бирданига белгилаш мумкин; ёки тепадаги <b>«Автоматик ажратиш»</b>ни босинг — тизим ўзи ажратади.")
              : gettext("Бир нечтасини чапдаги катакча билан танлаб, бирданига белгилаш мумкин."));
        go = {text: gettext("Биринчисига ўтиш ↓"), el: root.querySelector(".pk-sheet.is-none")};
      } else if (!hasLocal) {
        t = gettext("<b>Нархланадиган смета йўқ.</b> Иш ҳажмлари турган листни <b>«Локал»</b>, нархлар листини <b>«Ресурс»</b> деб белгиланг — пастдаги «Керак эмас» ичида бўлиши мумкин.");
        go = {text: gettext("Листларни очиш ↓"), el: root.querySelector(".pk-skip"), open: true};
      } else if (!hasRes) {
        t = gettext("<b>Ресурс йўқ.</b> Нархлар турган листни (RES) <b>«Ресурс»</b> деб белгиланг ёки RES файлни қўшиб юкланг.");
        go = {text: gettext("Листларни очиш ↓"), el: root.querySelector(".pk-skip"), open: true};
      } else if (need) {
        t = fmt(gettext("<b>%s та локал</b> учун тизим ресурсни топа олмади. Қизил қаторда <b>«Ресурсни танлаш»</b>ни босинг — локал ва ресурслар ёнма-ён очилади, тўғрисини кўриб <b>«Шуни танлаш»</b>ни босасиз."), [need]);
        go = {text: gettext("Ўша қаторга ўтиш ↓"), el: root.querySelector(".pk-pair.is-need")};
      } else {
        // ҳаммаси тайёр — ортиқча матн кўрсатмаймиз, пастдаги яшил «Бошлаш» тугмасининг ўзи кифоя
        box.hidden = true;
        box.className = "pk-now";
        return;
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

    // ---------- лист тури (Локал / Ресурс / Ўтказиш): битта, бир нечта, автоматик, ортга қайтариш ----------
    var busy = false;
    // data — batch_pick'га (what: role | auto | undo); rows — кутиш белгиси қўйиладиган қаторлар
    function change(data, rows, done) {
      if (!mine) { toast(gettext("Бу иш сизники эмас — фақат кўриш мумкин")); return; }
      if (busy) return;
      busy = true;
      root.classList.add("is-working");
      (rows || []).forEach(function (r) { r.classList.add("is-busy"); });
      var keys = [].concat(data.key || []), got = null;
      post(D.pick, form, data)
        .then(function (d) { got = d; if (opts.onSaved) opts.onSaved(d); return reload({flash: keys.length === 1 ? keys[0] : "", sel: {}}); })
        .then(function () {
          busy = false;
          // ҳар бир ўзгаришдан кейин — бир босишда қайтариш (адашиб босилса)
          toast(done(got), true, got.undo && data.what !== "undo" ? {text: gettext("↶ Ортга қайтариш"), fn: undo} : null);
        })
        .catch(function (e) {
          busy = false;
          root.classList.remove("is-working");
          (rows || []).forEach(function (r) { r.classList.remove("is-busy"); });
          toast(e.message);
        });
    }
    function setRoles(keys, role, rows) {
      if (!keys.length) return;
      var name = {local: gettext("Локал"), resource: gettext("Ресурс"), unknown: gettext("Керак эмас")}[role] || "";
      change({what: "role", key: keys, role: role}, rows, function (d) {
        // тизим шу листнинг тузилишини эслаб қолди — смета­чига айтамиз (кейин қайта кўрсатиши шарт эмас)
        if (d.remembered) return "✓ " + d.remembered;
        if (keys.length > 1) return name ? fmt(gettext("✓ %s та лист — «%s»"), [keys.length, name]) : fmt(gettext("✓ %s та лист — белгиси олинди"), [keys.length]);
        return role ? gettext("✓ Сақланди — жуфтликлар янгиланди") : gettext("✓ Белги олинди");
      });
    }
    function undo() {
      change({what: "undo"}, [], function () { return gettext("✓ Охирги ўзгариш бекор қилинди"); });
    }
    root.querySelectorAll(".pk-seg button").forEach(function (b) {
      b.addEventListener("click", function () {
        if (b.classList.contains("is-on") || b.disabled) return;
        var row = b.closest(".pk-sheet");
        setRoles([row.dataset.k], b.dataset.role, [row]);
      });
    });
    root.querySelectorAll(".pk-take").forEach(function (b) {  // тизим таклифини қабул қилиш
      b.addEventListener("click", function () { var row = b.closest(".pk-sheet"); setRoles([row.dataset.k], b.dataset.role, [row]); });
    });
    root.querySelectorAll(".pk-unpick").forEach(function (b) {
      b.addEventListener("click", function () { setRoles([b.dataset.k], "", [b.closest(".pk-sheet")]); });
    });
    root.querySelectorAll(".pk-dropped-back").forEach(function (b) {
      b.addEventListener("click", function () {
        var rows = Array.prototype.slice.call(b.closest(".pk-group").querySelectorAll(".pk-sheet[data-k]"));
        setRoles(rows.map(function (r) { return r.dataset.k; }), "", rows);
      });
    });
    var undoBtn = root.querySelector(".pk-undo");
    if (undoBtn) undoBtn.addEventListener("click", undo);
    var autoBtn = root.querySelector(".pk-auto");
    if (autoBtn) autoBtn.addEventListener("click", function () {
      autoBtn.querySelector("span").textContent = gettext("Ажратилмоқда…");
      change({what: "auto"}, [], function (d) {
        return d.left ? fmt(gettext("✓ Тизим листларни ажратди — текширинг. Нотўғриси бўлса, турини босиб ўзгартиринг (%s та қолди)"), [d.left])
                      : gettext("✓ Тизим листларни ажратди — текширинг. Нотўғриси бўлса, турини босиб ўзгартиринг");
      });
    });

    // ---------- бир нечта листни танлаш (катакча) ва пастки панел ----------
    var sel = opts.sel || {};  // калит → true; қайта чизилганда сақланади
    var bulk = root.querySelector(".pk-bulk");
    function picks() { return Array.prototype.slice.call(root.querySelectorAll(".pk-pick-in")); }
    function visible(box) {
      return box ? Array.prototype.filter.call(box.querySelectorAll(".pk-pick-in"), function (i) { return !i.closest("[hidden]"); }) : [];
    }
    function drawBulk() {
      var on = picks().filter(function (i) { return i.checked; });
      picks().forEach(function (i) { i.closest(".pk-sheet").classList.toggle("is-picked", i.checked); });
      root.querySelectorAll(".pk-gsel-in").forEach(function (g) {
        var all = visible(g.closest(".pk-group, .pk-skip"));
        var n = all.filter(function (i) { return i.checked; }).length;
        g.checked = all.length > 0 && n === all.length;
        g.indeterminate = n > 0 && n < all.length;
      });
      if (!bulk) return;
      bulk.hidden = !on.length;
      bulk.querySelector(".pk-bulk-n b").textContent = on.length;
    }
    picks().forEach(function (i) {
      if (sel[i.value]) i.checked = true;
      i.addEventListener("change", function () { if (i.checked) sel[i.value] = true; else delete sel[i.value]; drawBulk(); });
    });
    root.querySelectorAll(".pk-gsel-in").forEach(function (g) {
      g.closest("label").addEventListener("click", function (e) { e.stopPropagation(); });  // <summary> ичида — очилиб-ёпилмасин
      g.addEventListener("change", function () {
        visible(g.closest(".pk-group, .pk-skip")).forEach(function (i) {  // қидирувда яширинганлар танланмайди
          i.checked = g.checked;
          if (g.checked) sel[i.value] = true; else delete sel[i.value];
        });
        drawBulk();
      });
    });
    if (bulk) {
      bulk.querySelectorAll("[data-bulk]").forEach(function (b) {
        b.addEventListener("click", function () {
          var on = picks().filter(function (i) { return i.checked; });
          setRoles(on.map(function (i) { return i.value; }), b.dataset.bulk, on.map(function (i) { return i.closest(".pk-sheet"); }));
        });
      });
      bulk.querySelector(".pk-bulk-x").addEventListener("click", function () {
        picks().forEach(function (i) { i.checked = false; });
        Object.keys(sel).forEach(function (k) { delete sel[k]; });
        drawBulk();
      });
    }
    drawBulk();

    // ---------- жуфтлик: ресурс танлови ва «нархлансин» белгиси ----------
    function savePair(row, redraw) {
      var sel = row.querySelector(".pk-select"), inc = row.querySelector("input[type=checkbox]");
      if (!mine) return;
      post(D.pick, form, {what: "pair", n: row.dataset.n, res: sel.value, include: inc.checked ? "1" : ""})
        .then(function (d) {
          if (opts.onSaved) opts.onSaved(d);
          toast(gettext("✓ Сақланди"), true);
          if (redraw) return reload({flash: row.dataset.local});
        })
        .catch(function (e) { toast(e.message); });
    }
    // ---------- ресурс танлаш рўйхати: <select> ўрнига тушунарли рўйхат (файл, лист, мослик, огоҳлантириш) ----------
    function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
    var ICO_SHEET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" class="h-5 w-5"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>';
    var ICO_DOWN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="h-4 w-4"><path d="M6 9l6 6 6-6"/></svg>';
    function word(v) { return v == null ? gettext("мослик номаълум") : v >= 0.7 ? gettext("жуда мос") : v >= 0.4 ? gettext("ўртача мос") : v > 0 ? gettext("кам мос") : gettext("мос эмас"); }
    function ddItem(o, chosen) {
      var v = ovOf(o), d = o.dataset, tags = [];
      if (d.best) tags.push('<em class="is-best">' + gettext("★ энг мос") + "</em>");
      if (d.same) tags.push("<em>" + gettext("локалнинг ўз файлида") + "</em>");
      if (d.struct) tags.push('<em class="is-best">' + gettext("лист номи ва № бир хил") + "</em>");
      if (d.np) tags.push('<em class="is-np">' + gettext("нархи йўқ — танламанг") + "</em>");
      if (chosen) tags.push('<em class="is-on">' + gettext("✓ ҳозир танланган") + "</em>");
      if (d.base) {
        return '<button type="button" class="pk-dd-i' + (chosen ? " is-on" : "") + '" data-v="' + esc(o.value) + '">' +
          '<span class="pk-dd-ic is-base">' + gettext("Б") + '</span><span class="pk-dd-t"><b>' + gettext("Ресурс базасидан") + "</b><small>" + gettext("RES файлсиз — нархлар тизимдаги базадан олинади") + "</small>" +  // i18n: ok — gettext ичида
          (tags.length ? '<span class="pk-dd-tags">' + tags.join("") + "</span>" : "") + "</span><span></span></button>";
      }
      var pc = v == null ? "—" : Math.round(v * 100) + "%";
      return '<button type="button" class="pk-dd-i' + (chosen ? " is-on" : "") + (d.np ? " is-np" : "") + '" data-v="' + esc(o.value) + '" data-q="' +
          esc((d.file + " " + d.title + " " + d.sheet + " " + d.tag).toLowerCase()) + '">' +
        '<span class="pk-dd-ic">' + ICO_SHEET + "<i>" + esc(d.tag || gettext("Р")) + "</i></span>" +
        '<span class="pk-dd-t"><b>' + esc(d.file) + "</b><small>" + esc(d.title) + " · " + fmt(gettext("лист «%s»"), [esc(d.sheet)]) + (d.rows && d.rows !== "0" ? " · " + fmt(gettext("%s қатор"), [esc(d.rows)]) : "") + "</small>" +
          (tags.length ? '<span class="pk-dd-tags">' + tags.join("") + "</span>" : "") + "</span>" +
        '<span class="pk-dd-m ' + tone(v) + '"><b>' + pc + '</b><i><s style="width:' + (v == null ? 0 : Math.round(v * 100)) + '%"></s></i><small>' + word(v) + "</small></span></button>";
    }
    function buildDD(row) {
      var sel = row.querySelector(".pk-select");
      if (!sel || sel.dataset.dd) return;
      sel.dataset.dd = "1";
      sel.classList.add("pk-sel-hid");
      sel.tabIndex = -1;
      var opts = Array.prototype.filter.call(sel.options, function (o) { return o.value; });
      var wrap = document.createElement("div"), btn = document.createElement("button"), pop = document.createElement("div");
      wrap.className = "pk-dd";
      btn.type = "button"; btn.className = "pk-dd-btn" + (sel.value ? "" : " is-main");
      btn.innerHTML = "<span>" + (sel.value ? gettext("Ресурсни алмаштириш") : gettext("Ресурс файлини танлаш")) + '</span><i class="pk-dd-n">' + fmt(gettext("%s та"), [opts.length]) + "</i>" + ICO_DOWN;
      btn.title = gettext("Бу локал учун нархлар олинадиган файлни танлаш");
      pop.className = "pk-dd-pop"; pop.hidden = true;
      wrap.appendChild(btn); wrap.appendChild(pop);
      sel.parentNode.insertBefore(wrap, sel.nextSibling);
      function draw() {
        var real = opts.filter(function (o) { return !o.dataset.base; }), base = opts.filter(function (o) { return o.dataset.base; });
        var top = real.filter(function (o) { return o.dataset.best || o.dataset.same; });
        var rest = real.filter(function (o) { return top.indexOf(o) < 0; }).sort(function (a, b) {
          return (!!a.dataset.np - !!b.dataset.np) || ((ovOf(b) || 0) - (ovOf(a) || 0));
        });
        var h = '<div class="pk-dd-h"><b>' + gettext("Бу локалнинг нархлари қайси файлда?") + "</b>" +
                "<small>" + gettext("Фоиз — локалдаги ресурсларнинг неча фоизи шу файлда бор. Қанча юқори бўлса, шунча тўғри.") + "</small></div>";
        if (real.length > 6) h += '<input type="search" class="pk-dd-q" placeholder="' + esc(gettext("Файл ёки бўлим номи бўйича қидириш…")) + '">';  // i18n: ok — gettext ичида
        h += '<div class="pk-dd-list">';
        if (top.length) h += '<div class="pk-dd-g">' + gettext("Тавсия этилади") + "</div>" + top.map(function (o) { return ddItem(o, o.value === sel.value); }).join("");
        if (rest.length) h += '<div class="pk-dd-g">' + gettext("Бошқа ресурс файллари") + "</div>" + rest.map(function (o) { return ddItem(o, o.value === sel.value); }).join("");
        if (base.length) h += '<div class="pk-dd-g">' + gettext("Файлсиз") + "</div>" + base.map(function (o) { return ddItem(o, o.value === sel.value); }).join("");
        h += '<div class="pk-dd-none" hidden>' + gettext("Топилмади") + "</div></div>";
        pop.innerHTML = h;
        var q = pop.querySelector(".pk-dd-q");
        if (q) {
          q.addEventListener("input", function () {
            var t = q.value.trim().toLowerCase(), any = false;
            pop.querySelectorAll(".pk-dd-i[data-q]").forEach(function (b) { var ok = !t || b.dataset.q.indexOf(t) >= 0; b.hidden = !ok; any = any || ok; });
            pop.querySelectorAll(".pk-dd-g").forEach(function (g) { g.hidden = !!t; });
            pop.querySelector(".pk-dd-none").hidden = any || !t;
          });
          setTimeout(function () { q.focus(); }, 0);
        }
      }
      function close() { pop.hidden = true; wrap.classList.remove("is-open"); }
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (!pop.hidden) { close(); return; }
        document.querySelectorAll(".pk-dd.is-open").forEach(function (w) { w.classList.remove("is-open"); w.querySelector(".pk-dd-pop").hidden = true; });
        draw(); pop.hidden = false; wrap.classList.add("is-open");
      });
      pop.addEventListener("click", function (e) {
        e.stopPropagation();
        var it = e.target.closest(".pk-dd-i");
        if (!it) return;
        close();
        if (it.dataset.v === sel.value) return;
        if (it.classList.contains("is-np") && !confirm(gettext("Бу ресурс листида нарх йўқ — бу локал нархланмай қолади. Барибир танлансинми?"))) return;
        sel.value = it.dataset.v;
        sel.dispatchEvent(new Event("change"));
      });
      document.addEventListener("click", function () { if (!pop.hidden) close(); });
      document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !pop.hidden) close(); });
    }

    pairs().forEach(function (row) {
      var sel = row.querySelector(".pk-select"), inc = row.querySelector("input[type=checkbox]");
      buildDD(row);
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
          return sel.value === k ? {text: gettext("✓ Шу ресурс танланган"), chosen: true} : {text: gettext("Шу ресурсни танлаш")};
        },
        choose: function (k) { sel.value = k; sel.dispatchEvent(new Event("change")); },
      };
    }
    function compare(row) {
      if (!V) return;
      var sel = row.querySelector(".pk-select"), c = cands(row), start = sel.value && !sel.options[sel.selectedIndex].dataset.base ? sel.value : (c[0] && c[0].k);
      var panes = [{url: D.sheet, k: row.dataset.local, tabs: V.fileTabs(SHEETS, row.dataset.local), markFor: window.narxMarkFor || null}];
      if (start) panes.push({url: D.sheet, k: start, tabs: V.candTabs(c, BY, row.dataset.local), pick: pickFor(row), markFor: window.narxMarkFor || null});
      V.open(fmt(sel.value ? gettext("%s — локал ва ресурс ёнма-ён") : gettext("%s — ресурс танланмаган: ўнгдан кўриб танланг"), [row.dataset.title]), panes);
    }
    root.querySelectorAll("[data-view]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (!V) return;
        var k = b.dataset.view;
        V.open(fmt(gettext("Excel кўриниши — %s"), [BY[k] ? BY[k].file_name : ""]), [{url: D.sheet, k: k, tabs: V.fileTabs(SHEETS, k), markFor: window.narxMarkFor || null}]);
      });
    });

    // ---------- усул ва фоизлар (эслаб қолинади) ----------
    root.querySelectorAll(".pk-mode input").forEach(function (inp) {
      inp.addEventListener("change", function () {
        root.querySelectorAll(".pk-mode").forEach(function (m) { m.classList.toggle("is-on", m.querySelector("input").checked); });
        // усул листларни ким белгилашини ўзгартиради — блок қайта чизилади
        post(D.nf, form, {nf_mode: inp.value, batch: D.batch || ""}).then(function () { return reload(); })
          .then(function () { toast(inp.value === "auto" ? gettext("✓ Автоматик: листларни тизим ажратди") : gettext("✓ Қўлда: ҳар бир листни ўзингиз белгиланг"), true); })
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
        }).catch(function (e) { toast(fmt(gettext("Фоизлар сақланмади: %s"), [e.message])); });
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
      go.querySelector(".pk-go-t").textContent = gettext("Нархланяпти…");
      prog.hidden = false;
      var bar = prog.querySelector(".pk-bar i"), txt = prog.querySelector(".pk-prog-t"), pc = prog.querySelector(".pk-prog-p");
      function show(p, t) { bar.style.width = p + "%"; pc.textContent = Math.round(p) + "%"; if (t) txt.textContent = t; }
      show(3, D.project ? gettext("Навбатга қўйилмоқда…") : gettext("Танловингиз сақланмоқда…"));
      prog.scrollIntoView({behavior: "smooth", block: "center"});
      var finished = false, queued = false;
      // смета ичидаги босқичлар (сервер айтади) ва уларнинг улуши: ўқиш, қаторлар (сони билан), ёзиш, сақлаш.
      // Босқич ичида сони йўқ бўлса — вақт ўтгани сари секинлашиб силжийди, лекин кейинги босқич чегарасидан ўтмайди
      var PH = {read: [0, .38], price: [.38, .46], write: [.46, .8], save: [.8, .96], auto: [.96, 1]};
      var phKey = "", phSince = Date.now(), lastAt = 0;
      function num(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " "); }
      function poll() {
        if (finished || D.project) return;
        fetch(D.progress, {cache: "no-store", credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (d) {
          if (finished) return;
          if (queued && d.finished) {  // фондаги нархлаш тугади
            finished = true;
            if (d.error) return fail(new Error(d.error));
            show(100, gettext("Тайёр! Натижа очиляпти…"));
            bar.style.animation = "none";
            setTimeout(function () { location.href = d.url; }, 400);
            return;
          }
          if (d.queued && !d.total) show(4, d.ahead ? fmt(gettext("Навбатда: олдингизда %s та объект…"), [d.ahead]) : gettext("Навбатда — бошланяпти…"));
          if (d.total) {
            var key = d.at + ":" + (d.phase || "");
            if (key !== phKey) { phKey = key; phSince = Date.now(); }
            var span = PH[d.phase] || [0, .38], inside;
            var counted = (d.phase === "price" || d.phase === "write") && d.rows && d.rows_done;
            if (counted) inside = d.rows_done / d.rows;
            else inside = 0.9 * (1 - Math.exp(-(Date.now() - phSince) / 8000));
            var job = span[0] + (span[1] - span[0]) * inside;
            var p = 5 + 92 * (d.at - 1 + job) / d.total;
            lastAt = Math.max(lastAt, p);  // орқага қайтмайди
            // d.text — сервердаги асл ёзув («смета нархланяпти: <номи>»); олди олиб ташланади, қолгани — смета номи
            var t = fmt(gettext("%s / %s смета: %s"), [d.at, d.total, d.text.replace(/^смета нархланяпти:\s*/, "")]);  // i18n: ok
            var what = {read: gettext("Excel ўқилмоқда"), price: gettext("қаторлар нархланмоқда"), write: gettext("Excel'га ёзилмоқда"),
                        save: gettext("файл сақланмоқда"), auto: gettext("таклифлар қўйилмоқда")}[d.phase];
            if (what) {
              t += " — " + what;
              if (counted) t += ": " + fmt(gettext("%s / %s қатор, қолди %s"), [num(d.rows_done), num(d.rows), num(d.rows - d.rows_done)]);
              else if (d.rows) t += " " + fmt(gettext("(%s қатор)"), [num(d.rows)]);
            }
            show(Math.min(99, lastAt), t);
          }
          setTimeout(poll, 1000);
        }).catch(function () { setTimeout(poll, 2000); });
      }
      setTimeout(poll, 600);
      var fd = new FormData(form);
      fetch(form.action, {method: "POST", body: fd, credentials: "same-origin", headers: {"X-Requested-With": "XMLHttpRequest"}})
        .then(function (r) { return r.json().catch(function () { return {ok: false, error: fmt(gettext("сервер хатоси (%s)"), [r.status])}; }); })
        .then(function (d) {
          if (!d.ok) throw new Error(d.error || gettext("хато"));
          if (d.queued) { queued = true; return; }  // фонда нархланяпти — poll тугашини кутади
          finished = true;
          show(100, D.project ? gettext("Навбатга қўйилди — папка саҳифаси очиляпти…") : gettext("Тайёр! Натижа очиляпти…"));
          bar.style.animation = "none";
          setTimeout(function () { location.href = d.url; }, 500);
        })
        .catch(fail);
      function fail(err) {
          finished = true;
          go.disabled = false;
          go.querySelector(".pk-go-t").textContent = gettext("Қайта уриниш");
          prog.hidden = true;
          var box = document.createElement("div");
          box.className = "pk-err";
          box.textContent = err.message || String(err);
          go.parentNode.appendChild(box);
      }
    });

    // саҳифа нархлаш пайтида қайта очилди — ўша жараённи кўрсатади (қайта навбатга қўйилмайди)
    if (D.running === "1" && go) form.dispatchEvent(new Event("submit", {cancelable: true}));

    // ---------- қайта чизиш (лист тури ўзгарганда) ----------
    function reload(o) {
      return fetch(D.partial, {cache: "no-store", credentials: "same-origin"}).then(function (r) {
        if (!r.ok) throw new Error(fmt(gettext("Янгилаб бўлмади (%s)"), [r.status]));
        return r.text();
      }).then(function (html) {
        var y = window.scrollY, box = document.createElement("div");
        box.innerHTML = html;
        var fresh = box.firstElementChild;
        root.replaceWith(fresh);
        mount(fresh, {revealed: revealed, view: view, sel: o && o.sel ? o.sel : sel, onSaved: opts.onSaved, onReload: opts.onReload});
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

/* «Бошлаш»дан олдин сводка фоизлари бир марта кўрсатилади (транспорт, прочие, страхование, НДС):
   улар йиғиқ турганда кўринмай қолмасин. Ойнанинг ўзида катаклар — шу ерда тўғрилаб «Тўғри, бошлаш» босилади:
   қийматлар формадаги катакларга ёзилади (сақланади) ва нархлаш бошланади. Листлар (_picker.html) ва папка
   («Ҳаммасини бошлаш») — иккаласида ҳам. */
(function () {
  if (window.narxPctCheck) return;
  window.narxPctCheck = true;
  var gettext = window.gettext || function (s) { return s; };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function norm(v) { var n = parseFloat(String(v || "").replace("%", "").replace(/\s/g, "").replace(",", ".")); return isNaN(n) ? 0 : n; }
  // ойнадаги катаклар: ҳар бир фоиз — номи, катак, стандартдан фарқ қилса — сариқ ва «стандарт: …»
  function editor(box) {
    var wrap = el("div", "pctd"), pairs = [];
    Array.prototype.forEach.call(box.querySelectorAll("label"), function (l) {
      var name = l.querySelector("span[title]"), src = l.querySelector("input");
      if (!name || !src) return;
      var def = (src.title || "").replace(/^[^:]*:\s*/, "");  // «Стандарт: 3» (ҳар тилда) — қиймат
      var row = el("label", "pctd-row"), inp = el("input", "pctd-in num"), std = el("small", "pctd-std");
      row.appendChild(el("span", "pctd-n", name.textContent.trim()));
      var cell = el("span", "pctd-c");
      inp.type = "text"; inp.inputMode = "decimal"; inp.autocomplete = "off"; inp.value = src.value;
      cell.appendChild(inp); cell.appendChild(el("i", "", "%"));
      row.appendChild(cell);
      row.appendChild(std);
      function mark() {
        var changed = def && norm(inp.value) !== norm(def);
        row.classList.toggle("is-changed", !!changed);
        std.textContent = changed ? gettext("стандарт:") + " " + def : "";
      }
      inp.addEventListener("input", mark);
      inp.addEventListener("focus", function () { inp.select(); });
      mark();
      wrap.appendChild(row);
      pairs.push([inp, src]);
    });
    wrap.appendChild(el("p", "pctd-note", gettext("Нархлашда сводка шу фоизлар билан ҳисобланади. Кейин смета саҳифасида ҳам ўзгартириш мумкин.")));
    return {node: wrap, pairs: pairs};
  }
  document.addEventListener("submit", function (e) {
    var form = e.target, box = form.querySelector && form.querySelector(".pk-pct");
    if (!box || form.dataset.pctOk === "1" || !window.narxDialog) return;
    var ed = editor(box);
    if (!ed.pairs.length) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var btn = e.submitter;
    window.narxDialog.confirm("", {title: gettext("Сводка фоизлари"), ok: gettext("Тўғри, бошлаш"), cancel: gettext("Бекор қилиш"), body: ed.node}).then(function (yes) {
      if (!yes) return;
      var changed = false;
      ed.pairs.forEach(function (p) {
        var v = p[0].value.trim() || "0";
        if (v !== p[1].value) { p[1].value = v; changed = true; }
      });
      // листлар саҳифасида катак ўзгариши ўзи сақланади (change); папкада — форма билан бирга юборилади
      if (changed && ed.pairs.length) ed.pairs[0][1].dispatchEvent(new Event("change", {bubbles: true}));
      form.dataset.pctOk = "1";  // шу формада бир марта сўралади
      if (form.requestSubmit) form.requestSubmit(btn || undefined); else form.submit();
    });
  }, true);
})();
