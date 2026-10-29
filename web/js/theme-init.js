// Applies the saved theme before first paint (kept as a file so the page can run under a strict Content-Security-Policy).
(function () {
  try {
    var t = localStorage.getItem('cr-theme');
    var dark = t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.classList.toggle('dark', dark);
  } catch (e) { /* storage blocked: stay light */ }
})();
