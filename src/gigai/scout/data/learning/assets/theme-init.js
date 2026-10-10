(function () {
  var theme = "light";
  var NAME_PREFIX = "course-theme:";
  try {
    if (window.name && window.name.indexOf(NAME_PREFIX) === 0) {
      var fromName = window.name.slice(NAME_PREFIX.length);
      if (fromName === "dark" || fromName === "light") { theme = fromName; }
    }
  } catch (e) { /* window.name unavailable; stay light */ }
  try {
    var saved = window.localStorage.getItem("course-theme");
    if (saved === "dark" || saved === "light") { theme = saved; }
  } catch (e) { /* no storage available */ }
  document.documentElement.setAttribute("data-theme", theme);
})();
