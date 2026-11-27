// Applies the saved theme before the page is drawn, so it doesn't flash
(function () {
  try {
    var theme = localStorage.getItem("cgp-theme");
    if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  } catch (e) { /* storage unavailable: follow the system theme */ }
})();
