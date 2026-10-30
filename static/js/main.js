// Auto-dismiss toast messages.
document.querySelector('.mobile-menu')?.addEventListener('click', function () {
  const open = document.querySelector('.shell').classList.toggle('nav-open');
  this.setAttribute('aria-expanded', String(open));
});
document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') {
    document.querySelector('.shell')?.classList.remove('nav-open');
    document.querySelector('.mobile-menu')?.setAttribute('aria-expanded', 'false');
  }
});
(function () {
  const toasts = document.getElementById('toasts');
  if (!toasts) return;
  setTimeout(() => {
    [...toasts.children].forEach((t, i) => {
      setTimeout(() => {
        t.style.transition = 'opacity .3s, transform .3s';
        t.style.opacity = '0';
        t.style.transform = 'translateX(20px)';
        setTimeout(() => t.remove(), 300);
      }, i * 120);
    });
  }, 4200);
})();

// Dark-mode toggle. Persists the choice; falls back to OS preference.
(function () {
  function current() {
    var explicit = document.documentElement.getAttribute('data-theme');
    if (explicit) return explicit;
    return window.matchMedia &&
      window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('lostlink-theme', next); } catch (e) {}
    });
  });
})();

// Score rings: set the conic-gradient percentage from data-p.
document.querySelectorAll('.score-ring[data-p]').forEach((el) => {
  el.style.setProperty('--p', el.dataset.p);
  if (el.dataset.ring) el.style.setProperty('--ring', el.dataset.ring);
});
