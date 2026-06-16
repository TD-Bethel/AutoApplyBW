// Applies the saved theme before the page paints (avoids a light-mode flash).
// Loaded in <head>; CSP-safe as an external same-origin script.
(function () {
  try {
    if (localStorage.getItem("theme") === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  } catch (e) {}
})();
