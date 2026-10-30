/* Қурилиш саҳнаси: SVG элементлари data-fx бўйича битта «тайёрлик» (p = 0…1) га қараб чизилади.

   data-fx="draw"   — чизиқ бошидан охиригача чизилади
   data-fx="rise"   — пастдан юқорига ўсади (устун, пойдевор)
   data-fx="shrink" — пастга ботиб йўқолади (бульдозер текислаган тепалик)
   data-fx="fade" / "out" — пайдо бўлади / йўқолади
   data-fx="move"   — data-x0 → data-x1 (data-y баландликда) юради; ичидаги .wheel лар айланади
   data-a, data-b   — шу эффект p нинг қайси оралиғида бажарилади

   JavaScript ўчиқ бўлса ёки саҳна ишга тушмаса — SVG тайёр бино бўлиб кўринади (ҳеч нарса яширинмайди). */
(function () {
  "use strict";

  function clamp(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function ease(t) { return t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }

  function Scene(svg) {
    this.items = [].map.call(svg.querySelectorAll("[data-fx]"), function (el) {
      var it = {el: el, fx: el.dataset.fx, a: parseFloat(el.dataset.a), b: parseFloat(el.dataset.b), last: -1};
      if (it.fx === "draw") {
        try { it.len = el.getTotalLength(); } catch (e) { it.len = 0; }
        el.style.strokeDasharray = it.len + " " + it.len;
      } else if (it.fx === "rise" || it.fx === "shrink") {
        el.style.transformBox = "fill-box";
        el.style.transformOrigin = "50% 100%";
      } else if (it.fx === "move") {
        it.x0 = parseFloat(el.dataset.x0); it.x1 = parseFloat(el.dataset.x1); it.y = parseFloat(el.dataset.y || 0);
        it.wheels = [].map.call(el.querySelectorAll(".wheel"), function (w) {
          w.style.transformBox = "fill-box"; w.style.transformOrigin = "50% 50%";
          return {el: w, r: parseFloat(w.dataset.r || 6)};
        });
      }
      return it;
    });
  }

  Scene.prototype.set = function (p) {
    for (var i = 0; i < this.items.length; i++) {
      var it = this.items[i], l = clamp((p - it.a) / (it.b - it.a));
      if (l === it.last) continue;
      it.last = l;
      var el = it.el;
      switch (it.fx) {
        case "draw":
          el.style.strokeDashoffset = it.len * (1 - l);
          el.style.visibility = l > 0 ? "visible" : "hidden";
          break;
        case "rise": el.style.transform = "scaleY(" + l + ")"; el.style.visibility = l > 0 ? "visible" : "hidden"; break;
        case "shrink": el.style.transform = "scaleY(" + (1 - l) + ")"; break;
        case "fade": el.style.opacity = l; break;
        case "out": el.style.opacity = 1 - l; break;
        case "move":
          var x = it.x0 + (it.x1 - it.x0) * l;
          el.setAttribute("transform", "translate(" + x + " " + it.y + ")");
          for (var k = 0; k < it.wheels.length; k++) {
            it.wheels[k].el.style.transform = "rotate(" + ((x - it.x0) / it.wheels[k].r * 57.2958) + "deg)";
          }
          break;
      }
    }
  };

  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Ўзи ўйнайди (кириш саҳифалари): duration мс давомида 0 → 1 */
  function play(svg, duration, onDone) {
    var s = new Scene(svg);
    if (reduce) { s.set(1); if (onDone) onDone(); return; }
    s.set(0);
    var t0 = null;
    function frame(now) {
      if (t0 === null) t0 = now;
      var t = clamp((now - t0) / duration);
      s.set(ease(t));
      if (t < 1) requestAnimationFrame(frame); else if (onDone) onDone();
    }
    requestAnimationFrame(frame);
  }

  /* Scroll билан (landing): track экрандан ўтган сари from → 1; onProgress(p, sp) — p саҳна, sp айлантириш (0…1).
     from > 0 бўлса, саҳифа очилганда саҳна intro мс да 0 → from ўйнайди (масалан геодезия қозиқлари қоқилади). */
  function scroll(svg, track, onProgress, opts) {
    opts = opts || {};
    var s = new Scene(svg), from = opts.from || 0, ticking = false, lastP = -1, intro = null;
    function progress() {
      var r = track.getBoundingClientRect(), span = r.height - window.innerHeight;
      var sp = span > 0 ? clamp(-r.top / span) : 1;
      return {sp: sp, p: from + (1 - from) * sp};
    }
    function update() {
      ticking = false;
      var g = progress();
      if (intro !== null) { if (g.sp === 0) return; cancelAnimationFrame(intro); intro = null; }  // айлантирди — intro тўхтайди
      if (Math.abs(g.p - lastP) < 0.0005) return;
      lastP = g.p;
      s.set(g.p);
      if (onProgress) onProgress(g.p, g.sp);
    }
    function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(update); } }
    window.addEventListener("scroll", onScroll, {passive: true});
    window.addEventListener("resize", onScroll);
    var g0 = progress();
    if (from > 0 && g0.sp === 0 && !reduce && opts.intro) {
      s.set(0);
      if (onProgress) onProgress(from, 0);
      var t0 = null;
      intro = requestAnimationFrame(function step(now) {
        if (t0 === null) t0 = now;
        var t = clamp((now - t0) / opts.intro);
        s.set(from * ease(t));
        if (t < 1) intro = requestAnimationFrame(step); else { intro = null; lastP = from; }
      });
    } else {
      update();
    }
  }

  // scene(svg).set(p) — исталган лаҳзани қўлда кўрсатиш (текширув учун)
  window.BuildScene = {play: play, scroll: scroll, scene: function (svg) { return new Scene(svg); }};
})();
