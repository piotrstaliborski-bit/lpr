/* Edytor treści strony – widoczny tylko po zalogowaniu administratora. */
(function () {
  "use strict";
  var TK = "lpr_admin_token", API = "/api";
  var MAP = {
    "glowna-tytul": "#v-glowna .hero h2",
    "glowna-opis": "#v-glowna .hero > p",
    "aktualnosci": "#v-aktualnosci .card.soon",
    "onas": '#v-onas [data-testid="onas-tresc"]',
    "onas-opis": "#v-onas .lpr-head p",
    "kontakt": "#v-kontakt .card.soon",
    "praca-opis": "#v-praca .lpr-head p",
    "praca-klauzula": '#v-praca [data-testid="praca-klauzula"]'
  };
  var active = false, snap = {}, saved = null, lastEd = null, verified = null, bar, launcher, tip;

  function $(s) { return document.querySelector(s); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function tok() { try { return localStorage.getItem(TK); } catch (e) { return null; } }
  function auth(o) { o = o || {}; o.headers = Object.assign({}, o.headers, { Authorization: "Bearer " + tok() }); return o; }
  function editables() { return [].slice.call(document.querySelectorAll("[data-edit]")); }
  function visibleEds() { return editables().filter(function (e) { return e.getClientRects().length > 0; }); }

  function toast(t) {
    if (!tip) { tip = document.createElement("div"); tip.className = "lprE-toast"; document.body.appendChild(tip); }
    tip.textContent = t; tip.classList.add("on");
    clearTimeout(tip._t); tip._t = setTimeout(function () { tip.classList.remove("on"); }, 3500);
  }

  /* ---- styl ---- */
  var css = document.createElement("style");
  css.textContent =
    "[data-edit] img{max-width:100%;height:auto;border-radius:6px}" +
    "[data-edit] a[href^='/api/pliki/']{font-weight:600}" +
    ".lpr-ed{outline:2px dashed var(--ye,#f2c200);outline-offset:4px;min-height:1.5em;cursor:text}" +
    ".lpr-ed:focus{outline-style:solid}" +
    ".lprE-launch{position:fixed;right:14px;bottom:calc(14px + env(safe-area-inset-bottom,0px));z-index:60;border:0;border-radius:999px;padding:12px 18px;font:600 15px Barlow,system-ui,sans-serif;background:var(--ye,#f2c200);color:#12162b;box-shadow:0 4px 14px rgba(0,0,0,.35);cursor:pointer}" +
    ".lprE-bar{position:fixed;left:0;right:0;bottom:0;z-index:61;padding:10px 12px calc(10px + env(safe-area-inset-bottom,0px));background:var(--sf,#fff);color:var(--tx,#12162b);border-top:2px solid var(--ye,#f2c200);box-shadow:0 -4px 16px rgba(0,0,0,.25);display:flex;flex-wrap:wrap;gap:6px;align-items:center;font:500 14px Barlow,system-ui,sans-serif}" +
    ".lprE-bar button{font:600 14px Barlow,system-ui,sans-serif;border:1px solid var(--ln,#d3d7e2);background:var(--bg,#eceef3);color:var(--tx,#12162b);border-radius:6px;padding:7px 11px;cursor:pointer}" +
    ".lprE-bar button.pri{background:var(--ok,#1d7a46);border-color:var(--ok,#1d7a46);color:#fff}" +
    ".lprE-bar button.dng{color:var(--re,#c8202c)}" +
    ".lprE-bar .sp{flex:1}" +
    ".lprE-toast{position:fixed;left:50%;transform:translateX(-50%);bottom:90px;z-index:70;background:#12162b;color:#fff;padding:10px 16px;border-radius:8px;font:500 14px Barlow,system-ui,sans-serif;opacity:0;pointer-events:none;transition:opacity .2s;max-width:90vw}" +
    ".lprE-toast.on{opacity:1}";
  document.head.appendChild(css);

  /* ---- oznaczenie i wczytanie zapisanych treści ---- */
  Object.keys(MAP).forEach(function (k) { var e = $(MAP[k]); if (e) e.setAttribute("data-edit", k); });
  fetch(API + "/tresci").then(function (r) { return r.ok ? r.json() : {}; }).then(function (m) {
    Object.keys(m).forEach(function (k) {
      var e = $('[data-edit="' + k + '"]');
      if (e && !active) { e.innerHTML = m[k]; e.classList.remove("soon"); }
    });
  }).catch(function () {});

  /* ---- zaznaczenie (żeby przyciski nie gubiły kursora) ---- */
  document.addEventListener("selectionchange", function () {
    var s = window.getSelection();
    if (!active || !s.rangeCount) return;
    var n = s.anchorNode, el = n && (n.nodeType === 1 ? n : n.parentElement);
    var ed = el && el.closest && el.closest(".lpr-ed");
    if (ed) { saved = s.getRangeAt(0).cloneRange(); lastEd = ed; }
  });
  function restore() {
    var ed = lastEd || visibleEds()[0];
    if (!ed) return false;
    ed.focus();
    var s = window.getSelection(); s.removeAllRanges();
    if (saved) s.addRange(saved);
    else { var r = document.createRange(); r.selectNodeContents(ed); r.collapse(false); s.addRange(r); }
    return true;
  }
  function cmd(name, val) { restore(); document.execCommand(name, false, val || null); }

  /* ---- upload pliku/obrazu ---- */
  function upload(file, isImage) {
    if (!file) return;
    var fd = new FormData(); fd.append("file", file);
    toast("Wgrywanie…");
    fetch(API + "/pliki", auth({ method: "POST", body: fd })).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Błąd wgrywania"); return j; });
    }).then(function (j) {
      restore();
      if (isImage && j.is_image) document.execCommand("insertImage", false, j.url);
      else document.execCommand("insertHTML", false, '<a href="' + esc(j.url) + '">' + esc(j.name) + "</a>&nbsp;");
      toast("Dodano: " + j.name);
    }).catch(function (e) { toast(e.message || "Błąd wgrywania"); });
  }
  function picker(accept, isImage) {
    var i = document.createElement("input"); i.type = "file"; if (accept) i.accept = accept;
    i.onchange = function () { upload(i.files[0], isImage); };
    i.click();
  }

  /* ---- tryb edycji ---- */
  function startEdit() {
    var list = visibleEds();
    if (!list.length) { toast("Na tej podstronie nie ma treści do edycji."); return; }
    active = true; saved = null; lastEd = list[0]; snap = {};
    editables().forEach(function (e) { snap[e.getAttribute("data-edit")] = e.innerHTML; });
    list.forEach(function (e) { e.contentEditable = "true"; e.classList.add("lpr-ed"); });
    list[0].focus();
    launcher.hidden = true; buildBar();
  }
  function stopEdit() {
    active = false;
    editables().forEach(function (e) { e.removeAttribute("contenteditable"); e.classList.remove("lpr-ed"); });
    if (bar) { bar.remove(); bar = null; }
    launcher.hidden = false;
  }
  function cancel() {
    if (!confirm("Odrzucić zmiany?")) return;
    editables().forEach(function (e) { var k = e.getAttribute("data-edit"); if (k in snap) e.innerHTML = snap[k]; });
    stopEdit();
  }
  function save() {
    var jobs = [], changed = 0;
    editables().forEach(function (e) {
      var k = e.getAttribute("data-edit");
      if (e.innerHTML === snap[k]) return;
      changed++;
      jobs.push(fetch(API + "/tresci/" + k, auth({ method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ html: e.innerHTML }) }))
        .then(function (r) {
          if (r.status === 401) throw new Error("Sesja wygasła – zaloguj się ponownie (Oferta pracy → Panel administratora)");
          if (!r.ok) throw new Error("Nie udało się zapisać");
          return r.json();
        }).then(function (j) { e.innerHTML = j.html; e.classList.remove("soon"); }));
    });
    if (!changed) { stopEdit(); toast("Brak zmian"); return; }
    Promise.all(jobs).then(function () { stopEdit(); toast("Zapisano ✓"); })
      .catch(function (e) { toast(e.message || "Błąd zapisu"); });
  }
  function resetDefault() {
    var list = visibleEds();
    if (!confirm("Przywrócić pierwotną treść na tej podstronie? Twoje zmiany zostaną usunięte.")) return;
    Promise.all(list.map(function (e) {
      return fetch(API + "/tresci/" + e.getAttribute("data-edit"), auth({ method: "DELETE" }));
    })).then(function () { location.reload(); });
  }
  function link() {
    restore();
    var u = prompt("Adres linku (https://…, mailto:… lub tel:…):", "https://");
    if (!u) return;
    if (!/^(https?:\/\/|mailto:|tel:|#)/i.test(u.trim())) { toast("Link musi zaczynać się od https://, mailto: lub tel:"); return; }
    document.execCommand("createLink", false, u.trim());
  }

  function buildBar() {
    bar = document.createElement("div"); bar.className = "lprE-bar";
    var defs = [
      ["B", function () { cmd("bold"); }, "Pogrubienie"],
      ["I", function () { cmd("italic"); }, "Kursywa"],
      ["Nagłówek", function () { cmd("formatBlock", "<h3>"); }, "Nagłówek"],
      ["Akapit", function () { cmd("formatBlock", "<p>"); }, "Zwykły akapit"],
      ["• Lista", function () { cmd("insertUnorderedList"); }, "Lista punktowana"],
      ["1. Lista", function () { cmd("insertOrderedList"); }, "Lista numerowana"],
      ["Link", link, "Wstaw link"],
      ["Usuń link", function () { cmd("unlink"); }, "Usuń link"],
      ["Obraz", function () { picker("image/jpeg,image/png,image/gif,image/webp", true); }, "Wstaw obraz"],
      ["Plik", function () { picker("", false); }, "Wstaw plik do pobrania"]
    ];
    defs.forEach(function (d) {
      var b = document.createElement("button"); b.type = "button"; b.textContent = d[0]; b.title = d[2];
      b.addEventListener("mousedown", function (e) { e.preventDefault(); }); /* nie zabieraj fokusu z tekstu */
      b.addEventListener("click", d[1]); bar.appendChild(b);
    });
    var sp = document.createElement("span"); sp.className = "sp"; bar.appendChild(sp);
    [["Domyślne", resetDefault, "dng"], ["Anuluj", cancel, ""], ["Zapisz", save, "pri"]].forEach(function (d) {
      var b = document.createElement("button"); b.type = "button"; b.textContent = d[0]; b.className = d[2];
      b.addEventListener("click", d[1]); bar.appendChild(b);
    });
    document.body.appendChild(bar);
  }

  /* ---- przycisk „Edytuj stronę” po zalogowaniu ---- */
  launcher = document.createElement("button");
  launcher.className = "lprE-launch"; launcher.type = "button"; launcher.hidden = true;
  launcher.textContent = "✎ Edytuj stronę";
  launcher.onclick = startEdit;
  document.body.appendChild(launcher);

  function tick() {
    var t = tok();
    if (!t) { verified = null; launcher.hidden = true; if (active) stopEdit(); return; }
    if (verified && verified.indexOf("pending:") === 0) return;
    if (verified !== t) {
      verified = "pending:" + t;
      fetch(API + "/auth/me", auth()).then(function (r) { if (!r.ok) throw 0; verified = t; })
        .catch(function () { verified = null; try { localStorage.removeItem(TK); } catch (e) {} });
      return;
    }
    if (!active) launcher.hidden = visibleEds().length === 0;
  }
  setInterval(tick, 1000); tick();
  window.addEventListener("beforeunload", function (e) { if (active) { e.preventDefault(); e.returnValue = ""; } });
})();
