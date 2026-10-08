/* Second Luxe - skrip form Tahap 4: petunjuk tawaran, pratinjau dan validasi foto Jual ke Kami. */
(function () {
  'use strict';
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var fmt = function (n) { return window.SL ? window.SL.rupiah(n) : 'Rp ' + n; };

  /* ---------- Form tawaran: tampilkan persentase dari harga saat mengetik ---------- */
  var amount = $('#amount[data-price]');
  var hint = $('[data-offer-hint]');
  if (amount && hint) {
    var price = parseInt(amount.dataset.price, 10), min = parseInt(amount.dataset.min, 10);
    var base = hint.textContent;
    amount.addEventListener('input', function () {
      var digits = amount.value.replace(/\D/g, '').slice(0, 12);
      amount.value = digits ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, '.') : '';   // pemisah ribuan
      var n = parseInt(digits || '0', 10);
      if (!n) { hint.textContent = base; hint.className = 'hint'; return; }
      var pct = Math.round(n / price * 100);
      if (n >= price) { hint.textContent = 'Tawaran harus di bawah harga produk (' + fmt(price) + ').'; hint.className = 'hint error'; }
      else if (n < min) { hint.textContent = 'Terlalu rendah. Minimal ' + fmt(min) + '.'; hint.className = 'hint error'; }
      else { hint.textContent = pct + '% dari harga. Peluang diterima lebih besar di atas 80%.'; hint.className = 'hint'; }
    });
    if (amount.value) amount.dispatchEvent(new Event('input'));
  }

  /* ---------- Jual ke Kami: pratinjau foto + validasi tipe dan ukuran di sisi klien ---------- */
  var input = $('[data-photo-input]');
  if (input) {
    var box = $('[data-photo-previews]'), errBox = $('[data-photo-error]');
    var MAX = parseInt(input.dataset.max, 10) || 5, LIMIT = 5 * 1024 * 1024;
    var OK = ['image/jpeg', 'image/png', 'image/webp'];
    var showError = function (msg) { errBox.textContent = msg; errBox.hidden = !msg; };
    input.addEventListener('change', function () {
      box.innerHTML = ''; showError('');
      var files = Array.prototype.slice.call(input.files);
      var problem = '';
      if (files.length > MAX) problem = 'Maksimal ' + MAX + ' foto.';
      files.forEach(function (f) {
        if (OK.indexOf(f.type) === -1) problem = 'Foto harus berformat JPG, PNG, atau WEBP.';
        else if (f.size > LIMIT) problem = 'Ukuran tiap foto maksimal 5 MB.';
      });
      if (problem) { showError(problem); input.value = ''; return; }
      files.forEach(function (f) {
        var img = document.createElement('img');
        img.alt = 'Pratinjau foto';
        img.src = URL.createObjectURL(f);
        img.onload = function () { URL.revokeObjectURL(img.src); };
        box.appendChild(img);
      });
    });
  }
})();
