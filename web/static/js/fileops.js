/* Битта файл / смета устида амаллар (папка дарахти, жуфтлик карталари, объект саҳифаси):
   [data-fop="replace"] — тузатилган файлни шу ўрнига юклаш; [data-fop="undo"] — олдинги нусхани қайтариш;
   [data-fop="reprice"] — сметадаги қўлда ўзгаришларни бекор қилиб, бошидан нархлаш.
   data-url — POST манзили, data-name — файл/смета номи. Амалдан кейин шу саҳифага қайтилади (next). */
(function () {
  if (window.narxFileOps) return;
  function token() {
    var m = document.querySelector('meta[name="csrf-token"]');
    if (m) return m.content;
    var i = document.querySelector("[name=csrfmiddlewaretoken]");
    return i ? i.value : "";
  }
  function post(url, extra, file, progress) {
    var f = document.createElement("form");
    f.method = "post"; f.action = url; f.hidden = true;
    if (file) f.enctype = "multipart/form-data";
    var add = function (k, v) { var i = document.createElement("input"); i.type = "hidden"; i.name = k; i.value = v; f.appendChild(i); };
    add("csrfmiddlewaretoken", token());
    add("next", location.pathname + location.search);
    Object.keys(extra || {}).forEach(function (k) { add(k, extra[k]); });
    if (file) f.appendChild(file);
    document.body.appendChild(f);
    if (progress && window.narxProgress) { try { narxProgress.start(progress).auto(2); } catch (e) {} }
    f.submit();
  }
  var ops = {
    replace: function (b) {
      var inp = document.createElement("input");
      inp.type = "file"; inp.name = "file"; inp.accept = ".xls,.xlsx,.xlsm"; inp.hidden = true;
      inp.onchange = function () {
        if (!inp.files.length) return;
        if (!confirm("«" + b.dataset.name + "» ўрнига «" + inp.files[0].name + "» юклансинми?\n\n" +
                     "Фақат шу файл ўзгаради. Нархланган бўлса — фақат шу файлга боғлиқ сметалар қайта ҳисобланади, " +
                     "қўлда ўзгаришлар сақланади. Кейин «Ортга» билан қайтариш мумкин.")) return;
        post(b.dataset.url, null, inp, {title: "Файл алмаштирилмоқда", steps: ["Файл юкланмоқда", "Листлар ўқилмоқда", "Сметалар янгиланмоқда"]});
      };
      document.body.appendChild(inp);
      inp.click();
    },
    undo: function (b) {
      if (!confirm("«" + b.dataset.name + "» — охирги алмаштириш бекор қилиниб, олдинги нусха қайтарилсинми?")) return;
      post(b.dataset.url, null, null, {title: "Олдинги нусха қайтарилмоқда", steps: ["Файл тикланмоқда", "Сметалар янгиланмоқда"]});
    },
    reprice: function (b) {
      if (!confirm("«" + b.dataset.name + "» сметасидаги ҳамма қўлда ўзгаришлар (нарх, таклиф, L/M, белгилар) бекор қилинсинми?\n\nСмета бошидан нархланади. Фоизлар сақланади.")) return;
      post(b.dataset.url, {confirm: "1"});
    },
  };
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-fop]");
    if (!b || !ops[b.dataset.fop]) return;
    e.preventDefault(); e.stopPropagation();
    ops[b.dataset.fop](b);
  }, true);
  window.narxFileOps = ops;
})();
