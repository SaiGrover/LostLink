/* ==========================================================================
   LostLink landing — count-up stats + mobile menu
   ========================================================================== */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- count-up stats (once, via IntersectionObserver) ------------------ */
  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

  function formatValue(v, decimals, suffix) {
    return (decimals > 0 ? v.toFixed(decimals) : Math.round(v).toString()) + suffix;
  }

  function runCount(el, i) {
    var target = parseFloat(el.dataset.target);
    var suffix = el.dataset.suffix || '';
    var decimals = parseInt(el.dataset.decimals || '0', 10);
    if (reduceMotion) { el.textContent = formatValue(target, decimals, suffix); return; }

    var duration = 1500 + i * 80;
    var startOffset = 480 + i * 90;
    var done = false;
    function finish() { if (!done) { done = true; el.textContent = formatValue(target, decimals, suffix); } }
    setTimeout(function () {
      var start = null;
      function step(ts) {
        if (done) return;
        if (start === null) start = ts;
        var p = Math.min((ts - start) / duration, 1);
        el.textContent = formatValue(target * easeOutCubic(p), decimals, suffix);
        if (p < 1) requestAnimationFrame(step);
        else finish();
      }
      requestAnimationFrame(step);
    }, startOffset);
    // Safety net: if rAF is paused (tab loaded hidden), still land the final value.
    setTimeout(finish, startOffset + duration + 250);
  }

  var statVals = Array.prototype.slice.call(document.querySelectorAll('.stat-val'));
  var statsEl = document.querySelector('.stats');
  var counted = false;
  function startCount() {
    if (counted) return;
    counted = true;
    statVals.forEach(runCount);
  }
  if (statVals.length && statsEl) {
    if ('IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (entries, obs) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) { startCount(); obs.disconnect(); }
        });
      }, { threshold: 0.25 });
      io.observe(statsEl);
    }
    // Fallback: single-viewport layout means the stats are on screen at load;
    // if the observer never fires (already in view / non-compositing context),
    // start once the element is within the viewport anyway.
    window.addEventListener('load', function () {
      setTimeout(function () {
        var r = statsEl.getBoundingClientRect();
        if (r.top < window.innerHeight && r.bottom > 0) startCount();
      }, 350);
    });
  }

  /* ---- mobile menu ------------------------------------------------------ */
  var burger = document.getElementById('burger');
  var overlay = document.getElementById('overlay');
  var menu = document.getElementById('mobile-menu');

  function openMenu() {
    burger.setAttribute('aria-expanded', 'true');
    overlay.hidden = false;
    menu.hidden = false;
    document.body.classList.add('menu-open');
  }
  function closeMenu() {
    burger.setAttribute('aria-expanded', 'false');
    overlay.hidden = true;
    menu.hidden = true;
    document.body.classList.remove('menu-open');
  }
  function toggleMenu() {
    (burger.getAttribute('aria-expanded') === 'true') ? closeMenu() : openMenu();
  }

  if (burger) {
    burger.addEventListener('click', toggleMenu);
    overlay.addEventListener('click', closeMenu);
    menu.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', closeMenu);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeMenu();
    });
    window.addEventListener('resize', function () {
      if (window.innerWidth > 720) closeMenu();
    });
  }
})();
