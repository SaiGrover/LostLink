/* LostLink interface behaviour. Progressive enhancement only: every form and
   link works without this file; it adds feedback, motion and shortcuts. */
(function () {
  'use strict';
  var doc = document, root = doc.documentElement;
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function $(sel, ctx) { return (ctx || doc).querySelector(sel); }
  function $$(sel, ctx) { return Array.prototype.slice.call((ctx || doc).querySelectorAll(sel)); }
  function csrf() {
    var m = doc.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    var input = $('input[name=csrfmiddlewaretoken]');
    return m ? decodeURIComponent(m[1]) : (input ? input.value : '');
  }
  window.LostLink = { $: $, $$: $$, csrf: csrf, toast: toast };

  /* ---- Theme ------------------------------------------------------------ */
  function currentTheme() {
    return root.getAttribute('data-theme') ||
      (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  }
  $$('[data-theme-toggle]').forEach(function (btn) {
    btn.setAttribute('aria-pressed', String(currentTheme() === 'dark'));
    btn.addEventListener('click', function () {
      var next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      btn.setAttribute('aria-pressed', String(next === 'dark'));
      try { localStorage.setItem('lostlink-theme', next); } catch (e) {}
    });
  });

  /* ---- Mobile navigation drawer ---------------------------------------- */
  var shell = $('.shell'), navToggle = $('.nav-toggle'), scrim = null;
  function setNav(open) {
    if (!shell) return;
    shell.classList.toggle('nav-open', open);
    if (navToggle) navToggle.setAttribute('aria-expanded', String(open));
    doc.body.style.overflow = open ? 'hidden' : '';
    if (open) {
      scrim = doc.createElement('div'); scrim.className = 'scrim';
      scrim.addEventListener('click', function () { setNav(false); });
      doc.body.appendChild(scrim);
      var first = $('.sidebar a'); if (first) first.focus();
    } else if (scrim) { scrim.remove(); scrim = null; }
  }
  if (navToggle) navToggle.addEventListener('click', function () { setNav(!shell.classList.contains('nav-open')); });

  /* ---- Topbar border on scroll ----------------------------------------- */
  var topbar = $('.topbar');
  if (topbar) {
    var onScroll = function () { topbar.classList.toggle('is-scrolled', window.scrollY > 4); };
    window.addEventListener('scroll', onScroll, { passive: true }); onScroll();
  }

  /* ---- Menus (details): close on outside click / Escape ----------------- */
  doc.addEventListener('click', function (e) {
    $$('details.menu[open]').forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute('open'); });
  });
  doc.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      $$('details.menu[open]').forEach(function (d) { d.removeAttribute('open'); var s = $('summary', d); if (s) s.focus(); });
      if (shell && shell.classList.contains('nav-open')) { setNav(false); if (navToggle) navToggle.focus(); }
    }
    // "/" focuses search unless typing.
    if (e.key === '/' && !/input|textarea|select/i.test((doc.activeElement || {}).tagName || '')) {
      var s = $('[data-search-shortcut]');
      if (s) { e.preventDefault(); s.focus(); s.select && s.select(); }
    }
  });

  /* ---- Toasts ----------------------------------------------------------- */
  var ICONS = { success: 'check', danger: 'alert', warning: 'alert', info: 'info', muted: 'info' };
  function dismiss(el) {
    if (!el || el.classList.contains('is-leaving')) return;
    el.classList.add('is-leaving');
    setTimeout(function () { el.remove(); }, 220);
  }
  function arm(el) {
    var timer = setTimeout(function () { dismiss(el); }, 6000);
    el.addEventListener('mouseenter', function () { clearTimeout(timer); });
    el.addEventListener('mouseleave', function () { timer = setTimeout(function () { dismiss(el); }, 2500); });
    var close = $('.toast__close', el);
    if (close) close.addEventListener('click', function () { dismiss(el); });
  }
  function toast(message, tone) {
    var region = $('#toasts');
    if (!region) return;
    tone = tone || 'info';
    var el = doc.createElement('div');
    el.className = 'toast toast--' + tone;
    el.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
    el.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-' + (ICONS[tone] || 'info') + '"></use></svg>' +
      '<div></div><button type="button" class="toast__close" aria-label="Dismiss"><svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg></button>';
    el.children[1].textContent = message;
    region.appendChild(el); arm(el);
  }
  $$('#toasts .toast').forEach(arm);

  /* ---- Confirmation dialog for consequential forms --------------------- */
  var dialog = $('#confirm-dialog'), pending = null;
  if (dialog && typeof dialog.showModal === 'function') {
    var titleEl = $('[data-confirm-title]', dialog), textEl = $('[data-confirm-text]', dialog),
        okBtn = $('[data-confirm-ok]', dialog), noteWrap = $('[data-confirm-note]', dialog),
        noteInput = $('textarea', noteWrap), iconUse = $('[data-confirm-icon] use', dialog);
    doc.addEventListener('submit', function (e) {
      var form = e.target;
      if (!form.matches('form[data-confirm]') || form.dataset.confirmed === '1') return;
      e.preventDefault();
      pending = form;
      var tone = form.dataset.confirmTone || 'neutral';
      dialog.className = 'dialog dialog--' + tone;
      titleEl.textContent = form.dataset.confirm;
      textEl.textContent = form.dataset.confirmText || '';
      okBtn.textContent = form.dataset.confirmLabel || 'Confirm';
      okBtn.className = 'btn ' + (tone === 'danger' ? 'btn-danger' : tone === 'success' ? 'btn-success' : 'btn-primary');
      iconUse.setAttribute('href', '#i-' + (tone === 'danger' ? 'alert' : tone === 'success' ? 'check' : 'info'));
      var wantsNote = form.hasAttribute('data-confirm-note');
      noteWrap.hidden = !wantsNote;
      if (wantsNote) {
        noteInput.value = (form.querySelector('[name=note]') || {}).value || '';
        noteInput.placeholder = form.dataset.confirmNote || 'Optional note';
      }
      dialog.showModal();
      (wantsNote ? noteInput : okBtn).focus();
    });
    okBtn.addEventListener('click', function () {
      if (!pending) return;
      if (!noteWrap.hidden) {
        var field = pending.querySelector('[name=note]');
        if (!field) { field = doc.createElement('input'); field.type = 'hidden'; field.name = 'note'; pending.appendChild(field); }
        field.value = noteInput.value;
      }
      pending.dataset.confirmed = '1';
      var form = pending; pending = null;
      dialog.close();
      var btn = form.querySelector('[type=submit], button:not([type])');
      if (btn) btn.classList.add('is-loading');
      form.submit();
    });
    $$('[data-confirm-cancel]', dialog).forEach(function (b) { b.addEventListener('click', function () { pending = null; dialog.close(); }); });
    dialog.addEventListener('click', function (e) { if (e.target === dialog) { pending = null; dialog.close(); } });
  }

  /* ---- Submit feedback (prevents double submission) -------------------- */
  doc.addEventListener('submit', function (e) {
    var form = e.target;
    if (e.defaultPrevented || form.hasAttribute('data-no-loading') || form.method.toLowerCase() === 'get') return;
    var btn = e.submitter || form.querySelector('button[type=submit], button:not([type])');
    if (btn && btn.classList.contains('btn')) {
      setTimeout(function () { btn.classList.add('is-loading'); btn.setAttribute('aria-busy', 'true'); }, 0);
    }
  });

  /* ---- Password visibility & caps lock --------------------------------- */
  $$('[data-reveal-password]').forEach(function (btn) {
    var input = doc.getElementById(btn.getAttribute('aria-controls'));
    if (!input) return;
    btn.addEventListener('click', function () {
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.setAttribute('aria-pressed', String(show));
      btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      $('use', btn).setAttribute('href', show ? '#i-eye-off' : '#i-eye');
    });
  });
  $$('input[type=password]').forEach(function (input) {
    var warn = doc.getElementById(input.id + '-caps');
    if (!warn) return;
    function check(e) { if (e.getModifierState) warn.hidden = !e.getModifierState('CapsLock'); }
    input.addEventListener('keyup', check); input.addEventListener('keydown', check);
    input.addEventListener('blur', function () { warn.hidden = true; });
  });

  /* ---- Live password guidance (register) ------------------------------- */
  var pw1 = $('[data-password-strength]');
  if (pw1) {
    var list = doc.getElementById(pw1.dataset.passwordStrength), pw2 = doc.getElementById(pw1.dataset.confirmWith);
    function evaluate() {
      var v = pw1.value;
      var rules = { length: v.length >= 8, numeric: v.length > 0 && !/^\d+$/.test(v),
                    mix: /[a-zA-Z]/.test(v) && /[^a-zA-Z]/.test(v), match: !!pw2 && v.length > 0 && v === pw2.value };
      Object.keys(rules).forEach(function (k) { var li = $('[data-rule=' + k + ']', list); if (li) li.classList.toggle('is-met', rules[k]); });
    }
    pw1.addEventListener('input', evaluate); if (pw2) pw2.addEventListener('input', evaluate);
  }

  /* ---- Inline validation on blur --------------------------------------- */
  $$('form[data-validate] .input[required], form[data-validate] .field-input[required], form[data-validate] input[type=email]').forEach(function (input) {
    input.addEventListener('blur', function () {
      if (!input.value && !input.required) return;
      var valid = input.checkValidity();
      input.setAttribute('aria-invalid', String(!valid));
      var msg = doc.getElementById(input.id + '-live-error');
      if (msg) msg.textContent = valid ? '' : input.validationMessage;
    });
    input.addEventListener('input', function () { if (input.getAttribute('aria-invalid') === 'true' && input.checkValidity()) { input.setAttribute('aria-invalid', 'false'); var msg = doc.getElementById(input.id + '-live-error'); if (msg) msg.textContent = ''; } });
  });

  /* ---- Character counters ---------------------------------------------- */
  $$('[data-counter]').forEach(function (input) {
    var out = doc.getElementById(input.dataset.counter), max = input.getAttribute('maxlength');
    function update() { out.textContent = input.value.length + (max ? ' / ' + max : ''); }
    input.addEventListener('input', update); update();
  });

  /* ---- Photo upload with preview --------------------------------------- */
  $$('[data-dropzone]').forEach(function (zone) {
    var input = $('input[type=file]', zone), preview = $('.dropzone__preview', zone), img = preview && $('img', preview),
        name = $('[data-file-name]', zone), err = $('[data-file-error]', zone), maxMb = Number(zone.dataset.maxMb || 5);
    var clear = $('[data-file-clear]', zone);
    function show(file) {
      if (err) err.textContent = '';
      if (!file) { zone.classList.remove('has-file'); if (preview) preview.hidden = true; return; }
      if (!/^image\/(jpeg|png|webp|gif)$/.test(file.type)) { if (err) err.textContent = 'Choose a JPG, PNG, WEBP or GIF image.'; input.value = ''; return; }
      if (file.size > maxMb * 1024 * 1024) { if (err) err.textContent = 'That photo is larger than ' + maxMb + ' MB.'; input.value = ''; return; }
      zone.classList.add('has-file');
      if (preview && img) { img.src = URL.createObjectURL(file); preview.hidden = false; }
      if (name) name.textContent = file.name + ' · ' + (file.size / 1024 / 1024).toFixed(1) + ' MB';
      zone.dispatchEvent(new CustomEvent('photo:change', { bubbles: true, detail: { url: img && img.src } }));
    }
    input.addEventListener('change', function () { show(input.files[0]); });
    ['dragenter', 'dragover'].forEach(function (t) { zone.addEventListener(t, function () { zone.classList.add('is-dragover'); }); });
    ['dragleave', 'drop'].forEach(function (t) { zone.addEventListener(t, function () { zone.classList.remove('is-dragover'); }); });
    if (clear) clear.addEventListener('click', function (e) { e.preventDefault(); input.value = ''; show(null); zone.dispatchEvent(new CustomEvent('photo:change', { bubbles: true, detail: {} })); });
  });

  /* ---- Live report preview card ---------------------------------------- */
  var previewCard = $('[data-preview-card]');
  if (previewCard) {
    var form = previewCard.closest('form') || doc.querySelector('form[data-report-form]');
    function sync() {
      var get = function (n) { var el = form.elements[n]; return el ? el : null; };
      var type = (form.querySelector('input[name=report_type]:checked') || {}).value || 'lost';
      var title = get('title').value.trim() || 'Untitled item';
      var cat = get('category'), loc = get('location'), date = get('event_date');
      $('[data-p=title]', previewCard).textContent = title;
      var badge = $('[data-p=type]', previewCard);
      badge.className = 'type type--' + type;
      $('use', badge).setAttribute('href', '#i-' + type);
      $('[data-p=type-label]', previewCard).textContent = type === 'found' ? 'Found' : 'Lost';
      $('[data-p=category]', previewCard).textContent = cat && cat.value ? cat.options[cat.selectedIndex].text : 'Category';
      $('[data-p=location]', previewCard).textContent = loc && loc.value ? loc.options[loc.selectedIndex].text : 'Campus zone';
      $('[data-p=date]', previewCard).textContent = date && date.value ? new Date(date.value + 'T00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : 'Date';
    }
    form.addEventListener('input', sync); form.addEventListener('change', sync); sync();
    form.addEventListener('photo:change', function (e) {
      var media = $('[data-p=media]', previewCard);
      if (e.detail && e.detail.url) media.innerHTML = '<img alt="" src="' + e.detail.url + '">';
      else media.innerHTML = '<div class="placeholder-media" aria-hidden="true">·</div>';
    });
  }

  /* ---- Tabs (progressive: all panels visible without JS) --------------- */
  $$('[data-tabs]').forEach(function (group) {
    var tabs = $$('[role=tab]', group);
    function select(tab, focus, remember) {
      tabs.forEach(function (t) {
        var on = t === tab, panel = doc.getElementById(t.getAttribute('aria-controls'));
        t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1;
        if (panel) { if (on) panel.removeAttribute('data-inactive'); else panel.setAttribute('data-inactive', ''); }
      });
      if (focus) tab.focus();
      // Only user choices update the URL: writing a fragment during page load
      // makes the browser jump to it when loading finishes.
      if (remember && history.replaceState) history.replaceState(null, '', '#' + tab.getAttribute('aria-controls'));
    }
    tabs.forEach(function (tab, i) {
      tab.addEventListener('click', function (e) { e.preventDefault(); select(tab, false, true); });
      tab.addEventListener('keydown', function (e) {
        var d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (d) { e.preventDefault(); select(tabs[(i + d + tabs.length) % tabs.length], true, true); }
      });
    });
    var initial = tabs.filter(function (t) { return '#' + t.getAttribute('aria-controls') === location.hash; })[0] || tabs[0];
    if (initial) select(initial);
  });

  /* ---- Scroll reveal ---------------------------------------------------- */
  var revealables = $$('[data-reveal]');
  if (revealables.length && 'IntersectionObserver' in window && !reduceMotion) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add('is-visible'); io.unobserve(en.target); } });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
    revealables.forEach(function (el) { io.observe(el); });
  } else {
    revealables.forEach(function (el) { el.classList.add('is-visible'); });
  }

  /* ---- Notifications: mark read without reload, live badge ------------- */
  function setBadge(n) {
    $$('[data-unread-badge]').forEach(function (b) { b.textContent = n; b.hidden = !n; });
    $$('[data-unread-label]').forEach(function (b) { b.textContent = n ? n + ' unread' : 'All caught up'; });
  }
  doc.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.matches('form[data-ajax-read]')) return;
    e.preventDefault();
    fetch(form.action, { method: 'POST', body: new FormData(form), headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrf() }, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(function (data) {
        if ('id' in data) {
          var row = form.closest('[data-notification]');
          if (row) {
            row.classList.toggle('is-unread', !data.is_read);
            var btn = $('button', form), unreadField = $('input[name=unread]', form);
            if (unreadField) unreadField.value = data.is_read ? '1' : '';
            if (btn) { btn.textContent = data.is_read ? 'Mark unread' : 'Mark read'; btn.classList.remove('is-loading'); }
          }
        } else {
          $$('[data-notification]').forEach(function (row) { row.classList.remove('is-unread'); });
          $$('form[data-ajax-read] button').forEach(function (b) { b.classList.remove('is-loading'); });
          $$('[data-notification] form[data-ajax-read]:not([data-read-all]) button').forEach(function (b) { b.textContent = 'Mark unread'; });
          $$('[data-notification] form[data-ajax-read] input[name=unread]').forEach(function (f) { f.value = '1'; });
          toast('All notifications marked as read.', 'success');
        }
        setBadge(data.unread);
      })
      .catch(function () { $$('.is-loading', form).forEach(function (b) { b.classList.remove('is-loading'); }); toast('Could not update the notification. Try again.', 'danger'); });
  });
  var badgeEl = $('[data-unread-badge]');
  if (badgeEl && badgeEl.dataset.poll) {
    setInterval(function () {
      if (doc.hidden) return;
      fetch(badgeEl.dataset.poll, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) setBadge(d.unread); }).catch(function () {});
    }, 60000);
  }

  /* ---- Copy-to-clipboard ------------------------------------------------ */
  $$('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (navigator.clipboard) navigator.clipboard.writeText(btn.dataset.copy).then(function () { toast('Copied to clipboard.', 'success'); });
    });
  });
})();
