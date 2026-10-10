(function () {
  document.documentElement.classList.add("js");

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function applyHljsTheme(theme) {
    document.querySelectorAll("link[data-hljs-theme]").forEach(function (link) {
      link.disabled = link.getAttribute("data-hljs-theme") !== theme;
    });
  }

  function highlightCode() {
    if (window.hljs) {
      document.querySelectorAll("pre code[data-lang]").forEach(function (block) {
        try {
          block.removeAttribute("data-highlighted");
          window.hljs.highlightElement(block);
        } catch (e) { /* leave plain */ }
      });
    }
  }

  function renderMermaid(theme) {
    if (!window.mermaid) { return; }
    try {
      window.mermaid.initialize({
        startOnLoad: false,
        theme: theme === "dark" ? "dark" : "default",
        securityLevel: "strict",
      });
      document.querySelectorAll(".mermaid").forEach(function (el) {
        if (!el.hasAttribute("data-src")) { el.setAttribute("data-src", el.textContent); }
        el.removeAttribute("data-processed");
        el.textContent = el.getAttribute("data-src");
      });
      var p = window.mermaid.run({ querySelector: ".mermaid" });
      if (p && p.catch) { p.catch(function () { /* fallback text stays visible */ }); }
    } catch (e) { /* fallback text stays visible */ }
  }

  applyHljsTheme(currentTheme());
  highlightCode();
  renderMermaid(currentTheme());

  var toggleBtn = document.getElementById("theme-toggle");
  function updateToggleLabel(theme) {
    if (!toggleBtn) { return; }
    var label = toggleBtn.querySelector(".theme-toggle-label");
    if (theme === "dark") {
      if (label) { label.textContent = "Dark"; }
    } else {
      if (label) { label.textContent = "Light"; }
    }
  }
  updateToggleLabel(currentTheme());

  if (toggleBtn) {
    toggleBtn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { window.name = "course-theme:" + next; } catch (e) { /* window.name unavailable */ }
      try { window.localStorage.setItem("course-theme", next); } catch (e) { /* no storage available */ }
      updateToggleLabel(next);
      applyHljsTheme(next);
      renderMermaid(next);
    });
  }

  var toggles = document.querySelectorAll(".toc-module > summary");
  toggles.forEach(function (s) { s.addEventListener("click", function () {}); });

  var searchInput = document.getElementById("sidebar-search-input");
  if (searchInput) {
    searchInput.addEventListener("input", function () {
      var q = searchInput.value.trim().toLowerCase();
      document.querySelectorAll("nav.sidebar details.toc-module").forEach(function (mod) {
        var items = mod.querySelectorAll("ul.module-lessons > li");
        var anyVisible = false;
        items.forEach(function (li) {
          var title = li.getAttribute("data-lesson-title") || "";
          var match = !q || title.indexOf(q) !== -1;
          li.classList.toggle("lesson-hidden", !match);
          if (match) { anyVisible = true; }
        });
        mod.classList.toggle("module-hidden", !anyVisible);
        if (q && anyVisible) { mod.setAttribute("open", ""); }
      });
    });
  }
})();
