/* Саҳифа «қурилади»: ўнг четда минорали кран туради. Блок экранга кирганда кран
   1) стрелани орқадан буриб келади — юк узоқдан (кичикроқ) яқинлашади,
   2) аравача стрела бўйлаб юкни жойи устига олиб боради,
   3) илгак юкни секин туширади, юк ўтиради,
   4) строплар ечилади, илгак кўтарилади, стрела буриб кетилади.
   Юклар навбат билан; тез айлантирилса кран тезлашади. Сарлавҳа сўзлари ғиштдек терилади.
   Тор экран (кран сиғмайди), «ҳаракатни камайтириш» ёки IntersectionObserver йўқ — ҳамма нарса жойида. */
(function () {
  "use strict";
  var root = document.documentElement;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !("IntersectionObserver" in window)) { root.classList.remove("asm"); return; }
  window.__asmReady = true;

  var LOADS = ".sheet, .mod, .measure, .index, .chain, .states, .final .cta";
  var BRICK = ".hero h1, .sec-h h2, .final h2";
  var NS = "http://www.w3.org/2000/svg";

  function clamp(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function ease(t) { return t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
  function settle(t) { var c = 1.4; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); }  // охирида бир оз силкиниб ўтиради
  function lerp(a, b, t) { return a + (b - a) * t; }
  function each(sel, fn) { [].forEach.call(document.querySelectorAll(sel), fn); }

  /* ---------- ғишт: сарлавҳа сўзлари ---------- */
  each(BRICK, function (el) {
    el.classList.add("asm-brick");
    var i = 0;
    (function wrap(node) {
      [].slice.call(node.childNodes).forEach(function (n) {
        if (n.nodeType === 3) {
          var frag = document.createDocumentFragment();
          n.textContent.split(/( +)/).forEach(function (part) {
            if (!part) return;
            if (/^ +$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
            var w = document.createElement("span");
            w.className = "asm-w"; w.style.setProperty("--i", i++); w.textContent = part;
            frag.appendChild(w);
          });
          node.replaceChild(frag, n);
        } else if (n.nodeType === 1) wrap(n);
      });
    })(el);
  });
  var brickIO = new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("asm-go"); brickIO.unobserve(e.target); } });
  }, {threshold: 0.3});
  each(".asm-brick", function (el) { brickIO.observe(el); });

  /* ---------- минорали кран ---------- */
  var MIN_W = 1180;  // кран ўнг четдаги бўш жойга сиғади
  // кенг экранда body zoom (1.15…1.5) билан катталашади: кран zoom'дан ташқарида — экран пикселларида чизилади,
  // элементнинг transform'и эса zoom ичида — силжиш zoom'га бўлинади
  function zoom() { return parseFloat(getComputedStyle(document.body).zoom) || 1; }
  var crane = null, queue = [], busy = false;

  function mk(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function lattice(x1, y1, x2, y2, h, step) {  // панжарали ферма: икки белбоғ ва зигзаг
    var d = "M" + x1 + " " + y1 + "H" + x2 + "M" + x1 + " " + (y1 - h) + "H" + x2, n = Math.max(2, Math.round(Math.abs(x2 - x1) / step));
    for (var i = 0; i <= n; i++) {
      var x = x1 + (x2 - x1) * i / n;
      d += (i === 0 ? "M" : "L") + x + " " + (i % 2 ? y1 - h : y1);
    }
    return d;
  }

  function build() {
    var W = window.innerWidth, H = window.innerHeight;
    var old = document.querySelector(".tc");
    if (old) old.remove();
    if (W < MIN_W) { crane = null; return; }
    var z = zoom(), mx = W - Math.round(50 * z), jy = Math.round(72 * z) + 52, base = H - 6, L = mx - Math.round(W * 0.16);
    var svg = mk("svg", {"class": "tc", width: W, height: H, viewBox: "0 0 " + W + " " + H, "aria-hidden": "true"});
    // минора: икки устун, ҳар 22 px да диагонал, пастда пойдевор
    var d = "M" + (mx - 7) + " " + (jy + 8) + "V" + base + "M" + (mx + 7) + " " + (jy + 8) + "V" + base, up = true;
    for (var y = jy + 8; y < base - 10; y += 22) { d += "M" + (up ? mx - 7 : mx + 7) + " " + y + "L" + (up ? mx + 7 : mx - 7) + " " + (y + 22); up = !up; }
    mk("path", {"class": "tc-ln", d: d}, svg);
    mk("rect", {"class": "tc-solid", x: mx - 18, y: base - 8, width: 36, height: 8}, svg);
    // айланувчи қисм: стрела, қарши стрела, қарши юклар, осма тортқилар (scaleX — буриш)
    var slew = mk("g", {}, svg);
    mk("path", {"class": "tc-ln", d: lattice(mx - 8, jy, mx - L, jy, 10, 18)}, slew);
    mk("path", {"class": "tc-ln", d: "M" + (mx - L) + " " + jy + "l-6 -5v-5"}, slew);
    mk("path", {"class": "tc-ln", d: lattice(mx + 8, jy, mx + 42, jy, 8, 12)}, slew);
    mk("rect", {"class": "tc-solid", x: mx + 22, y: jy - 1, width: 9, height: 18}, slew);
    mk("rect", {"class": "tc-solid", x: mx + 32, y: jy - 1, width: 9, height: 18}, slew);
    mk("path", {"class": "tc-thin", d: "M" + mx + " " + (jy - 48) + "L" + (mx - L * 0.62) + " " + (jy - 10) + "M" + mx + " " + (jy - 48) + "L" + (mx + 40) + " " + (jy - 8)}, slew);
    // минора боши ва кабина (бурилмайди)
    mk("path", {"class": "tc-ln", d: "M" + (mx - 8) + " " + (jy - 10) + "L" + mx + " " + (jy - 48) + "L" + (mx + 8) + " " + (jy - 10)}, svg);
    mk("rect", {"class": "tc-solid", x: mx - 28, y: jy + 2, width: 20, height: 17}, svg);
    mk("rect", {"class": "tc-win", x: mx - 25, y: jy + 5, width: 9, height: 8}, svg);
    // аравача, троснлар, илгак блоки, строплар
    var trolley = mk("rect", {"class": "tc-solid", width: 18, height: 6}, svg);
    var c1 = mk("line", {"class": "tc-cab"}, svg), c2 = mk("line", {"class": "tc-cab"}, svg);
    var block = mk("rect", {"class": "tc-solid", width: 14, height: 11}, svg);
    var hook = mk("path", {"class": "tc-ln tc-hk"}, svg);
    var s1 = mk("line", {"class": "tc-sling"}, svg), s2 = mk("line", {"class": "tc-sling"}, svg);
    document.documentElement.appendChild(svg);
    crane = {svg: svg, mx: mx, jy: jy, L: L, slew: slew, trolley: trolley, c1: c1, c2: c2, block: block, hook: hook, s1: s1, s2: s2,
             s: crane ? crane.s : 0.06, d: crane ? crane.d : L * 0.3, hy: crane ? crane.hy : jy + 70, load: null};
    draw();
  }

  // ҳолатни чизиш: s — стрела буриш (0.06 — четдан кўринади, 1 — саҳифа томонга), d — аравача мачтадан масофаси, hy — илгак баландлиги
  function draw() {
    var c = crane; if (!c) return;
    c.slew.setAttribute("transform", "translate(" + c.mx + " 0) scale(" + c.s + " 1) translate(" + (-c.mx) + " 0)");
    var tx = c.mx - c.d * c.s;
    c.trolley.setAttribute("x", tx - 9); c.trolley.setAttribute("y", c.jy);
    c.c1.setAttribute("x1", tx - 3); c.c1.setAttribute("y1", c.jy + 6); c.c1.setAttribute("x2", tx - 3); c.c1.setAttribute("y2", c.hy);
    c.c2.setAttribute("x1", tx + 3); c.c2.setAttribute("y1", c.jy + 6); c.c2.setAttribute("x2", tx + 3); c.c2.setAttribute("y2", c.hy);
    c.block.setAttribute("x", tx - 7); c.block.setAttribute("y", c.hy);
    c.hook.setAttribute("d", "M" + tx + " " + (c.hy + 11) + "v6q0 7 -6 7q-4 0 -5 -4");
    var L = c.load, show = !!L && L.sling > 0;
    [c.s1, c.s2].forEach(function (s) { s.style.opacity = show ? L.sling : 0; });
    if (L) {
      c.s1.setAttribute("x1", tx); c.s1.setAttribute("y1", c.hy + 17); c.s1.setAttribute("x2", L.l + 6); c.s1.setAttribute("y2", L.t);
      c.s2.setAttribute("x1", tx); c.s2.setAttribute("y1", c.hy + 17); c.s2.setAttribute("x2", L.r - 6); c.s2.setAttribute("y2", L.t);
    }
  }

  // юкни (элементни) кўрсатилган жойга қўйиш: марказ x, юқори y, масштаб k
  function place(job, cx, top, k) {
    var el = job.el, nTop = job.docTop - window.scrollY, nLeft = job.left;
    var dx = cx - (nLeft + job.w / 2), dy = top - nTop;
    var z = zoom();
    el.style.transform = "translate(" + (dx / z).toFixed(1) + "px," + (dy / z).toFixed(1) + "px) scale(" + k.toFixed(3) + ")";
    crane.load = {l: cx - job.w * k / 2, r: cx + job.w * k / 2, t: top, sling: job.sling};
  }

  function tween(ms, fn) {
    return new Promise(function (done) {
      var t0 = null, speed = 1 + Math.min(queue.length, 4) * 0.9;  // навбат катта — тезроқ
      ms /= speed;
      (function f(now) {
        if (t0 === null) t0 = now;
        var t = clamp((now - t0) / ms);
        fn(t); draw();
        if (t < 1) requestAnimationFrame(f); else done();
      })(performance.now());
    });
  }

  function release(el) { el.classList.remove("asm-wait"); el.style.transform = ""; el.style.transition = ""; }

  function deliver(el) {
    var c = crane;
    el.style.transform = "";
    var r = el.getBoundingClientRect(), job = {el: el, docTop: r.top + window.scrollY, left: r.left, w: r.width, sling: 1};
    var nTop = r.top;
    if (!c || nTop < c.jy + 40 || nTop > window.innerHeight + 200) { release(el); return Promise.resolve(); }  // кран етолмайди — дарров жойига
    var hang = c.jy + 66, slingH = 46, d0 = c.L * 0.3, target = c.mx - (r.left + r.width / 2);
    target = Math.max(40, Math.min(c.L - 30, target));
    el.classList.remove("asm-wait"); el.classList.add("asm-fly");
    c.d = d0; c.hy = hang;
    // 1) стрела буриб келинади, юк узоқдан яқинлашади
    return tween(900, function (t) {
      var e = ease(t); c.s = lerp(0.06, 1, e);
      var tx = c.mx - c.d * c.s;
      place(job, tx, c.hy + 17 + slingH, lerp(0.45, 1, e));
      el.style.opacity = clamp(t * 3);
    }).then(function () {  // 2) аравача юк устига
      return tween(800, function (t) {
        c.d = lerp(d0, target, ease(t));
        place(job, c.mx - c.d, c.hy + 17 + slingH, 1);
      });
    }).then(function () {  // 3) тушириш
      return tween(1150, function (t) {
        var finalTop = job.docTop - window.scrollY, e = settle(t);
        var top = lerp(hang + 17 + slingH, finalTop, e);
        c.hy = top - slingH - 17;
        var nat = job.left + job.w / 2;
        place(job, lerp(c.mx - c.d, nat, ease(t)), top, 1);
      });
    }).then(function () {  // 4) строплар ечилади, илгак кўтарилади, стрела кетади
      el.style.transform = ""; el.classList.remove("asm-fly"); el.style.opacity = "";
      var from = c.hy;
      return tween(700, function (t) {
        job.sling = 1 - clamp(t * 2.5); c.load.sling = job.sling;
        c.hy = lerp(from, hang, ease(t));
      });
    }).then(function () {
      c.load = null;
      var dFrom = c.d;
      return tween(700, function (t) { var e = ease(t); c.s = lerp(1, 0.06, e); c.d = lerp(dFrom, d0, e); });
    });
  }

  function pump() {
    if (busy) return;
    var el = queue.shift();
    if (!el) return;
    busy = true;
    deliver(el).then(function () { busy = false; pump(); }, function () { release(el); busy = false; pump(); });
  }

  build();
  if (crane) {
    each(LOADS, function (el) { el.classList.add("asm-wait"); });
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        io.unobserve(e.target); queue.push(e.target); pump();
      });
    }, {threshold: 0.2});
    each(".asm-wait", function (el) { io.observe(el); });
    var rt = null;
    window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(function () {
      build();
      if (!crane) { each(".asm-wait, .asm-fly", release); queue.length = 0; }  // экран торайди — ҳаммаси жойида
    }, 150); });
  }
  root.classList.add("asm-on");
})();
