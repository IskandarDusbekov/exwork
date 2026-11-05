/* Кириш саҳифалари: паролни кўрсатиш / яшириш тугмаси ва янги пароль учун узунлик кўрсаткичи.
   input[type=password] — кўз тугмаси; data-meter="10" — «7 / 10 белги» ва ранг; data-match="id_password1" — мослиги. */
(function () {
  var EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.6 10.6 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.2M6.5 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7c1.8 0 3.4-.5 4.7-1.3"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

  function toggle(input) {
    var wrap = document.createElement("div");
    wrap.className = "pw-wrap";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-eye";
    btn.setAttribute("aria-label", "Паролни кўрсатиш");
    btn.setAttribute("aria-pressed", "false");
    btn.innerHTML = EYE;
    btn.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.innerHTML = show ? EYE_OFF : EYE;
      btn.setAttribute("aria-pressed", show ? "true" : "false");
      btn.setAttribute("aria-label", show ? "Паролни яшириш" : "Паролни кўрсатиш");
      input.focus();
    });
    wrap.appendChild(btn);
  }

  function strength(v) {
    var kinds = [/[a-zа-яёўқғҳ]/, /[A-ZА-ЯЁЎҚҒҲ]/, /\d/, /[^\wа-яёўқғҳА-ЯЁЎҚҒҲ]/].filter(function (r) { return r.test(v); }).length;
    if (/^\d+$/.test(v)) return [1, "Фақат рақам бўлмасин"];
    var score = kinds + (v.length >= 14 ? 1 : 0) + (v.length >= 18 ? 1 : 0);
    return score >= 4 ? [3, "Кучли"] : score >= 3 ? [2, "Яхши"] : [1, "Ўртача — ҳарф, рақам ва белги аралаштиринг"];
  }

  function meter(input) {
    var min = parseInt(input.dataset.meter, 10) || 10;
    var box = document.createElement("div");
    box.className = "pw-meter";
    box.setAttribute("aria-live", "polite");
    box.innerHTML = '<div class="pw-bar"><span></span></div><div class="pw-txt"><b></b><span></span></div>';
    var field = input.closest(".f") || input.parentNode;
    var hint = field.querySelector(".f-h");
    field.insertBefore(box, hint || null);
    var bar = box.querySelector(".pw-bar span"), count = box.querySelector("b"), note = box.querySelector(".pw-txt span");
    function upd() {
      var n = input.value.length;
      box.classList.toggle("is-empty", n === 0);
      if (n < min) {
        box.dataset.level = n ? "0" : "";
        bar.style.width = Math.round(n * 100 / min) + "%";
        count.textContent = n + " / " + min + " белги";
        note.textContent = n ? "яна " + (min - n) + " та" : "";
      } else {
        var s = strength(input.value);
        box.dataset.level = String(s[0]);
        bar.style.width = "100%";
        count.textContent = n + " белги ✓";
        note.textContent = s[1];
      }
    }
    input.addEventListener("input", upd);
    upd();
  }

  function match(input) {
    var other = document.getElementById(input.dataset.match);
    if (!other) return;
    var out = document.createElement("p");
    out.className = "pw-match";
    out.setAttribute("aria-live", "polite");
    (input.closest(".f") || input.parentNode).appendChild(out);
    function upd() {
      if (!input.value) { out.textContent = ""; out.dataset.ok = ""; return; }
      var ok = input.value === other.value;
      out.dataset.ok = ok ? "1" : "0";
      out.textContent = ok ? "Пароллар мос ✓" : "Пароллар ҳали мос эмас";
    }
    input.addEventListener("input", upd);
    other.addEventListener("input", upd);
    upd();
  }

  document.querySelectorAll('input[type="password"]').forEach(function (input) {
    toggle(input);
    if (input.dataset.meter) meter(input);
    if (input.dataset.match) match(input);
  });
})();
