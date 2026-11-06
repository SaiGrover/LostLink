/* AJAX live search & filtering (synopsis Section 4.13).
   Requests the Django search endpoint as the user types or filters and swaps
   in the rendered result fragment. The URL mirrors the filters, so results
   are shareable and the back button restores them. Without JavaScript the
   same form submits normally. */
(function () {
  'use strict';
  var form = document.getElementById('search-form');
  if (!form) return;
  var L = window.LostLink;
  form.classList.add('is-live');
  var results = document.getElementById('results');
  var countEl = document.getElementById('result-count');
  var feedback = document.getElementById('search-feedback');
  var spinner = document.getElementById('search-spinner');
  var chipsEl = document.getElementById('active-filters');
  var filterCount = document.getElementById('filter-count');
  var endpoint = form.dataset.endpoint;
  var timer = null, controller = null, latest = 0, skeletonTimer = null;

  function params(page) {
    var p = new URLSearchParams(new FormData(form));
    Array.from(p.keys()).forEach(function (k) { if (!p.get(k)) p.delete(k); });
    if (p.get('sort') === 'newest') p.delete('sort');
    if (page && page > 1) p.set('page', page); else p.delete('page');
    return p;
  }

  function skeleton() {
    var card = '<div class="skeleton-item" aria-hidden="true"><div class="skeleton"></div>' +
      '<div class="skeleton skeleton-line" style="width:75%;height:18px"></div><div class="skeleton skeleton-line" style="width:50%"></div></div>';
    return '<div class="items">' + new Array(9).join(card) + '</div>';
  }

  function labelFor(name, value) {
    var el = form.elements[name];
    if (el && el.tagName === 'SELECT') { var o = Array.from(el.options).find(function (x) { return x.value === value; }); return o ? o.text : value; }
    if (name === 'q') return '“' + value + '”';
    if (name === 'date_from') return 'From ' + value;
    if (name === 'date_to') return 'Until ' + value;
    if (name === 'type') return value === 'lost' ? 'Lost' : 'Found';
    return value;
  }

  function renderChips() {
    if (!chipsEl) return;
    var p = params(), locked = form.dataset.lockedType, html = '', n = 0;
    ['q', 'type', 'category', 'location', 'status', 'date_from', 'date_to'].forEach(function (k) {
      var v = p.get(k);
      if (!v || (k === 'type' && locked)) return;
      n++;
      html += '<button type="button" class="chip" data-clear="' + k + '" aria-label="Remove filter ' + labelFor(k, v).replace(/"/g, '') + '">' +
        labelFor(k, v).replace(/[<>&]/g, '') + '<svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg></button>';
    });
    if (n > 1) html += '<button type="button" class="btn btn-plain btn-sm" data-clear="all">Clear all</button>';
    chipsEl.innerHTML = html;
    if (filterCount) { filterCount.textContent = n; filterCount.hidden = !n; }
  }

  function run(page, opts) {
    opts = opts || {};
    var seq = ++latest;
    if (controller) controller.abort();
    controller = new AbortController();
    var p = params(page);
    var shareable = p.toString();
    if (form.dataset.lockedType) p.set('type', form.dataset.lockedType);
    if (spinner) spinner.hidden = false;
    results.classList.add('is-loading');
    results.setAttribute('aria-busy', 'true');
    clearTimeout(skeletonTimer);
    skeletonTimer = setTimeout(function () { if (seq === latest) results.innerHTML = skeleton(); }, 220);
    renderChips();
    fetch(endpoint + '?' + p.toString(), { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: controller.signal, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('status ' + r.status); return r.json(); })
      .then(function (data) {
        if (seq !== latest) return;
        clearTimeout(skeletonTimer);
        results.innerHTML = data.html;
        var grid = results.querySelector('.items');
        if (grid) grid.classList.add('is-entering');
        if (window.LostLink && LostLink.animateIn) LostLink.animateIn(results);
        if (countEl) countEl.textContent = data.total;
        document.querySelectorAll('[data-total]').forEach(function (n) { n.textContent = data.total; });
        if (feedback) feedback.textContent = data.total === 1 ? '1 report found.' : data.total + ' reports found.';
        var url = window.location.pathname + (shareable ? '?' + shareable : '');
        if (opts.push) history.pushState({ page: page }, '', url); else history.replaceState({ page: page }, '', url);
        if (opts.scroll) { var top = form.getBoundingClientRect().top + window.scrollY - 70; window.scrollTo({ top: top, behavior: 'smooth' }); }
      })
      .catch(function (e) {
        if (e.name === 'AbortError' || seq !== latest) return;
        clearTimeout(skeletonTimer);
        results.innerHTML = '<div class="empty"><div class="empty__mark" aria-hidden="true"><i></i><b></b><i></i></div>' +
          '<p class="empty__title">Results could not load</p><p>Check your connection and try again. Your filters are kept.</p>' +
          '<button type="button" class="btn btn-sm" data-retry>Try again</button></div>';
        if (feedback) feedback.textContent = 'Search could not load.';
      })
      .finally(function () {
        if (seq !== latest) return;
        if (spinner) spinner.hidden = true;
        results.classList.remove('is-loading');
        results.removeAttribute('aria-busy');
      });
  }

  form.querySelectorAll('input[type=search], input[type=text]').forEach(function (el) {
    el.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(function () { run(1); }, 260); });
  });
  form.querySelectorAll('select, input[type=date], input[type=radio]').forEach(function (el) {
    el.addEventListener('change', function () { run(1); });
  });
  form.addEventListener('submit', function (e) { e.preventDefault(); clearTimeout(timer); run(1); });

  document.addEventListener('click', function (e) {
    var pageBtn = e.target.closest('#results [data-page]');
    if (pageBtn) { e.preventDefault(); run(Number(pageBtn.dataset.page), { push: true, scroll: true }); return; }
    if (e.target.closest('[data-retry]')) { run(1); return; }
    var clear = e.target.closest('[data-clear]');
    if (clear) {
      var k = clear.dataset.clear;
      if (k === 'all') {
        Array.from(form.elements).forEach(function (el) {
          if (el.type === 'radio') el.checked = el.value === '';
          else if (/^(search|text|date|select-one)$/.test(el.type) && el.name !== 'sort') el.value = '';
        });
        document.querySelectorAll('[data-category-chip]').forEach(function (c) { c.setAttribute('aria-pressed', 'false'); });
        run(1);
      } else {
        var el = form.elements[k];
        if (el && el.length && el[0] && el[0].type === 'radio') { form.querySelector('[name=' + k + '][value=""]').checked = true; }
        else if (el) el.value = '';
        run(1);
      }
      return;
    }
    var cat = e.target.closest('[data-category-chip]');
    if (cat) {
      var sel = form.elements.category;
      sel.value = sel.value === cat.dataset.categoryChip ? '' : cat.dataset.categoryChip;
      document.querySelectorAll('[data-category-chip]').forEach(function (c) { c.setAttribute('aria-pressed', String(c.dataset.categoryChip === sel.value)); });
      run(1);
    }
  });
  form.elements.category && form.elements.category.addEventListener('change', function () {
    document.querySelectorAll('[data-category-chip]').forEach(function (c) { c.setAttribute('aria-pressed', String(c.dataset.categoryChip === form.elements.category.value)); });
  });

  window.addEventListener('popstate', function () { window.location.reload(); });
  renderChips();
})();
