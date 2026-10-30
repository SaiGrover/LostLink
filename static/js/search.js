// AJAX live search & filtering (synopsis Section 4.13).
// Issues asynchronous requests as the user types/filters and swaps in the
// returned results fragment without a full page reload.
(function () {
  const form = document.getElementById('search-form');
  if (!form) return;

  const results = document.getElementById('results');
  const countEl = document.getElementById('result-count');
  const spinner = document.getElementById('search-spinner');
  const endpoint = form.dataset.endpoint;
  let timer = null;
  let controller = null;
  let latest = 0;

  function serialize() {
    const params = new URLSearchParams(new FormData(form));
    return params.toString();
  }

  async function run(pushUrl = true, page = 1) {
    const sequence = ++latest;
    if (controller) controller.abort();
    controller = new AbortController();
    const qs = serialize() + '&page=' + page;
    if (spinner) spinner.style.visibility = 'visible';
    try {
      const resp = await fetch(endpoint + '?' + qs, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        signal: controller.signal,
      });
      if (!resp.ok) throw new Error('Search failed');
      const data = await resp.json();
      if (sequence !== latest) return;
      results.innerHTML = data.html;
      if (countEl) countEl.textContent = data.total;
      document.getElementById('search-feedback').textContent = `${data.total} reports found.`;
      if (pushUrl) {
        const clean = qs ? '?' + qs : '';
        window.history.replaceState(null, '', window.location.pathname + clean);
      }
    } catch (e) {
      if (e.name !== 'AbortError') document.getElementById('search-feedback').textContent = 'Search could not load. Please try Apply filters again.';
    } finally {
      if (spinner && sequence === latest) spinner.style.visibility = 'hidden';
    }
  }

  // Debounced text input; instant on filter change.
  results.addEventListener('click', (event) => {
    const button = event.target.closest('[data-page]');
    if (button) { run(true, Number(button.dataset.page)); form.scrollIntoView({block:'start'}); }
  });
  form.querySelectorAll('input[type="text"], input[type="search"]').forEach((el) => {
    el.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => run(), 250);
    });
  });
  form.querySelectorAll('select, input[type="date"], input[type="radio"], input[type="checkbox"]').forEach((el) => {
    el.addEventListener('change', () => run());
  });
  form.addEventListener('submit', (e) => { e.preventDefault(); run(); });

  // Filter chips that set a hidden field then re-run.
  document.querySelectorAll('[data-filter-type]').forEach((chip) => {
    chip.addEventListener('click', () => {
      const field = form.querySelector('input[name="type"]');
      const val = chip.dataset.filterType;
      field.value = (field.value === val) ? '' : val;
      document.querySelectorAll('[data-filter-type]').forEach(c => c.classList.remove('active-chip'));
      if (field.value) chip.classList.add('active-chip');
      run();
    });
  });
})();
