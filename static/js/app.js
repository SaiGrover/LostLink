/* LostLink interface behaviour: progressive enhancement only.
   Every form and link works without this file. It adds feedback, shortcuts,
   live previews and in-place updates. */
(function () {
  'use strict';
  var doc = document, root = doc.documentElement;
  function $(s, c) { return (c || doc).querySelector(s); }
  function $$(s, c) { return Array.prototype.slice.call((c || doc).querySelectorAll(s)); }
  function csrf() { var m = doc.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/); var i = $('input[name=csrfmiddlewaretoken]'); return m ? decodeURIComponent(m[1]) : (i ? i.value : ''); }
  window.LostLink = { $: $, $$: $$, csrf: csrf, toast: toast, animateIn: animateIn };

  /* ---- Theme: day by default, night only when chosen ---------------------- */
  $$('[data-theme-toggle]').forEach(function (b) {
    b.addEventListener('click', function () {
      var dark = root.getAttribute('data-theme') !== 'dark';
      if (dark) root.setAttribute('data-theme', 'dark'); else root.removeAttribute('data-theme');
      try { localStorage.setItem('lostlink-theme', dark ? 'dark' : 'light'); } catch (e) {}
    });
  });

  /* ---- Masthead gains a hairline once the page scrolls -------------------- */
  var mast = $('.mast');
  if (mast) { var onScroll = function () { mast.classList.toggle('is-scrolled', window.scrollY > 4); }; window.addEventListener('scroll', onScroll, { passive: true }); onScroll(); }

  /* ---- Menus: close on outside click / Escape; "/" focuses search -------- */
  doc.addEventListener('click', function (e) { $$('details.menu[open]').forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute('open'); }); });
  doc.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') $$('details.menu[open]').forEach(function (d) { d.removeAttribute('open'); var s = $('summary', d); if (s) s.focus(); });
    if (e.key === '/' && !/input|textarea|select/i.test((doc.activeElement || {}).tagName || '')) { var s = $('[data-search-shortcut]'); if (s && s.offsetParent) { e.preventDefault(); s.focus(); } }
  });

  /* ---- Toasts ------------------------------------------------------------- */
  var ICON = { success: 'check', danger: 'alert', warning: 'alert' };
  function dismiss(el) { if (el.classList.contains('is-leaving')) return; el.classList.add('is-leaving'); setTimeout(function () { el.remove(); }, 240); }
  function arm(el) {
    var t = setTimeout(function () { dismiss(el); }, 6000);
    el.addEventListener('mouseenter', function () { clearTimeout(t); });
    el.addEventListener('mouseleave', function () { t = setTimeout(function () { dismiss(el); }, 2500); });
    var x = $('.toast__x', el); if (x) x.addEventListener('click', function () { dismiss(el); });
  }
  function toast(msg, tone) {
    var r = $('#toasts'); if (!r) return; tone = tone || 'info';
    var el = doc.createElement('div'); el.className = 'toast toast--' + tone; el.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
    el.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-' + (ICON[tone] || 'info') + '"></use></svg><div></div><button type="button" class="toast__x" aria-label="Dismiss"><svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg></button>';
    el.children[1].textContent = msg; r.appendChild(el); arm(el);
  }
  $$('#toasts .toast').forEach(arm);

  /* ---- Confirmation dialog for consequential actions ---------------------- */
  var dialog = $('#confirm-dialog'), pending = null;
  if (dialog && dialog.showModal) {
    var tEl = $('[data-confirm-title]', dialog), xEl = $('[data-confirm-text]', dialog), ok = $('[data-confirm-ok]', dialog),
        noteWrap = $('[data-confirm-note]', dialog), note = $('textarea', noteWrap);
    doc.addEventListener('submit', function (e) {
      var f = e.target;
      if (!f.matches('form[data-confirm]') || f.dataset.confirmed === '1') return;
      e.preventDefault(); pending = f;
      var tone = f.dataset.confirmTone || 'neutral';
      tEl.textContent = f.dataset.confirm; xEl.textContent = f.dataset.confirmText || '';
      ok.textContent = f.dataset.confirmLabel || 'Confirm';
      ok.className = 'btn ' + (tone === 'danger' ? 'btn-danger' : tone === 'success' ? 'btn-ok' : 'btn-primary');
      dialog.className = 'dialog' + (tone === 'danger' ? ' dialog--danger' : tone === 'success' ? ' dialog--success' : '');
      var wants = f.hasAttribute('data-confirm-note'); noteWrap.hidden = !wants;
      if (wants) { note.value = (f.querySelector('[name=note]') || {}).value || ''; note.placeholder = f.dataset.confirmNote || 'Optional note'; }
      dialog.showModal(); (wants ? note : ok).focus();
    });
    ok.addEventListener('click', function () {
      if (!pending) return;
      if (!noteWrap.hidden) { var fld = pending.querySelector('[name=note]'); if (!fld) { fld = doc.createElement('input'); fld.type = 'hidden'; fld.name = 'note'; pending.appendChild(fld); } fld.value = note.value; }
      pending.dataset.confirmed = '1'; var f = pending; pending = null; dialog.close();
      var b = f.querySelector('[type=submit], button:not([type])'); if (b) b.classList.add('is-loading');
      f.submit();
    });
    $$('[data-confirm-cancel]', dialog).forEach(function (b) { b.addEventListener('click', function () { pending = null; dialog.close(); }); });
    dialog.addEventListener('click', function (e) { if (e.target === dialog) { pending = null; dialog.close(); } });
  }

  /* ---- Submit feedback (no double posts) ---------------------------------- */
  doc.addEventListener('submit', function (e) {
    var f = e.target;
    if (e.defaultPrevented || f.hasAttribute('data-no-loading') || f.method.toLowerCase() === 'get') return;
    var b = e.submitter || f.querySelector('button[type=submit], button:not([type])');
    if (b && b.classList.contains('btn')) setTimeout(function () { b.classList.add('is-loading'); b.setAttribute('aria-busy', 'true'); }, 0);
  });

  /* ---- Password reveal, caps lock, live guidance -------------------------- */
  $$('[data-reveal-password]').forEach(function (b) {
    var i = doc.getElementById(b.getAttribute('aria-controls')); if (!i) return;
    b.addEventListener('click', function () {
      var show = i.type === 'password'; i.type = show ? 'text' : 'password';
      b.setAttribute('aria-pressed', String(show)); b.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      $('use', b).setAttribute('href', show ? '#i-eye-off' : '#i-eye');
    });
  });
  $$('input[type=password]').forEach(function (i) {
    var w = doc.getElementById(i.id + '-caps'); if (!w) return;
    function c(e) { if (e.getModifierState) w.hidden = !e.getModifierState('CapsLock'); }
    i.addEventListener('keyup', c); i.addEventListener('keydown', c); i.addEventListener('blur', function () { w.hidden = true; });
  });
  var pw = $('[data-password-strength]');
  if (pw) {
    var list = doc.getElementById(pw.dataset.passwordStrength), pw2 = doc.getElementById(pw.dataset.confirmWith);
    var ev = function () { var v = pw.value; var r = { length: v.length >= 8, numeric: v.length > 0 && !/^\d+$/.test(v), mix: /[a-zA-Z]/.test(v) && /[^a-zA-Z]/.test(v), match: !!pw2 && v.length > 0 && v === pw2.value };
      Object.keys(r).forEach(function (k) { var li = $('[data-rule=' + k + ']', list); if (li) li.classList.toggle('is-met', r[k]); }); };
    pw.addEventListener('input', ev); if (pw2) pw2.addEventListener('input', ev);
  }
  $$('form[data-validate] .input[required], form[data-validate] input[type=email]').forEach(function (i) {
    i.addEventListener('blur', function () { if (!i.value && !i.required) return; var v = i.checkValidity(); i.setAttribute('aria-invalid', String(!v)); var m = doc.getElementById(i.id + '-live-error'); if (m) m.textContent = v ? '' : i.validationMessage; });
  });
  $$('[data-counter]').forEach(function (i) { var o = doc.getElementById(i.dataset.counter), max = i.getAttribute('maxlength'); var u = function () { o.textContent = i.value.length + (max ? ' / ' + max : ''); }; i.addEventListener('input', u); u(); });

  /* ---- Photo dropzone + live tag preview ---------------------------------- */
  $$('[data-dropzone]').forEach(function (z) {
    var input = $('input[type=file]', z), pv = $('.dropzone__preview', z), img = pv && $('img', pv), name = $('[data-file-name]', z), err = $('[data-file-error]'), max = Number(z.dataset.maxMb || 5);
    function show(file) {
      if (err) err.textContent = '';
      if (!file) { z.classList.remove('has-file'); if (pv) pv.hidden = true; return; }
      if (!/^image\/(jpeg|png|webp|gif)$/.test(file.type)) { if (err) err.textContent = 'Choose a JPG, PNG, WEBP or GIF image.'; input.value = ''; return; }
      if (file.size > max * 1048576) { if (err) err.textContent = 'That photo is larger than ' + max + ' MB.'; input.value = ''; return; }
      z.classList.add('has-file'); if (pv && img) { img.src = URL.createObjectURL(file); pv.hidden = false; }
      if (name) name.textContent = file.name + ' · ' + (file.size / 1048576).toFixed(1) + ' MB';
      z.dispatchEvent(new CustomEvent('photo:change', { bubbles: true, detail: { url: img && img.src } }));
    }
    input.addEventListener('change', function () { show(input.files[0]); });
    ['dragenter', 'dragover'].forEach(function (t) { z.addEventListener(t, function () { z.classList.add('is-over'); }); });
    ['dragleave', 'drop'].forEach(function (t) { z.addEventListener(t, function () { z.classList.remove('is-over'); }); });
  });
  var pc = $('[data-preview]');
  if (pc) {
    var form = doc.querySelector('form[data-report-form]');
    var sync = function () {
      var g = function (n) { return form.elements[n]; };
      var type = (form.querySelector('input[name=report_type]:checked') || {}).value || 'lost';
      var st = $('[data-p=type]', pc); st.className = 'type type--' + type;
      $('i', st).className = 'mk mk--' + type; st.lastChild.textContent = type === 'found' ? 'Found' : 'Lost';
      $('[data-p=title]', pc).textContent = g('title').value.trim() || 'What is it?';
      var cat = g('category'), loc = g('location'), dt = g('event_date');
      $('[data-p=location]', pc).textContent = loc && loc.value ? loc.options[loc.selectedIndex].text : 'Campus zone';
      $('[data-p=date]', pc).textContent = dt && dt.value ? new Date(dt.value + 'T00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : 'Date';
      $('[data-p=category]', pc).textContent = cat && cat.value ? cat.options[cat.selectedIndex].text : 'Category';
    };
    form.addEventListener('input', sync); form.addEventListener('change', sync); sync();
    form.addEventListener('photo:change', function (e) { var m = $('[data-p=media]', pc); m.innerHTML = e.detail && e.detail.url ? '<img alt="" src="' + e.detail.url + '">' : '<span class="photo__none">?</span>'; });
  }

  /* ---- Tabs (all panels visible without JS) -------------------------------- */
  $$('[data-tabs]').forEach(function (g) {
    var tabs = $$('[role=tab]', g);
    function sel(t, focus, remember) {
      tabs.forEach(function (x) { var on = x === t, p = doc.getElementById(x.getAttribute('aria-controls')); x.setAttribute('aria-selected', String(on)); x.tabIndex = on ? 0 : -1; if (p) { if (on) p.removeAttribute('data-inactive'); else p.setAttribute('data-inactive', ''); } });
      if (focus) t.focus();
      if (remember && history.replaceState) history.replaceState(null, '', '#' + t.getAttribute('aria-controls'));
    }
    tabs.forEach(function (t, i) {
      t.addEventListener('click', function (e) { e.preventDefault(); sel(t, false, true); });
      t.addEventListener('keydown', function (e) { var d = /Right|Down/.test(e.key) ? 1 : /Left|Up/.test(e.key) ? -1 : 0; if (d) { e.preventDefault(); sel(tabs[(i + d + tabs.length) % tabs.length], true, true); } });
    });
    sel(tabs.filter(function (t) { return '#' + t.getAttribute('aria-controls') === location.hash; })[0] || tabs[0]);
  });

  /* ---- Overlaps print into place ------------------------------------------
     When a match comes into view its two discs slide from touching to their
     real overlap while the score counts up, so the number is seen being made
     of the overlap. With reduced motion everything is shown at rest. */
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function countUp(el, target) {
    if (reduce) { el.textContent = target; return; }
    var t0 = null, dur = 1100;
    function step(ts) { if (!t0) t0 = ts; var p = Math.min((ts - t0) / dur, 1); el.textContent = Math.round(target * (1 - Math.pow(1 - p, 4))); if (p < 1) requestAnimationFrame(step); }
    el.textContent = '0'; requestAnimationFrame(step);
  }
  function reveal(el) {
    if (el.classList.contains('is-in')) return;
    var n = $('[data-count-target]', el);
    el.classList.add('is-in');
    if (n && el.dataset.count) countUp(n, Number(el.dataset.count));
  }
  function animateIn(scope) {
    var els = $$('[data-reveal]', scope || doc);
    if (reduce || !('IntersectionObserver' in window)) { els.forEach(function (e) { e.classList.add('is-in'); }); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { io.unobserve(en.target); setTimeout(function () { reveal(en.target); }, 120); } });
    }, { threshold: 0.4 });
    els.forEach(function (e) { io.observe(e); });
  }
  animateIn();
  window.LostLink.animateIn = animateIn;

  /* ---- Notifications: mark read/unread in place, live badge -------------- */
  function setBadge(n) { $$('[data-unread-badge]').forEach(function (b) { b.textContent = n; b.hidden = !n; }); $$('[data-unread-label]').forEach(function (b) { b.textContent = n ? n + ' unread' : 'All read'; }); }
  doc.addEventListener('submit', function (e) {
    var f = e.target; if (!f.matches('form[data-ajax-read]')) return; e.preventDefault();
    fetch(f.action, { method: 'POST', body: new FormData(f), headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrf() }, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (d) {
        if ('id' in d) {
          var row = f.closest('[data-notification]');
          if (row) { row.classList.toggle('is-unread', !d.is_read); var b = $('button', f), u = $('input[name=unread]', f); if (u) u.value = d.is_read ? '1' : ''; if (b) { b.textContent = d.is_read ? 'Mark unread' : 'Mark read'; b.classList.remove('is-loading'); } }
        } else {
          $$('[data-notification]').forEach(function (r) { r.classList.remove('is-unread'); });
          $$('[data-notification] form[data-ajax-read] button').forEach(function (b) { b.textContent = 'Mark unread'; b.classList.remove('is-loading'); });
          $$('[data-notification] form[data-ajax-read] input[name=unread]').forEach(function (i) { i.value = '1'; });
          $$('form[data-read-all] button').forEach(function (b) { b.classList.remove('is-loading'); });
          toast('All notifications are marked as read.', 'success');
        }
        setBadge(d.unread);
      })
      .catch(function () { $$('.is-loading', f).forEach(function (b) { b.classList.remove('is-loading'); }); toast('Could not update that. Try again.', 'danger'); });
  });
  var poll = $('[data-poll]');
  if (poll) setInterval(function () { if (doc.hidden) return; fetch(poll.dataset.poll, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) { if (d) setBadge(d.unread); }).catch(function () {}); }, 60000);
})();
