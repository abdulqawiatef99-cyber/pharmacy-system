/* Abdul Qawi Pharmacy site. All content comes from data/site.json.
   No uploads, no forms, no cookies, no trackers. localStorage only remembers language and theme. */
(function () {
  "use strict";
  var D = null;
  var lang = "ar";

  function store(k, v) {
    try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; }
  }
  function isTodo(v) {
    if (v === undefined || v === null || v === "" || v === D.todo_marker) return true;
    if (Array.isArray(v)) return v.length === 0 || v.indexOf(D.todo_marker) !== -1;
    return false;
  }
  function t(key) {
    var s = D.strings[lang][key];
    return s === undefined ? D.strings.ar[key] : s;
  }
  function p(path) {
    var cur = D;
    path.split(".").forEach(function (part) { cur = cur == null ? undefined : cur[part]; });
    return cur;
  }
  function local(field) { return p("pharmacy." + field + "_" + lang); }
  function $(id) { return document.getElementById(id); }
  function placeholder(el) {
    el.textContent = t("placeholder");
    el.classList.add("todo");
  }
  function wa(text) { return p("pharmacy.whatsapp_link") + "?text=" + encodeURIComponent(text); }
  function li(ul, text, cls) {
    var x = document.createElement("li");
    x.textContent = text;
    if (cls) x.className = cls;
    ul.appendChild(x);
  }

  function render() {
    var root = document.documentElement;
    root.lang = lang;
    root.dir = lang === "ar" ? "rtl" : "ltr";
    document.title = t("meta.title");
    var md = document.querySelector('meta[name="description"]');
    if (md) md.setAttribute("content", t("meta.description"));

    document.querySelectorAll("[data-t]").forEach(function (el) { el.textContent = t(el.getAttribute("data-t")); });
    document.querySelectorAll("[data-t-label]").forEach(function (el) {
      var s = t(el.getAttribute("data-t-label"));
      el.setAttribute("aria-label", s);
      el.setAttribute("title", s);
    });
    document.querySelectorAll("[data-p]").forEach(function (el) { el.textContent = local(el.getAttribute("data-p")); });

    $("order-btn").href = wa(t("wa.order_text"));
    $("rx-btn").href = wa(t("wa.rx_text"));
    $("wa-link").href = p("pharmacy.whatsapp_link");
    $("wa-link").textContent = p("pharmacy.whatsapp_display");
    $("mobile-link").href = "tel:" + p("pharmacy.mobile_e164");
    $("mobile-link").textContent = p("pharmacy.whatsapp_display");
    $("map-link").href = p("pharmacy.maps_link");

    var landE164 = p("pharmacy.landline_e164");
    var landEl = $("landline");
    landEl.textContent = "";
    landEl.classList.remove("todo");
    if (isTodo(landE164) || isTodo(p("pharmacy.landline_display"))) {
      placeholder(landEl);
      $("call-btn").href = "tel:" + p("pharmacy.mobile_e164");
    } else {
      var a = document.createElement("a");
      a.href = "tel:" + landE164;
      a.dir = "ltr";
      a.textContent = p("pharmacy.landline_display");
      landEl.appendChild(a);
      $("call-btn").href = "tel:" + landE164;
    }

    var hours = $("hours");
    hours.textContent = "";
    var hv = p("hours");
    if (isTodo(hv)) li(hours, t("placeholder"), "todo");
    else hv.forEach(function (h) {
      var names = h.days.map(function (d) { return t("day." + d); }).join(lang === "ar" ? "، " : ", ");
      li(hours, names + ": " + h.opens + " – " + h.closes);
    });

    var sv = $("services");
    sv.textContent = "";
    var sl = p("services");
    if (isTodo(sl)) li(sv, t("placeholder"), "todo");
    else sl.forEach(function (s) { li(sv, s[lang] || s.ar); });

    var area = p("delivery.area_" + lang);
    var fee = p("delivery.fee_egp");
    var aEl = $("delivery-area"), fEl = $("delivery-fee");
    aEl.classList.remove("todo"); fEl.classList.remove("todo");
    if (isTodo(area)) placeholder(aEl); else aEl.textContent = area;
    if (isTodo(fee)) placeholder(fEl); else fEl.textContent = fee + " " + t("delivery.fee_unit");

    $("returns-body").textContent = t("returns.body").replace("{days}", p("returns.days"));

    var soc = $("social");
    soc.textContent = "";
    var links = p("pharmacy.social");
    var any = false;
    Object.keys(links).forEach(function (k) {
      if (isTodo(links[k])) return;
      if (any) soc.appendChild(document.createTextNode(" · "));
      var a = document.createElement("a");
      a.href = links[k]; a.rel = "noopener"; a.target = "_blank";
      a.textContent = k.charAt(0).toUpperCase() + k.slice(1);
      soc.appendChild(a);
      any = true;
    });
    if (!any) placeholder(soc);

    $("year").textContent = String(new Date().getFullYear());
  }

  function applyBrand() {
    var c = p("pharmacy.colors");
    var hex = /^#[0-9a-fA-F]{6}$/;
    if (hex.test(c.primary)) document.documentElement.style.setProperty("--brand", c.primary);
    if (hex.test(c.accent)) document.documentElement.style.setProperty("--brand-2", c.accent);
    var logo = p("pharmacy.logo");
    if (!isTodo(logo)) {
      var img = document.createElement("img");
      img.src = logo; img.alt = ""; img.width = 40; img.height = 40;
      var slot = $("logo");
      slot.textContent = "";
      slot.appendChild(img);
    }
  }

  function setTheme(mode) {
    if (mode === "light" || mode === "dark") document.documentElement.setAttribute("data-theme", mode);
    else document.documentElement.removeAttribute("data-theme");
  }

  function init(data) {
    D = data;
    var saved = store("lang");
    lang = saved === "en" ? "en" : "ar";
    setTheme(store("theme"));
    applyBrand();
    render();
    $("lang-btn").addEventListener("click", function () {
      lang = lang === "ar" ? "en" : "ar";
      store("lang", lang);
      render();
    });
    $("theme-btn").addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme");
      if (!cur) cur = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      var next = cur === "dark" ? "light" : "dark";
      setTheme(next);
      store("theme", next);
    });
  }

  setTheme(store("theme"));
  document.addEventListener("DOMContentLoaded", function () {
    fetch("data/site.json", { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(init)
      .catch(function () { document.documentElement.classList.add("no-data"); });
  });
})();
