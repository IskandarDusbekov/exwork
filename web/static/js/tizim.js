/* Панел → «Тизим»: жонли бошқарув харитаси, жараёнлар ва кодни ўқиш.
   Маълумот: panel/tizim_lessons.py (LESSONS, NODES — json_script), panel:tizim_live (ҳар 3 с), panel:tizim_code. */
(function () {
  "use strict";
  var root = document.getElementById("tz");
  var LESSONS = JSON.parse(document.getElementById("tz-lessons").textContent);
  var NODES = JSON.parse(document.getElementById("tz-nodes").textContent);
  var D = root.dataset, NS = "http://www.w3.org/2000/svg";
  var CODE_DIRS = ["core", "jobs", "panel", "accounts", "resbase", "narxbaza", "texnazor", "support", "news", "web"];
  var $ = function (id) { return document.getElementById(id); };

  // ---------- ёрдамчилар ----------
  function load(k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } }
  function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* йўқ */ } }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function fmt(n) { return n == null ? "—" : String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " "); }
  function bytes(n) {
    if (n == null) return "—";
    var u = ["Б", "КБ", "МБ", "ГБ", "ТБ"], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)) + " " + u[i];
  }
  function dur(s) {
    if (s == null) return "—";
    s = Math.round(s);
    if (s < 60) return s + " с";
    if (s < 3600) return Math.floor(s / 60) + " дақ " + (s % 60) + " с";
    if (s < 86400) return Math.floor(s / 3600) + " соат " + Math.floor(s % 3600 / 60) + " дақ";
    return Math.floor(s / 86400) + " кун " + Math.floor(s % 86400 / 3600) + " соат";
  }
  function ms(v) { return v == null ? "—" : v >= 1000 ? (v / 1000).toFixed(1) + " с" : v + " мс"; }
  function msCls(v) { return v >= 1500 ? "bad" : v >= 500 ? "warn" : ""; }
  function hhmm(t) { var d = new Date(t * 1000); return d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"}); }
  function table(rows) {
    return '<table class="tz-kv">' + rows.map(function (r) {
      return "<tr><td>" + r[0] + '</td><td class="' + (r[2] || "") + '">' + r[1] + "</td></tr>";
    }).join("") + "</table>";
  }
  function viewRef(dotted) {  // «jobs.views.batch_confirm» → {f: jobs/views.py, fn: batch_confirm}
    if (!dotted) return null;
    var p = dotted.split(".");
    if (CODE_DIRS.indexOf(p[0]) < 0 || p.length < 3) return null;
    var fn = p.pop(), cls = null;
    if (/^[A-Z]/.test(p[p.length - 1])) { cls = p.pop(); fn = cls + "." + fn; }
    return {f: p.join("/") + ".py", fn: fn.replace(/\.<locals>.*$/, "")};
  }
  function codeBtn(ref, label) {
    if (!ref) return '<span class="tz-muted">' + esc(label || "—") + "</span>";
    return '<button class="tz-cl" type="button" data-f="' + esc(ref.f) + '" data-fn="' + esc(ref.fn || "") + '">' +
      esc(label || (ref.f + (ref.fn ? " · " + ref.fn + "()" : ""))) + "</button>";
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".tz-cl");
    if (b) openCode({f: b.dataset.f, fn: b.dataset.fn || "", line: b.dataset.line ? +b.dataset.line : 0});
  });

  // ---------- тун / кун ----------
  var theme = load("tz-theme", null) || (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  function setTheme(t) { theme = t; document.documentElement.dataset.theme = t; save("tz-theme", t); }
  setTheme(theme);
  $("tz-theme").onclick = function () { setTheme(theme === "dark" ? "light" : "dark"); };

  // ---------- чизиш ----------
  var FAM = {
    blue: ["#c6dbf7", "#94b6e6", "#7aa0d6"], green: ["#c9e9bb", "#97cc86", "#7fb86d"],
    orange: ["#f8c99c", "#eba066", "#d98a4e"], purple: ["#ddd2f5", "#b8a5e7", "#a08bd8"],
    yellow: ["#f7e6a8", "#e5c866", "#d4b44c"], red: ["#f6c0c0", "#e58f8f", "#d47474"],
    slate: ["#ebe6dd", "#cdc5b8", "#b8af9f"]
  };
  var KIND_COLOR = {req: "var(--req)", res: "var(--res)", job: "var(--job)", data: "var(--data)", err: "var(--err)"};
  var IC = {
    shield: "M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z", server: "M4 5h16v5H4zM4 14h16v5H4zM8 7.5h.01M8 16.5h.01",
    gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2",
    convert: "M4 7h11l-3-3M20 17H9l3 3", search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
    table: "M4 5h16v14H4zM4 10h16M10 10v9", list: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
    skip: "M5 5l14 14M19 5L5 19",
    link: "M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1",
    columns: "M4 4h16v16H4zM9 4v16M15 4v16", pen: "M4 20l4-1 11-11-3-3L5 16zM14 6l3 3", sum: "M18 5H6l6 7-6 7h12",
    lock: "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3", user: "M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 21c0-4 4-6 8-6s8 2 8 6",
    file: "M6 3h8l4 4v14H6zM14 3v4h4", clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2", check: "M5 12l5 5 9-10",
    db: "M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3zM5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"
  };
  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function pts(a) { return a.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" "); }
  function icon(g, name, x, y, s) {
    if (IC[name]) el("path", {d: IC[name], "class": "n-ic", transform: "translate(" + (x - 12 * s) + "," + (y - 12 * s) + ") scale(" + s + ")"}, g);
  }
  function pill(g, x, y, text, cls) {
    var t = el("g", {"class": cls || "n-tag"}, g), w = Math.max(40, text.length * 7.2 + 18);
    el("rect", {x: x - w / 2, y: y - 11, width: w, height: 22, rx: 11}, t);
    var tx = el("text", {x: x, y: y + 4}, t); tx.textContent = text;
    return t;
  }
  var R = {hex: [70, 46], db: [44, 40], disk: [76, 44], doc: [58, 36], users: [96, 52]};
  function drawNode(n) {
    var g = el("g", {"class": "n-g", "data-id": n.id}), f = FAM[n.c || "slate"], x = n.x, y = n.y;
    el("ellipse", {cx: x, cy: y + 8, rx: (R[n.kind] || [70])[0] + 14, ry: ((R[n.kind] || [0, 46])[1]) + 10, "class": "n-sel"}, g);
    if (n.kind === "hex") {
      var rx = 62, ry = 36, cy = y - 12, d = 24, v = [];
      for (var i = 0; i < 6; i++) { var a = Math.PI / 3 * i; v.push([x + rx * Math.cos(a), cy + ry * Math.sin(a)]); }
      var dn = function (p) { return [p[0], p[1] + d]; };
      el("polygon", {points: pts([v[0], v[1], dn(v[1]), dn(v[0])]), fill: f[2], "class": "n-face"}, g);
      el("polygon", {points: pts([v[1], v[2], dn(v[2]), dn(v[1])]), fill: f[1], "class": "n-face"}, g);
      el("polygon", {points: pts([v[2], v[3], dn(v[3]), dn(v[2])]), fill: f[2], "class": "n-face"}, g);
      el("polygon", {points: pts(v), fill: f[0], "class": "n-face"}, g);
      icon(g, n.ic, x, cy - 12, 0.95);
      var t = el("text", {x: x, y: cy + 17, "class": "n-lbl"}, g); t.textContent = n.label;
      if (n.sub) pill(g, x, y + ry + d + 4, n.sub);
    } else if (n.kind === "db") {
      var r1 = 36, r2 = 13, top = y - 34, bot = y + 14;
      el("path", {d: "M" + (x - r1) + "," + top + " L" + (x - r1) + "," + bot + " A" + r1 + "," + r2 + " 0 0 0 " + (x + r1) + "," + bot +
        " L" + (x + r1) + "," + top + " Z", fill: f[1], "class": "n-face"}, g);
      el("ellipse", {cx: x, cy: top, rx: r1, ry: r2, fill: f[0], "class": "n-face"}, g);
      icon(g, "db", x, y - 6, 0.85);
      pill(g, x, bot + 30, n.label + (n.sub ? " · " + n.sub : ""));
    } else if (n.kind === "disk") {
      var w = 72, h = 38, th = 16, cy2 = y - 6, T = [[x - w, cy2], [x, cy2 - h], [x + w, cy2], [x, cy2 + h]];
      el("polygon", {points: pts([T[0], T[3], [T[3][0], T[3][1] + th], [T[0][0], T[0][1] + th]]), fill: f[1], "class": "n-face"}, g);
      el("polygon", {points: pts([T[3], T[2], [T[2][0], T[2][1] + th], [T[3][0], T[3][1] + th]]), fill: f[2], "class": "n-face"}, g);
      el("polygon", {points: pts(T), fill: f[0], "class": "n-face"}, g);
      for (var k = 0; k < 3; k++) {
        el("line", {x1: x - 30 + k * 12, y1: cy2 - 10 + k * 7, x2: x + 6 + k * 12, y2: cy2 - 28 + k * 7, stroke: "#2a2622", "stroke-width": 2, "stroke-linecap": "round"}, g);
      }
      pill(g, x, y + h + th + 14, n.label + (n.sub ? " · " + n.sub : ""));
    } else if (n.kind === "doc") {
      for (var s = 1; s >= 0; s--) {
        var oy = y - s * 12, ww = 52, hh = 30, Dd = [[x - ww, oy], [x, oy - hh], [x + ww, oy], [x, oy + hh]];
        el("polygon", {points: pts([Dd[0], Dd[3], [Dd[3][0], Dd[3][1] + 6], [Dd[0][0], Dd[0][1] + 6]]), fill: f[1], "class": "n-face"}, g);
        el("polygon", {points: pts([Dd[3], Dd[2], [Dd[2][0], Dd[2][1] + 6], [Dd[3][0], Dd[3][1] + 6]]), fill: f[2], "class": "n-face"}, g);
        el("polygon", {points: pts(Dd), fill: s ? f[1] : "#fffdf8", "class": "n-face"}, g);
      }
      for (var l = 0; l < 4; l++) {
        el("line", {x1: x - 28 + l * 8, y1: y - 20 + l * 5, x2: x + 4 + l * 8, y2: y - 38 + l * 5, stroke: f[2], "stroke-width": 3, "stroke-linecap": "round"}, g);
      }
      pill(g, x, y + 54, n.label + (n.sub ? " · " + n.sub : ""));
    } else if (n.kind === "users") {
      var pw = 92, ph = 46, pt = 12, base = n.c === "red" ? FAM.red : FAM.slate;
      var P = [[x - pw, y], [x, y - ph], [x + pw, y], [x, y + ph]];
      el("polygon", {points: pts([P[0], P[3], [P[3][0], P[3][1] + pt], [P[0][0], P[0][1] + pt]]), fill: base[1], "class": "n-face"}, g);
      el("polygon", {points: pts([P[3], P[2], [P[2][0], P[2][1] + pt], [P[3][0], P[3][1] + pt]]), fill: base[2], "class": "n-face"}, g);
      el("polygon", {points: pts(P), fill: base[0], "class": "n-face"}, g);
      var cols = n.c === "red" ? ["#e58f8f", "#d47474", "#f6c0c0"] : ["#b8a5e7", "#eba066", "#97cc86", "#94b6e6", "#e5c866"];
      [[-40, -6], [-14, -20], [14, -10], [-18, 10], [10, 16], [38, 0], [-44, 18]].forEach(function (sp, i2) {
        var px = x + sp[0], py = y + sp[1] - 8;
        el("rect", {x: px - 6, y: py - 2, width: 12, height: 17, rx: 5, fill: cols[i2 % cols.length], "class": "n-face", "stroke-width": 1.6}, g);
        el("circle", {cx: px, cy: py - 7, r: 5.5, fill: "#fffdf8", "class": "n-face", "stroke-width": 1.6}, g);
      });
      pill(g, x, y + ph + pt + 18, n.label + (n.sub ? " · " + n.sub : ""));
    }
    return g;
  }
  function edgePoints(a, b) {
    var ra = R[a.kind] || [60, 40], rb = R[b.kind] || [60, 40];
    var dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
    var ta = 1 / Math.hypot(ux / ra[0], uy / ra[1]), tb = 1 / Math.hypot(ux / rb[0], uy / rb[1]);
    return [[a.x + ux * ta, a.y + uy * ta], [b.x - ux * tb, b.y - uy * tb]];
  }

  // ---------- сахна (жонли ва жараёнлар учун бир хил) ----------
  var scenes = [];
  function Scene(svg) {
    var me = {svg: svg, nodes: {}, els: {}, edges: {}, flows: [], t0: performance.now(), onPick: null};
    me.build = function (nodes, pairs) {
      svg.innerHTML = "";
      var d = el("defs", {}, svg);
      var m = el("marker", {id: svg.id + "-ar", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"}, d);
      el("path", {d: "M0,0 L10,5 L0,10 z", fill: "currentColor"}, m);
      me.nodes = {}; me.els = {}; me.edges = {};
      nodes.forEach(function (n) { me.nodes[n.id] = n; });
      var lE = el("g", {}, svg), lN = el("g", {}, svg);
      me.lB = el("g", {}, svg); me.lP = el("g", {}, svg);
      pairs.forEach(function (k) {
        var ab = k.split("|"), a = me.nodes[ab[0]], b = me.nodes[ab[1]];
        if (!a || !b || me.edges[k]) return;
        var p = edgePoints(a, b);
        me.edges[k] = el("line", {x1: p[0][0], y1: p[0][1], x2: p[1][0], y2: p[1][1], "class": "e-line",
          "marker-end": "url(#" + svg.id + "-ar)", "marker-start": "url(#" + svg.id + "-ar)", style: "color: var(--ink)"}, lE);
      });
      nodes.slice().sort(function (a, b) { return a.y - b.y; }).forEach(function (n) {
        var g = drawNode(n); me.els[n.id] = g; lN.appendChild(g);
        g.addEventListener("click", function () { if (me.onPick) me.onPick(n.id); });
        if (NODES[n.id] || me.clickAll) g.classList.add("is-click");
      });
    };
    me.badges = function (map) {
      me.lB.innerHTML = "";
      Object.keys(map || {}).forEach(function (id) {
        var n = me.nodes[id]; if (!n) return;
        var v = map[id], text = typeof v === "string" ? v : v.t, cls = typeof v === "object" ? v.cls : "";
        var off = {hex: 78, db: 66, disk: 58, doc: 60, users: 70}[n.kind] || 70;
        pill(me.lB, n.x, n.y - off, text, "n-badge" + (cls ? " is-" + cls : ""));
      });
    };
    me.dim = function (on) {
      Object.keys(me.els).forEach(function (k) { me.els[k].classList.toggle("is-dim", !!on && on.indexOf(k) < 0); });
    };
    me.select = function (id) {
      Object.keys(me.els).forEach(function (k) { me.els[k].classList.toggle("is-sel", k === id); });
    };
    me.mark = function (bad) {
      Object.keys(me.els).forEach(function (k) { me.els[k].classList.toggle("is-bad", !!bad[k]); });
    };
    me.setFlows = function (list, keep) {
      var sig = JSON.stringify(list);
      if (keep && sig === me.sig) return;
      me.sig = sig;
      me.lP.innerHTML = "";
      var active = {};
      me.flows = (list || []).map(function (f, i) {
        var a = me.nodes[f[0]], b = me.nodes[f[1]];
        if (!a || !b) return null;
        active[[f[0], f[1]].sort().join("|")] = 1;
        var p = edgePoints(a, b);
        var c = el("circle", {r: 6.5, fill: KIND_COLOR[f[2]] || "var(--req)", "class": "pk", opacity: 0}, me.lP);
        var x = null;
        if (f[2] === "err") { x = el("text", {"font-size": 18, "font-weight": 800, fill: "var(--err)", "text-anchor": "middle", opacity: 0}, me.lP); x.textContent = "✕"; }
        return {p: p, c: c, x: x, i: i, err: f[2] === "err", dur: 1300 + Math.hypot(p[1][0] - p[0][0], p[1][1] - p[0][1]) * 1.2};
      }).filter(Boolean);
      Object.keys(me.edges).forEach(function (k) { me.edges[k].classList.toggle("is-on", !!active[k]); });
      me.t0 = performance.now();
    };
    scenes.push(me);
    return me;
  }
  function frame(now) {
    scenes.forEach(function (sc) {
      if (!sc.flows.length || sc.svg.closest("[hidden]")) return;
      var t = now - sc.t0, gap = 420;
      var period = (sc.flows.length - 1) * gap + Math.max.apply(null, sc.flows.map(function (f) { return f.dur; })) + 900;
      sc.flows.forEach(function (f) {
        var local = ((t - f.i * gap) % period + period) % period, k = local / f.dur;
        if (t - f.i * gap < 0 || k > 1) { f.c.setAttribute("opacity", 0); if (f.x) f.x.setAttribute("opacity", 0); return; }
        var stop = f.err ? 0.55 : 1, kk = Math.min(k / stop, 1) * stop;
        var e = kk < .5 ? 2 * kk * kk : 1 - Math.pow(-2 * kk + 2, 2) / 2;
        var x = f.p[0][0] + (f.p[1][0] - f.p[0][0]) * e, y = f.p[0][1] + (f.p[1][1] - f.p[0][1]) * e;
        f.c.setAttribute("cx", x); f.c.setAttribute("cy", y);
        f.c.setAttribute("opacity", f.err && k > stop ? Math.max(0, 1 - (k - stop) * 3) : 1);
        if (f.x) { f.x.setAttribute("x", x); f.x.setAttribute("y", y - 10); f.x.setAttribute("opacity", k > stop ? 1 : 0); }
      });
    });
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ===================== ТАБЛАР =====================
  var tab = "live";
  function showTab(t) {
    tab = t;
    ["live", "guide", "code"].forEach(function (k) { $("v-" + k).hidden = k !== t; });
    document.querySelectorAll(".tz-tab").forEach(function (b) { b.classList.toggle("is-on", b.dataset.tab === t); });
    if (t === "code") loadFiles();
    if (t === "guide" && !guide.lesson) guideGo(LESSONS[0].id, 0, true);
    if (t === "live") pull();
    var h = t === "guide" && guide.lesson ? "guide/" + guide.lesson.id + "/" + (guide.step + 1) : t;
    history.replaceState(null, "", "#" + h);
  }
  document.querySelectorAll(".tz-tab").forEach(function (b) { b.onclick = function () { showTab(b.dataset.tab); }; });

  // ===================== ЖОНЛИ ҲОЛАТ =====================
  var MAP = LESSONS[0].nodes;
  var live = Scene($("svg-live"));
  live.build(MAP, ["users|nginx", "nginx|web", "pg|web", "redis|web", "media|web", "pg|worker", "media|worker", "lo|web", "lo|worker"]);
  live.onPick = function (id) { sel = sel === id ? null : id; live.select(sel); renderInsp(); };
  var S = null, sel = null, ttab = "now", failCount = 0;

  function pull() {
    if (tab !== "live" || document.hidden) return;
    fetch(D.live, {credentials: "same-origin", cache: "no-store"}).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function (d) {
      S = d; failCount = 0;
      $("tz-livedot").classList.remove("is-off");
      renderLive();
    }).catch(function () {
      failCount++;
      $("tz-livedot").classList.add("is-off");
      if (failCount > 1) $("tz-strip").innerHTML = '<div class="tz-chip is-bad"><small>Алоқа</small><b>Сервер жавоб бермаяпти</b></div>';
    });
  }
  setInterval(pull, 3000);
  setInterval(function () { $("tz-clock").textContent = new Date().toLocaleTimeString(); }, 1000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) pull(); });

  function procs(key) { return (S.server.procs || {})[key] || []; }
  function sumRss(list) { return list.reduce(function (a, p) { return a + (p.rss || 0); }, 0); }
  function badNodes() {
    var b = {};
    S.alerts.forEach(function (a) { if (a[0] === "bad") b[a[1]] = 1; });
    return b;
  }

  function renderLive() {
    var d = S, rq = d.requests, srv = d.server, t = d.tasks, w = d.workers;
    var bad = S.alerts.filter(function (a) { return a[0] === "bad"; }).length, warn = S.alerts.length - bad;
    var disk = (srv.disks || [])[0];
    // юқори қатор
    var chips = [
      ["Ҳолат", bad ? bad + " та хато" + (warn ? ", " + warn + " огоҳ." : "") : warn ? warn + " та огоҳлантириш" : "✓ Жойида", bad ? "bad" : warn ? "warn" : "ok", "alerts"],
      ["Сўров / дақ.", rq.per_min, "", "web"],
      ["Ўртача жавоб", ms(rq.avg_ms), msCls(rq.avg_ms), "web"],
      ["Ҳозир бажарилмоқда", rq.flight.length + " сўров · " + t.running.length + " иш", "", "now"],
      ["Навбатда", t.waiting.length, t.waiting.length ? "warn" : "", "worker"],
      ["Процессор", srv.cpu != null ? srv.cpu + " %" : "—", srv.cpu > 85 ? "bad" : "", "server"],
      ["RAM", srv.ram ? bytes(srv.ram.used) + " / " + bytes(srv.ram.total) : "—", srv.ram && srv.ram.pct > 90 ? "bad" : "", "server"],
      ["Диск", disk ? disk.pct + " % · бўш " + bytes(disk.free) : "—", disk && disk.pct > 80 ? "warn" : "", "media"],
      ["Онлайн", d.online, "", "users"]
    ];
    $("tz-strip").innerHTML = chips.map(function (c) {
      return '<button class="tz-chip ' + (c[2] ? "is-" + c[2] : "") + '" data-go="' + c[3] + '"><small>' + c[0] + "</small><b>" + esc(c[1]) + "</b></button>";
    }).join("");
    $("tz-strip").querySelectorAll("[data-go]").forEach(function (b) {
      b.onclick = function () {
        var g = b.dataset.go;
        if (g === "now") { setT("now"); return; }
        sel = g === "alerts" || g === "server" ? null : g; live.select(sel); renderInsp();
      };
    });
    // блоклар устидаги рақамлар
    var wBusy = w.list.reduce(function (a, x) { return a + (x.alive ? x.busy : 0); }, 0);
    var wThreads = w.list.reduce(function (a, x) { return a + (x.alive ? x.threads : 0); }, 0);
    var ngx = d.nginx, ngxBadge = ngx.readable ? fmt(ngx.n) + " сўров · 15 дақ." : (rq.per_min + " сўров/дақ.");
    var b = {
      users: d.online + " онлайн",
      nginx: ngxBadge,
      web: rq.flight.length + " / " + d.gunicorn.slots + " банд · " + ms(rq.avg_ms),
      pg: d.db.size != null ? bytes(d.db.size) + (d.db.conn_total ? " · " + d.db.conn_total + " уланиш" : "") : "?",
      redis: d.cache.backend === "RedisCache" ? (d.cache.mem != null ? bytes(d.cache.mem) : "?") : "кеш: жараён ичида",
      media: disk ? "диск " + disk.pct + " %" : "?",
      worker: w.mode === "thread" ? "сайт ичида · " + t.running.length + " иш" : (w.alive ? wBusy + "/" + wThreads + " банд · навбат " + t.waiting.length : "ишламаяпти!"),
      lo: (d.lo.busy || []).length + " / " + d.lo.slots + " банд" + (d.lo.waiting ? " · навбат " + d.lo.waiting : "")
    };
    var badMap = badNodes(), bb = {};
    Object.keys(b).forEach(function (k) { bb[k] = {t: b[k], cls: badMap[k] ? "bad" : ""}; });
    live.badges(bb);
    live.mark(badMap);
    // ҳаракат: ҳақиқий сўров ва ишларга қараб
    var fl = [], nf = Math.min(4, rq.flight.length);
    for (var i = 0; i < nf; i++) fl.push(["users", "nginx", "req"], ["nginx", "web", "req"], ["web", "pg", "req"]);
    if (!nf && rq.per_min > 0) fl.push(["users", "nginx", "req"], ["nginx", "web", "req"]);
    var nt = Math.min(3, t.running.length);
    for (var j = 0; j < nt; j++) fl.push(["pg", "worker", "job"], ["worker", "media", "data"]);
    if (procs("lo").length) fl.push(["worker", "lo", "data"], ["lo", "media", "data"]);
    if (w.mode === "worker" && !w.alive && t.waiting.length) fl.push(["pg", "worker", "err"]);
    live.setFlows(fl, true);
    $("c-now").textContent = rq.flight.length + t.running.length + t.waiting.length || "";
    $("c-db").textContent = (d.db.active || []).length || "";
    renderInsp();
    renderTable();
  }

  // ---------- ўнг панел ----------
  function section(title, html) { return '<div class="tz-sec"><div class="tz-sec-h">' + title + "</div>" + html + "</div>"; }
  function procTable(list) {
    if (!list.length) return '<div class="tz-muted">Жараён топилмади</div>';
    return '<table class="tz-grid"><tr><th>PID</th><th>RAM</th><th>CPU</th><th>Оқим</th><th>Ишлаяпти</th></tr>' + list.map(function (p) {
      return "<tr><td>" + p.pid + (p.pid === S.server.me ? " ★" : "") + "</td><td>" + bytes(p.rss) + "</td><td>" + p.cpu + " %</td><td>" + p.threads +
        "</td><td>" + dur(Date.now() / 1000 - p.since) + "</td></tr>";
    }).join("") + "</table>";
  }
  function nodeLive(id) {
    var d = S, rq = d.requests, srv = d.server;
    if (id === "users") {
      var k = d.kinds, names = {user: "Кирган фойдаланувчи", guest: "Меҳмон", bot: "Бот / дастур", scanner: "Сканер", login_fail: "Хато кириш",
        blocked: "Блокланган IP", csrf: "CSRF рад этилди", bad_host: "Бегона Host"};
      return section("Ҳозир", table([["Онлайн", d.online], ["Бугун юклашлар", fmt(d.today.uploads)], ["Бугун нархланган смета", fmt(d.today.priced)],
        ["Бугун нархланган қатор", fmt(d.today.rows)], ["Жами фойдаланувчи", fmt(d.counts.users)]])) +
        section("Шу соатдаги сўровлар — ким", Object.keys(k).length ? table(Object.keys(k).map(function (x) {
          return [names[x] || x, fmt(k[x]), x === "scanner" || x === "login_fail" || x === "blocked" ? "warn" : ""];
        })) : '<div class="tz-muted">Шу соатда ҳали ёзув йўқ (журнал дақиқада бир ёзилади)</div>');
    }
    if (id === "nginx") {
      var n = d.nginx, info = NODES.nginx, codes = n.codes || {};
      var head = n.readable ? table([["Охирги " + n.minutes + " дақиқада", fmt(n.n) + " сўров"], ["Ўртача вақт (nginx’да)", ms(n.avg_ms), msCls(n.avg_ms)],
        ["95 % сўров шундан тез", n.p95_ms != null ? ms(n.p95_ms) : "—"], ["Жараёнлар", procs("nginx").length + " · " + bytes(sumRss(procs("nginx")))]])
        : '<div class="tz-warn"><span>!</span><div><small>Журнал ўқилмади</small>' + esc(n.log) + " — " + esc(n.error || "") +
          (d.gunicorn.debug ? "<br>Бу компьютерда nginx йўқ (ишлаб чиқиш) — серверда кўринади." : "<br>deploy/README.md, 7-бўлим: <code>usermod -aG adm narxlash</code>") + "</div></div>";
      var ct = '<table class="tz-grid"><tr><th>Код</th><th>Маъноси</th><th>15 дақ.</th></tr>' + info.codes.map(function (c) {
        var cnt = c[0].split("/").reduce(function (a, x) { return a + (codes[x] || 0); }, 0);
        var cls = cnt && /^(5|429|413)/.test(c[0]) ? "bad" : "";
        return '<tr><td><b>' + c[0] + "</b></td><td>" + c[1] + '</td><td class="' + cls + '">' + (n.readable ? fmt(cnt) : "—") + "</td></tr>";
      }).join("") + "</table>";
      var slow = (n.slow || []).length ? '<table class="tz-grid">' + n.slow.map(function (s) {
        return '<tr><td class="' + msCls(s.ms) + '">' + ms(s.ms) + "</td><td>" + s.method + " " + esc(s.uri) + "</td><td>" + s.status + "</td></tr>";
      }).join("") + "</table>" : "";
      return section("Ҳозир", head) + section("Жавоб кодлари — нега қайтаради", ct) + (slow ? section("Энг секин (nginx кўргани)", slow) : "");
    }
    if (id === "web") {
      var g = d.gunicorn, wp = procs("web");
      return section("Ҳозир", table([
        ["Жараён × оқим", g.workers + " × " + g.threads + " = " + g.slots + " сўров бир вақтда" + (g.debug ? " (серверда)" : "")],
        ["Айни пайтда бажарилмоқда", rq.flight.length + " сўров", rq.flight.length >= g.slots ? "bad" : ""],
        ["Дақиқасига (охирги 5 дақ.)", rq.per_min], ["Ўртача жавоб вақти", ms(rq.avg_ms), msCls(rq.avg_ms)],
        ["Шундан базада", ms(rq.db_ms)], ["Охирги соатда", fmt(rq.n_hour) + " сўров"],
        ["Хато 500 (15 дақ.)", rq.e5_15, rq.e5_15 ? "bad" : ""], ["Хато 4xx (15 дақ.)", rq.e4_15],
        ["Секин (" + rq.slow_ms / 1000 + "+ с, 15 дақ.)", rq.slow_15, rq.slow_15 ? "warn" : ""],
        ["Журналда хато (1 соат)", d.errors_hour, d.errors_hour ? "warn" : ""]
      ])) + section("Жараёнлар (" + (g.debug ? "runserver" : "gunicorn") + ")", procTable(wp) + '<div class="tz-muted">★ — шу саҳифага жавоб бераётган жараён</div>');
    }
    if (id === "pg") {
      var db = d.db;
      if (db.error) return section("Хато", '<div class="tz-warn"><span>!</span><div>' + esc(db.error) + "</div></div>");
      var rows = [["База", esc(db.version)], ["Ҳажми", bytes(db.size)]];
      if (db.vendor === "postgresql") {
        rows.push(["Уланишлар", db.conn_total + " / " + db.max_conn, db.conn_total > .8 * db.max_conn ? "bad" : ""],
          ["Фаол / кутмоқда", (db.conn.active || 0) + " / " + (db.conn["idle in transaction"] || 0)],
          ["Кешдан ўқилди", db.cache_hit != null ? db.cache_hit + " %" : "—", db.cache_hit != null && db.cache_hit < 95 ? "warn" : ""],
          ["Deadlock", db.deadlocks, db.deadlocks ? "warn" : ""], ["Жараёнлар (RAM)", procs("pg").length + " · " + bytes(sumRss(procs("pg")))]);
      }
      rows.push(["Объект / смета", fmt(d.counts.batches) + " / " + fmt(d.counts.jobs)]);
      var tb = '<table class="tz-grid"><tr><th>Жадвал</th><th>Қатор</th><th>Ҳажм</th></tr>' + (db.tables || []).map(function (x) {
        return "<tr><td>" + esc(x.name) + "</td><td>" + fmt(x.rows) + "</td><td>" + (x.size != null ? bytes(x.size) : "—") + "</td></tr>";
      }).join("") + "</table>";
      return section("Ҳозир", table(rows)) + section("Энг катта жадваллар", tb) +
        (db.vendor === "postgresql" ? section("Айни пайтдаги SQL", '<button class="tz-link" data-tt="db">«Базада ҳозир» жадвалида →</button>') : "");
    }
    if (id === "redis") {
      var c = d.cache;
      if (c.backend !== "RedisCache") return section("Ҳозир", table([["Кеш", esc(c.backend)], ["Изоҳ", esc(c.note || "")]]));
      if (c.error) return section("Хато", '<div class="tz-warn"><span>!</span><div>' + esc(c.error) + "</div></div>");
      return section("Ҳозир", table([["Версия", esc(c.version)], ["Хотира", bytes(c.mem) + (c.maxmem ? " / " + bytes(c.maxmem) : "")],
        ["Калитлар", fmt(c.keys)], ["Уланишлар", c.clients], ["Кешдан топилди", c.hit != null ? c.hit + " %" : "—"],
        ["Ишлаяпти", dur(c.uptime)]]));
    }
    if (id === "media") {
      var rows2 = (srv.disks || []).filter(function (x) { return !x.same; }).map(function (x) {
        return [esc(x.label) + '<br><span class="tz-muted">' + esc(x.path) + "</span>", bytes(x.used) + " / " + bytes(x.total) + " (" + x.pct + " %)<br>бўш: " + bytes(x.free), x.pct > 90 ? "bad" : x.pct > 80 ? "warn" : ""];
      });
      var m = d.media.dirs, mt = m ? '<table class="tz-grid"><tr><th>Папка</th><th>Ҳажм</th><th>Файл</th></tr>' + Object.keys(m).map(function (k2) {
        return "<tr><td>media/" + esc(k2) + "/</td><td>" + bytes(m[k2].size) + "</td><td>" + fmt(m[k2].files) + "</td></tr>";
      }).join("") + '</table><div class="tz-muted">Ўлчанди: ' + hhmm(d.media.at) + " (15 дақиқада бир)</div>" : '<div class="tz-muted">Ўлчанмоқда… (катта папкада бир неча сония)</div>';
      return section("Диск", table(rows2.length ? rows2 : [["Диск", "psutil йўқ"]])) + section("media/ ичида", mt);
    }
    if (id === "worker") {
      var w = d.workers, t = d.tasks;
      var wl = w.mode === "thread" ? '<div class="tz-note">DEBUG режими: алоҳида ишчи йўқ — фондаги иш сайт жараёни ичидаги оқимда бажарилади. Серверда бу ерда <b>narxlash-worker</b> кўринади.</div>'
        : w.list.length ? '<table class="tz-grid"><tr><th>Ишчи</th><th>Банд</th><th>Бажарди</th><th>Сигнал</th></tr>' + w.list.map(function (x) {
          return '<tr><td><span class="tz-st-dot ' + (x.alive ? "ok" : "bad") + '"></span>' + esc(x.name) + "</td><td>" + x.busy + " / " + x.threads + "</td><td>" + fmt(x.done) +
            '</td><td class="' + (x.alive ? "" : "bad") + '">' + dur(x.seen) + " олдин</td></tr>";
        }).join("") + "</table>" : '<div class="tz-warn"><span>!</span><div>Ишчи рўйхатда йўқ — <code>systemctl status narxlash-worker</code></div></div>';
      return section("Ишчилар", wl + procTable(procs("worker"))) +
        section("Ҳозир бажармоқда (" + t.running.length + ")", taskList(t.running, true)) +
        section("Навбатда кутмоқда (" + t.waiting.length + ")", taskList(t.waiting, false));
    }
    if (id === "lo") {
      var lo = d.lo, lp = procs("lo");
      var runNow = lp.length ? procTable(lp) : '<div class="tz-muted">Айни пайтда ўгириш йўқ (нормал ҳолат)</div>';
      var rec = lo.recent.length ? '<table class="tz-grid"><tr><th>Вақт</th><th>Файл</th><th>Сония</th></tr>' + lo.recent.map(function (r) {
        return "<tr><td>" + new Date(r.t).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) + "</td><td>" + esc(r.name) + (r.error ? '<br><span class="bad">' + esc(r.error) + "</span>" : "") +
          '</td><td class="' + (r.sec > 30 ? "warn" : "") + '">' + r.sec + "</td></tr>";
      }).join("") + "</table>" : '<div class="tz-muted">Ҳали ўгирилган файл йўқ</div>';
      var slotsH = '<table class="tz-grid"><tr><th>Ўрин</th><th>Файл</th><th>Қачондан</th></tr>';
      for (var si = 0; si < lo.slots; si++) {
        var hb = (lo.busy || [])[si];
        slotsH += "<tr><td>" + (si + 1) + "</td><td>" + (hb ? '<span class="tz-pulse"></span>' + esc(hb.name) + '<br><span class="tz-muted">' + esc(hb.host) + ":" + hb.pid + "</span>" : '<span class="tz-muted">бўш</span>') +
          '</td><td class="' + (hb && Date.now() / 1000 - hb.t > 60 ? "warn" : "") + '">' + (hb ? dur(Date.now() / 1000 - hb.t) : "—") + "</td></tr>";
      }
      slotsH += "</table>" + '<div class="tz-muted">Сервер бўйича бир вақтда кўпи билан ' + lo.slots + " та .xls очилади; қолганлари навбат кутади ва фойдаланувчига «Сервер банд» деб кўрсатилади.</div>";
      return section("Навбат", table([["Чеклов", lo.slots + " та бир вақтда"], ["Банд", (lo.busy || []).length + " / " + lo.slots],
        ["Навбатда кутмоқда", fmt(lo.waiting), lo.waiting ? "warn" : ""]]) + slotsH) +
        section("Ҳозир", table([["Дастур", lo.exe ? esc(lo.exe) : '<span class="warn">' + esc(lo.exe_error || "топилмади") + "</span>"],
        ["Усул", esc(lo.method || "—")], ["Вақт чеклови", lo.timeout + " с"], ["Бугун ўгирилди", fmt(lo.day.n) + (lo.day.errors ? " · хато " + lo.day.errors : ""), lo.day.errors ? "warn" : ""],
        ["Ўртача / энг узоқ", (lo.day.avg != null ? lo.day.avg + " с" : "—") + " / " + (lo.day.max != null ? lo.day.max + " с" : "—")]])) +
        section("Ишлаётган жараёнлар", runNow) + section("Охирги ўгиришлар", rec);
    }
    return "";
  }
  function taskList(list, running) {
    if (!list.length) return '<div class="tz-muted">' + (running ? "Ҳозир фонда иш йўқ" : "Навбат бўш") + "</div>";
    return list.map(function (x) {
      var pr = x.rows ? '<div class="tz-bar"><i style="width:' + Math.round(100 * x.rows[0] / Math.max(1, x.rows[1])) + '%"></i></div><small>' + fmt(x.rows[0]) + " / " + fmt(x.rows[1]) + " қатор</small>" : "";
      return '<div class="tz-task' + (x.key ? " is-click" : "") + (sel === "task:" + x.key ? " is-sel" : "") + '"' + (x.key ? ' data-task="' + esc(x.key) + '" title="Босинг — қайси босқичда, қайси файл, қайси код"' : "") + '><div><b>' + esc(x.kind) + "</b> · " + esc(x.name) + '</div><div class="tz-muted">' + esc(x.user) +
        (running ? " · " + esc(x.worker) : "") + " · " + (running ? "ишлаяпти " : "кутяпти ") + dur(x.age) + "</div>" +
        (x.step ? '<div class="tz-step">' + esc(x.step) + "</div>" : "") + pr +
        '<div class="tz-now">Ҳозирги код: ' + codeBtn(x.code) + "</div></div>";
    }).join("");
  }
  function nodeStatic(id) {
    var n = NODES[id]; if (!n) return "";
    return section("Бу нима", "<p>" + n.what + "</p>") +
      section("Қандай ишлайди", "<ul>" + n.how.map(function (h) { return "<li>" + h + "</li>"; }).join("") + "</ul>") +
      section("Коди", n.code.map(function (c) {
        return '<button class="tz-ref tz-cl" type="button" data-f="' + esc(c.f) + '" data-fn="' + esc(c.fn || "") + '"' + (c.find ? ' data-find="' + esc(c.find) + '"' : "") + "><b>" + esc(c.t) + "</b><small>" + esc(c.f) + (c.fn ? " · " + esc(c.fn) + "()" : "") + "</small></button>";
      }).join(""));
  }
  // ---------- битта иш: аниқ қаерда ----------
  var STAGE_NODES = {queue: ["pg", "worker"], prep: ["worker", "media", "pg"], read: ["worker", "media"], price: ["worker", "pg"],
    write: ["worker", "media"], save: ["worker", "media"], auto: ["worker", "pg"], queued: ["pg", "worker"],
    converting: ["worker", "lo", "media"], ready: ["worker", "pg"], pricing: ["worker", "pg", "media"], done: ["worker"], run: ["worker", "pg", "media"]};
  var rate = {};  // key -> [вақт, бажарилган қатор, тезлик]
  function findTask(key) {
    var all = S.tasks.running.concat(S.tasks.waiting);
    for (var i = 0; i < all.length; i++) if (all[i].key === key) return all[i];
    return null;
  }
  function trackRate(x) {
    if (!x.rows || !x.key) return null;
    var now = Date.now() / 1000, r = rate[x.key];
    if (!r || x.rows[0] < r[1]) { rate[x.key] = [now, x.rows[0], r ? r[2] : null]; return rate[x.key][2]; }
    if (x.rows[0] > r[1] && now - r[0] > 1) {
      var v = (x.rows[0] - r[1]) / (now - r[0]);
      rate[x.key] = [now, x.rows[0], r[2] ? r[2] * 0.5 + v * 0.5 : v];
    }
    return rate[x.key][2];
  }
  function taskDetail(key) {
    var x = findTask(key);
    if (!x) {
      live.dim(null);
      return '<div class="tz-insp-h"><div><small>Фондаги иш</small><h2>Иш тугади</h2></div><button class="tz-ibtn" id="tz-unsel">✕</button></div>' +
        '<div class="tz-okbox">✓ Бу иш рўйхатдан чиқди — тугади ёки тўхтатилди. Натижа фойдаланувчи саҳифасида.</div>';
    }
    var d = x.detail || {}, st = d.stages || [], cur = d.cur || 0, now = st[cur] || {};
    live.dim(STAGE_NODES[now.key] || ["worker"]);
    live.select("worker");
    var h = '<div class="tz-insp-h"><div><small>' + esc(x.kind) + " · " + esc(x.user) + "</small><h2>" + esc(x.name) +
      '</h2></div><button class="tz-ibtn" id="tz-unsel">✕</button></div>';
    // қаерда
    var where = [["Ҳолат", x.worker ? '<span class="tz-pulse"></span>ишлаяпти' : "навбатда кутяпти", x.worker ? "" : "warn"],
      ["Ким бажаряпти", x.worker ? esc(x.worker) + '<br><span class="tz-muted">ишчи:PID#оқим</span>' : "—"],
      [x.worker ? "Бошланганига" : "Кутганига", dur(x.age)]];
    if (d.step_age != null && x.worker) where.push(["Шу қадамда", dur(d.step_age), d.step_age > 300 ? "warn" : ""]);
    if (d.heartbeat != null && x.worker) where.push(["Охирги сигнал", dur(d.heartbeat) + " олдин", d.heartbeat > 120 ? "bad" : ""]);
    h += section("Ҳозир", table(where));
    // босқичлар
    h += section("Босқичлар — ҳозир қаерда", '<ol class="tz-stages">' + st.map(function (s2, i) {
      var cls = i < cur ? "is-done" : i === cur ? "is-now" : "";
      return '<li class="' + cls + '"><i>' + (i < cur ? "✓" : i + 1) + "</i><div><b>" + esc(s2.t) + "</b>" + (i === cur ? '<span class="tz-now-tag">ҳозир</span>' : "") +
        (s2.what ? '<div class="tz-muted">' + esc(s2.what) + "</div>" : "") + (s2.code ? codeBtn(s2.code, s2.code.f + " · " + s2.code.fn + "()") : "") + "</div></li>";
    }).join("") + "</ol>");
    // қатор прогресси
    if (x.rows) {
      var v = trackRate(x), left = x.rows[1] - x.rows[0];
      h += section("Қаторлар", '<div class="tz-bar big"><i style="width:' + Math.round(100 * x.rows[0] / Math.max(1, x.rows[1])) + '%"></i></div>' +
        table([["Бажарилди", fmt(x.rows[0]) + " / " + fmt(x.rows[1])], ["Тезлик", v ? Math.round(v) + " қатор/с" : "ўлчанмоқда…"],
          ["Тахминан қолди", v && left > 0 ? dur(left / v) : "—"]]));
    }
    if (x.step) h += section("Ишчи ёзган охирги қадам", '<div class="tz-step">' + esc(x.step) + "</div>");
    // сметалар (нархлаш)
    if (d.smetas && d.smetas.length) {
      h += section("Сметалар (" + d.smetas.length + ")", d.smetas.map(function (m, i) {
        var mark = m.state === "done" ? "✓" : m.state === "now" ? "▶" : i + 1;
        return '<div class="tz-smeta is-' + m.state + '"><i>' + mark + "</i><div><b>" + esc(m.label) + '</b><div class="tz-muted">Локал: ' + esc(m.local) +
          (m.local_sheet ? " → «" + esc(m.local_sheet) + "»" : "") + "<br>Ресурс: " + esc(m.res) + (m.res_sheet ? " → «" + esc(m.res_sheet) + "»" : "") + "</div></div></div>";
      }).join(""));
    }
    if (d.part !== undefined && d.part) h += section("Ҳозирги ички папка", "<b>" + esc(d.part) + "</b>");
    if (d.parts && d.parts.length) h += section("Ички папкалар", table(d.parts.map(function (p2) { return [esc(p2.t), p2.n]; })));
    if (d.files && d.files.length) {
      h += section("Файллар (" + (d.nfiles || d.files.length) + ")", '<div class="tz-files-l">' + d.files.slice(0, 60).map(function (f) {
        var ext = (f.split(".").pop() || "").toLowerCase();
        return "<div><span class=\"tz-ext\">" + esc(ext) + "</span>" + esc(f) + (ext === "xls" ? ' <span class="tz-muted">(LibreOffice → .xlsx)</span>' : "") + "</div>";
      }).join("") + (d.files.length > 60 ? '<div class="tz-muted">… яна ' + (d.files.length - 60) + "</div>" : "") + "</div>");
    }
    if (d.url) h += '<div class="tz-links"><a href="' + esc(d.url) + '" target="_blank" rel="noopener">Фойдаланувчи кўрадиган саҳифа →</a></div>';
    return h;
  }
  document.addEventListener("click", function (e) {
    var t = e.target.closest(".tz-task[data-task]");
    if (!t || e.target.closest(".tz-cl")) return;
    sel = "task:" + t.dataset.task; renderInsp(); renderTable();
    if (window.innerWidth < 900) $("tz-insp").scrollIntoView({behavior: "smooth"});
  });

  function renderInsp() {
    if (!S) { $("tz-insp").innerHTML = '<div class="tz-muted">Юкланмоқда…</div>'; return; }
    var box = $("tz-insp"), keep = box.scrollTop, h;
    if (!sel || sel.indexOf("task:") !== 0) live.dim(null);
    if (sel && sel.indexOf("task:") === 0) {
      h = taskDetail(sel.slice(5));
    } else if (!sel) {
      var al = S.alerts.length ? S.alerts.map(function (a) {
        return '<button class="tz-alert is-' + a[0] + '" data-node="' + a[1] + '"><i></i><span>' + esc(a[2]) + "</span></button>";
      }).join("") : '<div class="tz-okbox">✓ Муаммо топилмади</div>';
      var srv = S.server;
      h = '<div class="tz-insp-h"><div><small>Умумий ҳолат</small><h2>' + esc(srv.host) + "</h2></div></div>" +
        section("Муаммолар", al) +
        section("Сервер", table([["Ишлаяпти", srv.boot ? dur(Date.now() / 1000 - srv.boot) : "—"], ["Процессор", srv.cpu != null ? srv.cpu + " % · " + srv.cores + " ядро" : "—"],
          ["Юклама (1/5/15 дақ.)", srv.load ? srv.load.map(function (x) { return x.toFixed(2); }).join(" / ") : "—"],
          ["RAM", srv.ram ? bytes(srv.ram.used) + " / " + bytes(srv.ram.total) + " (" + srv.ram.pct + " %)" : "—", srv.ram && srv.ram.pct > 90 ? "bad" : ""],
          ["Swap", srv.swap ? bytes(srv.swap.used) + " / " + bytes(srv.swap.total) : "—", srv.swap && srv.swap.pct > 50 ? "warn" : ""]])) +
        section("RAM ким ишлатяпти", table([["Сайт", procs("web").length + " жараён · " + bytes(sumRss(procs("web")))], ["Фон ишчиси", procs("worker").length + " · " + bytes(sumRss(procs("worker")))],
          ["PostgreSQL", procs("pg").length + " · " + bytes(sumRss(procs("pg")))], ["Redis", procs("redis").length + " · " + bytes(sumRss(procs("redis")))],
          ["Nginx", procs("nginx").length + " · " + bytes(sumRss(procs("nginx")))], ["LibreOffice", procs("lo").length + " · " + bytes(sumRss(procs("lo")))]])) +
        section("Бугун", table([["Юклашлар", fmt(S.today.uploads)], ["Нархланган смета", fmt(S.today.priced)], ["Нархланган қатор", fmt(S.today.rows)],
          ["Хато билан тугади", S.today.failed, S.today.failed ? "warn" : ""]])) +
        '<div class="tz-links"><a href="' + D.errors + '">Хатолар журнали →</a><a href="' + D.processes + '">Фондаги жараёнлар →</a><a href="' + D.backups + '">Захира →</a></div>' +
        '<div class="tz-muted">Блокни босинг — унинг ҳолати, вазифаси ва коди. Янгиланди: ' + new Date(S.now).toLocaleTimeString() + "</div>";
    } else {
      var n = NODES[sel] || {title: sel, role: ""}, isBad = badNodes()[sel];
      var mine = S.alerts.filter(function (a) { return a[1] === sel; });
      h = '<div class="tz-insp-h"><div><small>' + esc(n.role) + "</small><h2>" + esc(n.title) + '</h2></div><button class="tz-ibtn" id="tz-unsel">✕</button></div>' +
        (mine.length ? section("Муаммо", mine.map(function (a) { return '<div class="tz-alert is-' + a[0] + '"><i></i><span>' + esc(a[2]) + "</span></div>"; }).join("")) : "") +
        nodeLive(sel) + nodeStatic(sel);
      if (isBad) h = h.replace("tz-insp-h", "tz-insp-h is-bad");
    }
    box.innerHTML = h;
    box.scrollTop = keep;
    box.querySelectorAll("[data-node]").forEach(function (b) {
      b.onclick = function () { sel = b.dataset.node === "server" || b.dataset.node === "backup" ? null : b.dataset.node; live.select(sel); renderInsp(); };
    });
    box.querySelectorAll("[data-tt]").forEach(function (b) { b.onclick = function () { setT(b.dataset.tt); }; });
    box.querySelectorAll(".tz-ref[data-find]").forEach(function (b) {
      b.onclick = function (e) { e.stopPropagation(); openCode({f: b.dataset.f, find: b.dataset.find}); };
    });
    var un = $("tz-unsel"); if (un) un.onclick = function () { sel = null; live.select(null); renderInsp(); };
  }

  // ---------- пастки жадваллар ----------
  function setT(t) {
    ttab = t;
    $("tz-ttabs").querySelectorAll("button").forEach(function (b) { b.classList.toggle("is-on", b.dataset.t === t); });
    renderTable();
  }
  $("tz-ttabs").querySelectorAll("button").forEach(function (b) { b.onclick = function () { setT(b.dataset.t); }; });
  function reqRows(list, withStatus) {
    return list.map(function (r) {
      var ref = viewRef(r.view);
      return "<tr><td>" + (withStatus ? hhmm(r.t) : '<span class="tz-pulse"></span>' + ms(r.ms)) + '</td><td><b>' + r.method + "</b> " + esc(r.path) + "</td>" +
        (withStatus ? '<td class="' + (r.status >= 500 ? "bad" : r.status >= 400 ? "warn" : "") + '">' + r.status + '</td><td class="' + msCls(r.ms) + '">' + ms(r.ms) + "</td><td>" + ms(r.db) + " · " + r.q + "</td>" : "") +
        "<td>" + codeBtn(ref, ref ? ref.fn + "()" : (r.view || "…")) + "</td><td>" + esc(r.user || "—") + "</td></tr>";
    }).join("");
  }
  function renderTable() {
    if (!S) return;
    var box = $("tz-tbody"), h = "", rq = S.requests, t = S.tasks;
    if (ttab === "now") {
      h = '<div class="tz-th">Сўровлар — сайт ҳозир шуларга жавоб тайёрлаяпти</div>' + (rq.flight.length ?
        '<table class="tz-grid"><tr><th>Қачондан</th><th>Манзил</th><th>Ишлаётган функция</th><th>Ким</th></tr>' + reqRows(rq.flight, false) + "</table>"
        : '<div class="tz-muted">Айни пайтда бажарилаётган сўров йўқ (шу саҳифанинг ўз сўровлари ҳисобланмайди)</div>') +
        '<div class="tz-th">Фондаги ишлар</div><div class="tz-tasks">' + taskList(t.running, true) + "</div>" +
        (t.waiting.length ? '<div class="tz-th">Навбатда</div><div class="tz-tasks">' + taskList(t.waiting, false) + "</div>" : "");
    } else if (ttab === "recent") {
      h = rq.recent.length ? '<table class="tz-grid"><tr><th>Вақт</th><th>Манзил</th><th>Код</th><th>Жавоб</th><th>База (мс · сўров)</th><th>Функция</th><th>Ким</th></tr>' +
        reqRows(rq.recent, true) + "</table>" : '<div class="tz-muted">Ҳали сўров ёзилмаган</div>';
    } else if (ttab === "slow") {
      h = rq.slowest.length ? '<table class="tz-grid"><tr><th>Вақт</th><th>Манзил</th><th>Код</th><th>Жавоб</th><th>База</th><th>Функция</th><th>Ким</th></tr>' + reqRows(rq.slowest, true) + "</table>"
        : '<div class="tz-muted">Ҳали сўров йўқ</div>';
    } else if (ttab === "db") {
      var db = S.db;
      if (db.vendor !== "postgresql") h = '<div class="tz-muted">Ишлаб чиқиш базаси (SQLite) — айни пайтдаги SQL фақат PostgreSQL’да кўринади.</div>';
      else h = db.active && db.active.length ? '<table class="tz-grid"><tr><th>PID</th><th>Ҳолат</th><th>Қанча</th><th>Кутяпти</th><th>SQL</th></tr>' + db.active.map(function (q) {
        return "<tr><td>" + q.pid + "</td><td>" + esc(q.state) + '</td><td class="' + (q.sec > 30 ? "bad" : q.sec > 5 ? "warn" : "") + '">' + dur(q.sec) + "</td><td>" + esc(q.wait) + '</td><td><code class="tz-sql">' + esc(q.sql) + "</code></td></tr>";
      }).join("") + "</table>" : '<div class="tz-muted">Базада айни пайтда бажарилаётган сўров йўқ</div>';
    } else if (ttab === "chart") {
      h = chart(rq.series);
    }
    box.innerHTML = h;
  }
  function chart(series) {
    var W = 900, H = 150, n = series.length, max = Math.max.apply(null, series.map(function (s) { return s.n; }).concat([1]));
    var mx = Math.max.apply(null, series.map(function (s) { return s.n ? s.ms / s.n : 0; }).concat([100]));
    var bw = W / n, bars = "", line = "";
    series.forEach(function (s, i) {
      var bh = (H - 20) * s.n / max, x = i * bw;
      bars += '<rect x="' + (x + 1) + '" y="' + (H - bh) + '" width="' + (bw - 2) + '" height="' + bh + '" class="' + (s.e5 ? "c-bad" : "c-bar") + '"><title>' +
        hhmm(s.t) + " — " + s.n + " сўров, ўртача " + (s.n ? Math.round(s.ms / s.n) : 0) + " мс" + (s.e5 ? ", 500: " + s.e5 : "") + "</title></rect>";
      if (s.n) line += (line ? " L" : "M") + (x + bw / 2).toFixed(1) + "," + (H - (H - 20) * (s.ms / s.n) / mx).toFixed(1);
    });
    return '<div class="tz-th">Сўровлар дақиқа бўйича (устун) ва ўртача жавоб вақти (чизиқ, энг юқориси ' + Math.round(mx) + ' мс)</div>' +
      '<svg class="tz-chart" viewBox="0 0 ' + W + " " + (H + 18) + '">' + bars + (line ? '<path d="' + line + '" class="c-line"/>' : "") +
      '<text x="0" y="' + (H + 14) + '" class="c-ax">' + hhmm(series[0].t).slice(0, 5) + '</text><text x="' + W + '" y="' + (H + 14) + '" text-anchor="end" class="c-ax">ҳозир</text></svg>';
  }

  // ===================== ЖАРАЁНЛАР =====================
  var gScene = Scene($("svg-guide"));
  var guide = {lesson: null, step: 0, node: null};
  var seen = load("tz-seen", {});
  gScene.onPick = function (id) { guide.node = NODES[id] ? id : null; if (guide.node) renderGuideNode(); };
  var GROUP_COL = ["#f8c99c", "#97cc86", "#b8a5e7", "#e58f8f", "#94b6e6"];
  function renderTree() {
    var tree = $("tz-tree"); tree.innerHTML = "";
    var groups = [];
    LESSONS.forEach(function (l) {
      var g = groups[groups.length - 1];
      if (!g || g.name !== l.group) groups.push(g = {name: l.group, items: []});
      g.items.push(l);
    });
    groups.forEach(function (g, gi) {
      var box = document.createElement("div"); box.className = "tz-grp";
      box.innerHTML = '<div class="tz-grp-h"><i style="background:' + GROUP_COL[gi % GROUP_COL.length] + '"></i>' + esc(g.name) + "</div>";
      g.items.forEach(function (l) {
        var b = document.createElement("button"); b.type = "button";
        b.className = "tz-les" + (guide.lesson === l ? " is-on" : "");
        b.textContent = l.title;
        b.onclick = function () { guideGo(l.id, 0); };
        box.appendChild(b);
      });
      tree.appendChild(box);
    });
    $("tz-count").textContent = LESSONS.length + " та";
  }
  function guideGo(id, step, quiet) {
    var l = LESSONS.filter(function (x) { return x.id === id; })[0] || LESSONS[0];
    if (guide.lesson !== l) {
      guide.lesson = l;
      var pairs = {};
      l.steps.forEach(function (s) { (s.flow || []).forEach(function (f) { pairs[[f[0], f[1]].sort().join("|")] = 1; }); });
      gScene.build(l.nodes, Object.keys(pairs));
    }
    guide.step = Math.max(0, Math.min(step || 0, l.steps.length - 1));
    guide.node = null;
    var s = l.steps[guide.step];
    seen[l.id + ":" + guide.step] = 1; save("tz-seen", seen);
    if (!quiet || tab === "guide") history.replaceState(null, "", "#guide/" + l.id + "/" + (guide.step + 1));
    var gi = LESSONS.filter(function (x) { return x.group === l.group; });
    $("tz-crumb").textContent = l.group + "  /  " + (gi.indexOf(l) + 1) + " / " + gi.length;
    $("tz-title").textContent = l.title;
    $("tz-desc").textContent = l.desc || "";
    $("tz-meta").innerHTML = '<span class="tz-pill">қадамлар: ' + l.steps.length + "</span>";
    gScene.dim(s.on);
    gScene.badges(s.badge);
    gScene.setFlows(s.flow);
    $("tz-chips").innerHTML = (s.chips || []).map(function (c) { return '<div class="tz-chip"><small>' + esc(c[0]) + "</small><b>" + esc(c[1]) + "</b></div>"; }).join("");
    renderGuideCard(l, s);
    $("tz-stepn").textContent = "Қадам " + (guide.step + 1) + " / " + l.steps.length;
    $("tz-line").innerHTML = "";
    l.steps.forEach(function (st, i) {
      var b = document.createElement("button"); b.type = "button";
      b.className = "tz-st" + (i === guide.step ? " is-on" : "") + (seen[l.id + ":" + i] ? " is-seen" : "");
      b.innerHTML = "<i>" + (i + 1) + "</i><span>" + esc(st.t) + "</span>"; b.title = st.t;
      b.onclick = function () { guideGo(l.id, i); };
      $("tz-line").appendChild(b);
    });
    renderTree();
    $("tz-prev").disabled = guide.step === 0 && LESSONS.indexOf(l) === 0;
    var last = guide.step === l.steps.length - 1, nextL = LESSONS[LESSONS.indexOf(l) + 1];
    $("tz-next").textContent = last ? (nextL ? "Кейинги: " + nextL.title + " →" : "Охирги жараён") : "Кейинги: " + l.steps[guide.step + 1].t + " →";
    $("tz-next").disabled = last && !nextL;
  }
  function refBtn(c) {
    return '<button class="tz-ref tz-cl" type="button" data-f="' + esc(c.f) + '" data-fn="' + esc(c.fn || "") + '"' + (c.find ? ' data-find="' + esc(c.find) + '"' : "") +
      "><b>" + esc(c.t || c.fn || c.find) + "</b><small>" + esc(c.f) + (c.fn ? " · " + esc(c.fn) + "()" : "") + "</small></button>";
  }
  function bindFind(box) {
    box.querySelectorAll(".tz-ref[data-find]").forEach(function (b) {
      b.onclick = function (e) { e.stopPropagation(); openCode({f: b.dataset.f, find: b.dataset.find}); };
    });
  }
  function renderGuideCard(l, s) {
    var h = '<div class="tz-card-top"><span>Қадам ' + (guide.step + 1) + " / " + l.steps.length + "</span>" + (s.tag ? '<span class="tz-tag">' + esc(s.tag) + "</span>" : "") + "</div><h2>" + esc(s.t) + "</h2>";
    (s.txt || []).forEach(function (p) { h += "<p>" + p + "</p>"; });
    if (s.key) h += '<div class="tz-key"><span>✦</span><div><small>Асосий фикр</small>' + s.key + "</div></div>";
    if (s.warn) h += '<div class="tz-warn"><span>!</span><div><small>Эътибор беринг</small>' + s.warn + "</div></div>";
    if (s.code && s.code.length) h += '<div class="tz-sec-h">Шу қадамни бажарадиган код — босинг: вазифаси, коди, қаерда чақирилади</div>' + s.code.map(refBtn).join("");
    h += '<div class="tz-muted" style="margin-top:12px">Схемадаги блокни босинг — у нима эканлиги.</div>';
    var card = $("tz-card"); card.innerHTML = h; card.scrollTop = 0; bindFind(card);
  }
  function renderGuideNode() {
    var n = NODES[guide.node];
    var card = $("tz-card");
    card.innerHTML = '<div class="tz-card-top"><span>' + esc(n.role) + '</span><button class="tz-tag" id="tz-gback">← қадамга</button></div><h2>' + esc(n.title) + "</h2>" + nodeStatic(guide.node);
    bindFind(card);
    $("tz-gback").onclick = function () { guide.node = null; renderGuideCard(guide.lesson, guide.lesson.steps[guide.step]); };
  }
  $("tz-prev").onclick = function () {
    var l = guide.lesson;
    if (guide.step > 0) return guideGo(l.id, guide.step - 1);
    var p = LESSONS[LESSONS.indexOf(l) - 1]; if (p) guideGo(p.id, p.steps.length - 1);
  };
  $("tz-next").onclick = function () {
    var l = guide.lesson;
    if (guide.step < l.steps.length - 1) return guideGo(l.id, guide.step + 1);
    var n = LESSONS[LESSONS.indexOf(l) + 1]; if (n) guideGo(n.id, 0);
  };

  // ===================== КОД (фақат ўқиш) =====================
  var KW = {
    py: "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield self",
    js: "var let const function return if else for while do break continue new this typeof instanceof in of try catch finally throw switch case default null undefined true false class extends async await",
    sh: "if then else fi for do done while case esac function return exit set export local in"
  };
  function langOf(path) {
    var ext = (path.split(".").pop() || "").toLowerCase();
    return ext === "py" ? "py" : ext === "js" ? "js" : /^(sh|conf|service|yml|txt)$/.test(ext) ? "sh" : "plain";
  }
  function highlight(src, lang) {
    var lines = src.split("\n"), out = [], kw = {}, triple = null;
    (KW[lang] || "").split(" ").forEach(function (w) { kw[w] = 1; });
    var re = lang === "py" ? /(#.*$)|("""|''')|([rbfu]{0,2}"(?:\\.|[^"\\])*"|[rbfu]{0,2}'(?:\\.|[^'\\])*')|(\b\d[\d_.]*\b)|(@[\w.]+)|([A-Za-z_\u0400-\u04ff]\w*)/g
      : lang === "js" ? /(\/\/.*$)|(\/\*|\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(\b\d[\d.]*\b)|(@)|([A-Za-z_$]\w*)/g
      : lang === "sh" ? /(#.*$)|(\u0000)|("(?:\\.|[^"\\])*"|'[^']*')|(\b\d[\d.]*\b)|(\u0000)|([A-Za-z_]\w*)/g : null;
    lines.forEach(function (line) {
      if (!re) { out.push(esc(line)); return; }
      var h = "", pos = 0, prev = "", m;
      if (triple) {
        var end = line.indexOf(triple);
        if (end < 0) { out.push('<span class="t-s">' + esc(line) + "</span>"); return; }
        h += '<span class="t-s">' + esc(line.slice(0, end + 3)) + "</span>"; pos = end + 3; triple = null;
      }
      re.lastIndex = pos;
      while ((m = re.exec(line))) {
        h += esc(line.slice(pos, m.index));
        var tok = m[0], cls = "";
        if (m[1]) cls = "t-c";
        else if (m[2] && lang === "py") {
          var close = line.indexOf(tok, m.index + 3);
          if (close < 0) { h += '<span class="t-s">' + esc(line.slice(m.index)) + "</span>"; triple = tok; pos = line.length; break; }
          tok = line.slice(m.index, close + 3); cls = "t-s"; re.lastIndex = close + 3;
        } else if (m[2] && lang === "js") cls = "t-c";
        else if (m[3]) cls = "t-s";
        else if (m[4]) cls = "t-n";
        else if (m[5]) cls = "t-at";
        else if (m[6]) cls = kw[tok] ? "t-k" : (prev === "def" || prev === "class" || prev === "function") ? "t-d" : "";
        if (m[6]) prev = tok;
        h += cls ? '<span class="' + cls + '">' + esc(tok) + "</span>" : esc(tok);
        pos = re.lastIndex;
        if (!tok.length) re.lastIndex++;
      }
      out.push(h + esc(line.slice(pos)));
    });
    return out;
  }
  function renderCode(box, d) {
    var html = highlight(d.src, langOf(d.path)), s = d.start, e = d.end, buf = [];
    html.forEach(function (h, i) {
      var n = i + 1, f = s && n >= s && n <= e;
      buf.push('<div class="cl' + (f ? " is-f" : "") + '" id="' + box.id + "-L" + n + '"><em>' + n + "</em><span>" + (h || " ") + "</span></div>");
    });
    box.innerHTML = buf.join("");
    var target = s && document.getElementById(box.id + "-L" + Math.max(1, s - 3));
    box.scrollTop = target ? target.offsetTop - box.offsetTop : 0;
  }
  function fetchCode(ref) {
    var q = "?f=" + encodeURIComponent(ref.f) + (ref.fn ? "&fn=" + encodeURIComponent(ref.fn) : "") +
      (ref.find ? "&find=" + encodeURIComponent(ref.find) : "") + (ref.line ? "&line=" + ref.line : "");
    return fetch(D.code + q, {credentials: "same-origin"}).then(function (r) {
      if (!r.ok) throw new Error("Файл очилмади (" + r.status + ")");
      return r.json();
    });
  }
  var codeHist = [];
  function openCode(ref, back) {
    if (!back) { if (!$("tz-drawer").hidden && codeHist.length) codeHist.push(codeHist.cur); else codeHist = []; }
    codeHist.cur = ref;
    $("tz-dback").hidden = !codeHist.length;
    var box = $("tz-viewer"), info = $("tz-dinfo");
    $("tz-drawer").hidden = false;
    $("tz-dpath").textContent = ref.f + (ref.fn ? " · " + ref.fn + "()" : "");
    info.innerHTML = ""; box.innerHTML = '<div class="tz-empty">Юкланмоқда…</div>';
    fetchCode(ref).then(function (d) {
      renderCode(box, d);
      if (d.start) $("tz-dpath").textContent = ref.f + (ref.fn ? " · " + ref.fn + "()" : "") + "  ·  " + d.start + (d.end !== d.start ? "–" + d.end : "") + "-қатор";
      var h = "";
      if (d.doc) h += '<div class="tz-dsec"><small>Вазифаси (коддаги изоҳ)</small><div class="tz-doc">' + esc(d.doc) + "</div></div>";
      if (ref.fn) {
        h += '<div class="tz-dsec"><small>Қаерда чақирилади / уланади' + (d.callers.length ? " (" + d.callers.length + ")" : "") + "</small>" +
          (d.callers.length ? d.callers.map(function (c) {
            return '<button class="tz-call" data-f="' + esc(c.f) + '" data-line="' + c.line + '"><b>' + esc(c.f) + ":" + c.line + "</b> <code>" + esc(c.text) + "</code></button>";
          }).join("") : '<div class="tz-muted">Номи бўйича топилмади (масалан URL ёки навбат орқали чақирилади)</div>') + "</div>";
      }
      info.innerHTML = h;
      info.querySelectorAll(".tz-call").forEach(function (b) {
        b.onclick = function () { openCode({f: b.dataset.f, line: +b.dataset.line}); };
      });
    }).catch(function (e) { box.innerHTML = '<div class="tz-empty">' + esc(e.message) + "</div>"; });
  }
  $("tz-dback").onclick = function () { var r = codeHist.pop(); if (r) openCode(r, true); };
  $("tz-dclose").onclick = function () { $("tz-drawer").hidden = true; codeHist = []; };

  var files = null;
  function loadFiles() {
    if (files) return;
    fetch(D.code + "?list=1", {credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (d) { files = d.files; renderFiles(); });
  }
  function renderFiles() {
    var q = $("tz-fsearch").value.trim().toLowerCase(), list = $("tz-flist");
    list.innerHTML = "";
    (files || []).filter(function (f) { return !q || f.toLowerCase().indexOf(q) >= 0; }).slice(0, 500).forEach(function (f) {
      var b = document.createElement("button"); b.type = "button";
      var cut = f.lastIndexOf("/");
      b.innerHTML = '<span class="d">' + esc(f.slice(0, cut + 1)) + "</span>" + esc(f.slice(cut + 1));
      b.title = f;
      b.onclick = function () {
        list.querySelectorAll(".is-on").forEach(function (x) { x.classList.remove("is-on"); });
        b.classList.add("is-on");
        var box = $("tz-viewer2"); box.innerHTML = '<div class="tz-empty">Юкланмоқда…</div>';
        fetchCode({f: f}).then(function (d) { renderCode(box, d); });
      };
      list.appendChild(b);
    });
  }
  $("tz-fsearch").oninput = renderFiles;

  // ===================== клавиатура ва манзил =====================
  document.addEventListener("keydown", function (e) {
    if (e.target.tagName === "INPUT") return;
    if (e.key === "Escape") { if (!$("tz-drawer").hidden) $("tz-drawer").hidden = true; else if (sel) { sel = null; live.select(null); renderInsp(); } return; }
    if (tab === "guide" && $("tz-drawer").hidden) {
      if (e.key === "ArrowRight") $("tz-next").click();
      else if (e.key === "ArrowLeft") $("tz-prev").click();
    }
  });
  function fromHash() {
    var h = decodeURIComponent(location.hash.slice(1)).split("/");
    if (h[0] === "guide") { guideGo(h[1] || LESSONS[0].id, (parseInt(h[2], 10) || 1) - 1, true); showTab("guide"); }
    else if (h[0] === "code") showTab("code");
    else showTab("live");
  }
  window.addEventListener("hashchange", fromHash);
  renderInsp();
  fromHash();
})();
