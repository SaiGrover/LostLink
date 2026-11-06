/* LostLink landing page: typewriter heading, mobile menu, the live connection
   between the real matched pair on the orbits, and pausing work off-screen.
   Progressive enhancement: without this file the page is complete and static. */
(function () {
  'use strict';
  var doc = document, root = doc.documentElement;
  var hero = doc.querySelector('.lp-hero');
  if (!hero) return;
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  root.classList.add('lp-anim');

  /* ---- TypewriterHeading --------------------------------------------------
     Splits each [data-tw] part into characters that already occupy their
     final space (invisible), then reveals them one at a time with a cursor
     that has no width, so the layout never shifts. */
  function TypewriterHeading(el, options) {
    var speed = Number((options && options.speed) || el.dataset.speed || 35);
    var delay = Number((options && options.delay) || el.dataset.delay || 400);
    var done = (options && options.onDone) || function () {};
    var chars = [];
    Array.prototype.forEach.call(el.querySelectorAll('[data-tw]'), function (part) {
      var text = part.textContent;
      part.textContent = '';
      Array.from(text).forEach(function (ch) {
        var s = doc.createElement('span');
        s.className = 'tw-c';
        s.setAttribute('aria-hidden', 'true');
        s.textContent = ch;
        part.appendChild(s);
        chars.push(s);
      });
    });
    if (!chars.length) { done(); return; }
    if (reduce) { chars.forEach(function (c) { c.classList.add('is-on'); }); el.classList.add('is-typed'); done(); return; }
    var cursor = doc.createElement('span');
    cursor.className = 'tw-cursor';
    cursor.setAttribute('aria-hidden', 'true');
    chars[0].parentNode.insertBefore(cursor, chars[0]);
    var i = 0;
    function tick() {
      if (i < chars.length) {
        chars[i].classList.add('is-on');
        chars[i].parentNode.insertBefore(cursor, chars[i].nextSibling);
        i += 1;
        setTimeout(tick, chars[i - 1].textContent === ' ' ? speed * 0.6 : speed);
      } else {
        el.classList.add('is-typed');
        done();
      }
    }
    setTimeout(tick, delay);
  }

  var title = doc.querySelector('[data-typewriter]');
  function afterTyping() {
    hero.classList.add('is-typed');
    // the floating pointer arrives once the actions have settled
    setTimeout(function () { hero.classList.add('is-cursor'); }, reduce ? 0 : 900);
  }
  if (title) TypewriterHeading(title, { onDone: afterTyping }); else afterTyping();

  /* ---- Mobile menu -------------------------------------------------------- */
  var menuBtn = doc.querySelector('[data-lp-menu-btn]'), menu = doc.getElementById('lp-menu');
  if (menuBtn && menu) {
    var setMenu = function (open) {
      menu.hidden = !open;
      menuBtn.setAttribute('aria-expanded', String(open));
      menuBtn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    };
    menuBtn.addEventListener('click', function () { setMenu(menu.hidden); if (!menu.hidden) { var f = menu.querySelector('a'); if (f) f.focus(); } });
    doc.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !menu.hidden) { setMenu(false); menuBtn.focus(); } });
    doc.addEventListener('click', function (e) { if (!menu.hidden && !menu.contains(e.target) && !menuBtn.contains(e.target)) setMenu(false); });
    window.addEventListener('resize', function () { if (window.innerWidth > 860 && !menu.hidden) setMenu(false); });
  }

  /* ---- Live connection between the real matched pair ------------------------
     The lost and found photos ride different orbits. Every few seconds a line
     is drawn between them, a spark travels along it and the real score shows
     at its midpoint. Positions are read each frame while it is visible. */
  var stage = doc.querySelector('[data-orbits]');
  var svg = doc.querySelector('[data-link-svg]');
  var lostEl = doc.querySelector('[data-pair="lost"]'), foundEl = doc.querySelector('[data-pair="found"]');
  var label = doc.querySelector('[data-link-label]');
  var viz = doc.querySelector('.lp-viz');
  if (stage && svg && lostEl && foundEl && label) {
    var track = svg.querySelector('[data-link-track]'), line = svg.querySelector('[data-link-line]'),
        spark = svg.querySelector('[data-link-spark]'), grad = svg.querySelector('[data-link-grad]');
    var SIZE = 920;
    var center = function (el) {
      var box = stage.getBoundingClientRect(), r = el.querySelector('.lp-item__fly').getBoundingClientRect();
      var k = SIZE / box.width;
      return { x: (r.left + r.width / 2 - box.left) * k, y: (r.top + r.height / 2 - box.top) * k };
    };
    var geometry = function () {
      var a = center(lostEl), b = center(foundEl);
      var mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, C = SIZE / 2;
      // arc around the hub on the outside, so the score never covers it
      var dx = b.x - a.x, dy = b.y - a.y, l = Math.hypot(dx, dy) || 1;
      var nx = -dy / l, ny = dx / l;
      if (nx * (mx - C) + ny * (my - C) < 0) { nx = -nx; ny = -ny; }
      var h = nx * (mx - C) + ny * (my - C);          // how far the midpoint already sits from the hub
      var lift = Math.max(0, 210 - h) + 24;           // the curve's apex clears the hub (radius 118)
      return { a: a, b: b, c: { x: mx + nx * lift * 2, y: my + ny * lift * 2 } };
    };
    var at = function (g, t) {
      var u = 1 - t;
      return { x: u * u * g.a.x + 2 * u * t * g.c.x + t * t * g.b.x, y: u * u * g.a.y + 2 * u * t * g.c.y + t * t * g.b.y };
    };
    var draw = function (progress) {
      var g = geometry();
      var d = 'M' + g.a.x.toFixed(1) + ' ' + g.a.y.toFixed(1) + ' Q' + g.c.x.toFixed(1) + ' ' + g.c.y.toFixed(1) + ' ' + g.b.x.toFixed(1) + ' ' + g.b.y.toFixed(1);
      track.setAttribute('d', d); line.setAttribute('d', d);
      grad.setAttribute('x1', g.a.x); grad.setAttribute('y1', g.a.y); grad.setAttribute('x2', g.b.x); grad.setAttribute('y2', g.b.y);
      var len = line.getTotalLength();
      line.style.strokeDasharray = len;
      line.style.strokeDashoffset = len * (1 - progress);
      var p = at(g, Math.min(progress, 1));
      spark.setAttribute('cx', p.x); spark.setAttribute('cy', p.y);
      spark.style.opacity = progress >= 1 ? 0 : 1;
      // the label lives in the same unscaled 920px space as the SVG
      var mid = at(g, 0.5);
      label.style.left = mid.x + 'px';
      label.style.top = mid.y + 'px';
    };

    if (reduce) {
      // At rest: the connection is simply shown
      draw(1); svg.classList.add('is-on'); label.classList.add('is-on');
      lostEl.classList.add('is-linked'); foundEl.classList.add('is-linked');
    } else {
      var raf = 0, running = false;
      var pulse = function () {
        if (running || (viz && viz.classList.contains('is-paused')) || doc.hidden) return;
        running = true;
        var t0 = performance.now(), DRAW = 1100, HOLD = 2600, OUT = 700;
        svg.classList.add('is-on');
        var frame = function (now) {
          var t = now - t0;
          draw(Math.min(t / DRAW, 1));
          if (t > DRAW * 0.8 && !label.classList.contains('is-on')) {
            label.classList.add('is-on'); lostEl.classList.add('is-linked'); foundEl.classList.add('is-linked');
          }
          if (t > DRAW + HOLD) {
            svg.classList.remove('is-on'); label.classList.remove('is-on');
            lostEl.classList.remove('is-linked'); foundEl.classList.remove('is-linked');
          }
          if (t < DRAW + HOLD + OUT) raf = requestAnimationFrame(frame);
          else running = false;
        };
        raf = requestAnimationFrame(frame);
      };
      // first connection once the pair has flown in, then every 7 seconds
      setTimeout(function () { pulse(); setInterval(pulse, 7000); }, 3200);
    }
  }

  /* ---- Pause the orbits while they are off-screen ----------------------------- */
  if (viz && 'IntersectionObserver' in window && !reduce) {
    new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { viz.classList.toggle('is-paused', !en.isIntersecting); });
    }).observe(viz);
  }
})();
