/* Битта файл / смета устида амаллар (папка дарахти, жуфтлик карталари, объект саҳифаси):
   [data-fop="replace"] — тузатилган файлни шу ўрнига юклаш; [data-fop="undo"] — олдинги нусхани қайтариш;
   [data-fop="reprice"] — сметадаги қўлда ўзгаришларни бекор қилиб, бошидан нархлаш.
   data-url — POST манзили, data-name — файл/смета номи.
   Тасдиқлаш — саҳифа ичидаги ойна (narxDialog), браузерники эмас. Алмаштириш ва қайтариш — фонда (XHR):
   жараён ойнаси (narxProgress) фоиз ва қадамларни кўрсатади, браузер саҳифани қайта юкламайди.
   Папка саҳифасида фақат шу қисм янгиланади (window.narxProjectRefresh), бошқа саҳифаларда — саҳифа янгиланади. */
(function () {
  if (window.narxFileOps) return;
  var gettext = window.gettext || function (s) { return s; };  // таржима (/jsi18n/)
  function fmt(s, args) { var i = 0; return s.replace(/%s/g, function () { return args[i++]; }); }
  function token() {
    var m = document.querySelector('meta[name="csrf-token"]');
    if (m) return m.content;
    var i = document.querySelector("[name=csrfmiddlewaretoken]");
    return i ? i.value : "";
  }
  function ask(text, opts) {
    if (window.narxDialog) return window.narxDialog.confirm(text, opts);
    return Promise.resolve(window.confirm((opts && opts.title ? opts.title + "\n\n" : "") + text));
  }
  function fail(msg) {
    if (window.narxDialog) window.narxDialog.alert(msg, {title: gettext("Бажарилмади"), danger: true});
    else window.alert(msg);
  }
  // оддий форма (reprice — смета саҳифасига ўтади, у ерда ўзи кўрсатади)
  function post(url, extra) {
    var f = document.createElement("form");
    f.method = "post"; f.action = url; f.hidden = true;
    var add = function (k, v) { var i = document.createElement("input"); i.type = "hidden"; i.name = k; i.value = v; f.appendChild(i); };
    add("csrfmiddlewaretoken", token());
    add("next", location.pathname + location.search);
    Object.keys(extra || {}).forEach(function (k) { add(k, extra[k]); });
    document.body.appendChild(f);
    f.submit();
  }
  // фонда юбориш: юклаш фоизи → сервер ишлаши → саҳифа ичида янгилаш
  function send(url, file, progress) {
    var fd = new FormData();
    fd.append("csrfmiddlewaretoken", token());
    fd.append("next", location.pathname + location.search);
    if (file) fd.append("file", file, file.name);
    var np = window.narxProgress ? window.narxProgress.start(progress) : null;
    var xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-Requested-With", "fetch");
    xhr.setRequestHeader("X-CSRFToken", token());
    if (file && xhr.upload) {
      xhr.upload.onprogress = function (e) {
        if (!np || !e.lengthComputable) return;
        var p = Math.round(100 * e.loaded / e.total);
        np.note(p + "%").percent(Math.round(p * 0.4));
        if (p >= 100) np.step(1).percent(null).auto(2);
      };
    } else if (np) np.auto(2);
    function failed(msg) {
      if (np) np.fail(msg);
      fail(msg);
    }
    xhr.onload = function () {
      var d = {};
      try { d = JSON.parse(xhr.responseText); } catch (e) { d = {error: fmt(gettext("Сервер хатоси (%s)"), [xhr.status])}; }
      if (!d.ok) return failed(d.error || gettext("Хато"));
      if (window.narxProjectRefresh) {
        if (np) np.step(progress.steps.length - 1);
        window.narxProjectRefresh().then(function () {
          if (np) np.done(d.message || gettext("Тайёр"));
        }, function () { location.reload(); });
      } else {
        if (np) np.done(d.message || gettext("Тайёр"));
        location.reload();
      }
    };
    xhr.onerror = function () { failed(gettext("Интернет узилди — қайта уриниб кўринг")); };
    xhr.send(fd);
  }
  function pick(then) {
    var inp = document.createElement("input");
    inp.type = "file"; inp.accept = ".xls,.xlsx,.xlsm"; inp.hidden = true;
    inp.onchange = function () { if (inp.files.length) then(inp.files[0]); inp.remove(); };
    document.body.appendChild(inp);
    inp.click();
  }
  var REPLACE_STEPS = {title: gettext("Файл алмаштирилмоқда"), steps: [gettext("Файл юкланмоқда"), gettext("Листлар ўқилмоқда"), gettext("Сметалар янгиланмоқда"), gettext("Саҳифа янгиланмоқда")]};
  var ops = {
    replace: function (b) {
      pick(function (file) { ops.replaceFile(b.dataset.url, b.dataset.name, file); });
    },
    // батч саҳифаси ҳам шуни ишлатади (objFile.replace)
    replaceFile: function (url, name, file, note) {
      ask(note || gettext("Фақат шу файл ўзгаради. Нархланган бўлса — фақат шу файлга боғлиқ сметалар қайта ҳисобланади, қўлда ўзгаришлар сақланади. Кейин «Ортга» билан қайтариш мумкин."),
          {title: fmt(gettext("«%s» ўрнига «%s» юклансинми?"), [name, file.name]), ok: gettext("Ҳа, юклаш"), cancel: gettext("Бекор қилиш")}).then(function (yes) {
        if (yes) send(url, file, REPLACE_STEPS);
      });
    },
    undo: function (b) {
      ask(gettext("Охирги алмаштириш бекор қилинади, олдинги нусха қайтади. Боғлиқ сметалар қайта ҳисобланади."),
          {title: fmt(gettext("«%s» — олдинги нусха қайтарилсинми?"), [b.dataset.name]), ok: gettext("Ҳа, қайтариш"), cancel: gettext("Йўқ")}).then(function (yes) {
        if (yes) send(b.dataset.url, null, {title: gettext("Олдинги нусха қайтарилмоқда"), steps: [gettext("Файл тикланмоқда"), gettext("Сметалар янгиланмоқда"), gettext("Саҳифа янгиланмоқда")]});
      });
    },
    reprice: function (b) {
      ask(gettext("Ҳамма қўлда ўзгаришлар (нарх, таклиф, L/M, белгилар) бекор қилинади. Смета бошидан нархланади. Фоизлар сақланади."),
          {title: fmt(gettext("«%s» бошидан нархлансинми?"), [b.dataset.name]), ok: gettext("Ҳа, бошидан"), cancel: gettext("Йўқ"), danger: true}).then(function (yes) {
        if (yes) post(b.dataset.url, {confirm: "1"});
      });
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
