/* Second Luxe - JavaScript dasar: tema, drawer, dropdown, pencarian, flash.
   Skrip khusus halaman: checkout.js (checkout dan pembayaran). Chatbot menyusul di Tahap 5. */
(function () {
  'use strict';

  var root = document.documentElement;
  var $ = function (sel, ctx) { return (ctx || document).querySelector(sel); };
  var $$ = function (sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); };

  /* ---------- Util global untuk skrip lain ---------- */
  window.SL = {
    csrf: function () { var m = $('meta[name="csrf-token"]'); return m ? m.content : ''; },
    rupiah: function (n) { return 'Rp ' + Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.'); },
    postJSON: function (url, data) {
      return fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.SL.csrf() },
        body: JSON.stringify(data || {})
      }).then(function (r) { return r.json(); });
    }
  };

  /* ---------- Dark mode (disimpan di localStorage) ---------- */
  var themeBtn = $('[data-theme-toggle]');
  function syncThemeBtn() {
    if (themeBtn) themeBtn.setAttribute('aria-pressed', root.getAttribute('data-theme') === 'dark' ? 'true' : 'false');
  }
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('sl-theme', next); } catch (e) {}
      syncThemeBtn();
    });
    syncThemeBtn();
  }

  /* ---------- Drawer menu mobile ---------- */
  var drawer = $('#drawer');
  var overlay = $('.drawer-overlay');
  var drawerOpenBtn = $('[data-drawer-open]');
  function setDrawer(open) {
    if (!drawer) return;
    drawer.hidden = !open;
    overlay.hidden = !open;
    document.body.classList.toggle('no-scroll', open);
    if (drawerOpenBtn) drawerOpenBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) { var first = $('a, button', drawer); if (first) first.focus(); }
    else if (drawerOpenBtn) drawerOpenBtn.focus();
  }
  $$('[data-drawer-open]').forEach(function (b) { b.addEventListener('click', function () { setDrawer(true); }); });
  $$('[data-drawer-close]').forEach(function (b) { b.addEventListener('click', function () { setDrawer(false); }); });

  /* ---------- Dropdown (klik, bukan hover, agar ramah layar sentuh) ---------- */
  function closeDropdowns(except) {
    $$('.dropdown').forEach(function (d) {
      if (d === except) return;
      d.classList.remove('is-open');
      var b = $('.dropdown-btn', d);
      if (b) b.setAttribute('aria-expanded', 'false');
    });
  }
  $$('.dropdown-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var dd = btn.closest('.dropdown');
      var open = !dd.classList.contains('is-open');
      closeDropdowns(dd);
      dd.classList.toggle('is-open', open);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.dropdown')) closeDropdowns();
  });

  /* ---------- Panel pencarian ---------- */
  var searchBtn = $('[data-search-toggle]');
  var searchPanel = $('#search-panel');
  if (searchBtn && searchPanel) {
    searchBtn.addEventListener('click', function () {
      var open = searchPanel.hidden;
      searchPanel.hidden = !open;
      searchBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) $('#search-input').focus();
    });
  }

  /* ---------- Pesan flash ---------- */
  $$('[data-flash-close]').forEach(function (b) {
    b.addEventListener('click', function () { b.closest('.flash').remove(); });
  });
  $$('.flash-success').forEach(function (f) { setTimeout(function () { if (f.parentNode) f.remove(); }, 6000); });


  /* ---------- Saran pencarian otomatis ---------- */
  var sInput = $('#search-input');
  var sList = $('#search-suggestions');
  if (sInput && sList) {
    var timer = null, ctrl = null;
    var hideList = function () { sList.hidden = true; sList.innerHTML = ''; };
    sInput.addEventListener('input', function () {
      clearTimeout(timer);
      var q = sInput.value.trim();
      if (q.length < 2) { hideList(); return; }
      timer = setTimeout(function () {
        if (ctrl) ctrl.abort();
        ctrl = new AbortController();
        fetch(sInput.dataset.suggestUrl + '?q=' + encodeURIComponent(q), { signal: ctrl.signal })
          .then(function (r) { return r.ok ? r.json() : []; })
          .then(function (items) {
            sList.innerHTML = '';
            if (!items.length) { hideList(); return; }
            items.forEach(function (it) {
              var li = document.createElement('li');
              var a = document.createElement('a');
              a.href = it.url;
              var l = document.createElement('span'); l.textContent = it.label;       // textContent: aman dari XSS
              var t = document.createElement('small'); t.textContent = it.type;
              a.appendChild(l); a.appendChild(t); li.appendChild(a); sList.appendChild(li);
            });
            sList.hidden = false;
          })
          .catch(function () { /* diabaikan: pencarian biasa tetap berfungsi */ });
      }, 200);
    });
    sInput.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { var a = $('a', sList); if (a) { e.preventDefault(); a.focus(); } }
    });
    sList.addEventListener('keydown', function (e) {
      var cur = document.activeElement;
      if (e.key === 'ArrowDown' && cur.parentNode.nextElementSibling) { e.preventDefault(); $('a', cur.parentNode.nextElementSibling).focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); var prev = cur.parentNode.previousElementSibling; (prev ? $('a', prev) : sInput).focus(); }
    });
    document.addEventListener('click', function (e) { if (!e.target.closest('.search-panel')) hideList(); });
  }

  /* ---------- Galeri produk: ganti foto + zoom ---------- */
  var gallery = $('[data-gallery]');
  if (gallery) {
    var main = $('[data-gallery-main]', gallery);
    var img = $('[data-gallery-img]', gallery);
    $$('.gallery-thumbs button', gallery).forEach(function (b) {
      b.addEventListener('click', function () {
        img.src = b.dataset.src;
        $$('.gallery-thumbs button', gallery).forEach(function (x) { x.classList.remove('is-active'); });
        b.classList.add('is-active');
        main.classList.remove('is-zoom');
      });
    });
    var toggleZoom = function () { main.classList.toggle('is-zoom'); if (!main.classList.contains('is-zoom')) img.style.transformOrigin = ''; };
    main.addEventListener('click', toggleZoom);
    main.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleZoom(); } });
    main.addEventListener('mousemove', function (e) {
      if (!main.classList.contains('is-zoom')) return;
      var r = main.getBoundingClientRect();
      img.style.transformOrigin = ((e.clientX - r.left) / r.width * 100) + '% ' + ((e.clientY - r.top) / r.height * 100) + '%';
    });
    main.addEventListener('mouseleave', function () { main.classList.remove('is-zoom'); img.style.transformOrigin = ''; });
    if ($('.sticky-buy')) document.body.classList.add('has-sticky-buy');
  }

  /* ---------- Katalog: tombol filter (mobile) dan urutan otomatis ---------- */
  var filterBtn = $('[data-filter-toggle]');
  if (filterBtn) {
    filterBtn.addEventListener('click', function () {
      var box = $('#filters');
      var open = !box.classList.contains('is-open');
      box.classList.toggle('is-open', open);
      filterBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }
  $$('select[data-autosubmit]').forEach(function (s) { s.addEventListener('change', function () { s.form.submit(); }); });

  /* ---------- Konfirmasi sebelum aksi penting: <form data-confirm="Yakin?"> ---------- */
  $$('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      if (!window.confirm(f.getAttribute('data-confirm'))) e.preventDefault();
    });
  });

  /* ---------- Tombol cetak (sertifikat) ---------- */
  $$('[data-print]').forEach(function (b) { b.addEventListener('click', function () { window.print(); }); });

  /* ---------- Escape menutup semua panel ---------- */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    closeDropdowns();
    if (drawer && !drawer.hidden) setDrawer(false);
    if (searchPanel && !searchPanel.hidden) { searchPanel.hidden = true; if (searchBtn) searchBtn.setAttribute('aria-expanded', 'false'); }
  });
})();
