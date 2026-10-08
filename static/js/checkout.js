/* Second Luxe - JavaScript checkout dan pembayaran.
   Semua angka final dihitung ulang di server; skrip ini hanya memperbarui tampilan. */
(function () {
  'use strict';
  var $ = function (sel, ctx) { return (ctx || document).querySelector(sel); };
  var $$ = function (sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); };

  /* ---------- Halaman checkout ---------- */
  var form = $('#checkout-form');
  if (form) {
    var subtotal = parseInt(form.dataset.subtotal, 10) || 0;
    var discount = parseInt(form.dataset.discount, 10) || 0;
    var shipping = parseInt(form.dataset.shipping, 10) || 0;
    var rp = window.SL.rupiah;

    var render = function () {
      var total = Math.max(subtotal - discount + shipping, 0);
      $('#sum-discount').textContent = discount ? '-' + rp(discount) : 'Rp 0';
      $('#sum-shipping').textContent = shipping ? rp(shipping) : 'Pilih kurir';
      $('#sum-total').textContent = rp(total);
      // Perkiraan cicilan per bulan
      $$('input[name="payment"]').forEach(function (r) {
        var months = parseInt(r.dataset.months, 10) || 0;
        var note = $('[data-monthly]', r.closest('.choice'));
        if (!note) return;
        if (!months) { note.textContent = ''; return; }
        note.textContent = (subtotal - discount + shipping) >= 1000000
          ? months + ' x ' + rp(Math.round(total / months)) + ' per bulan'
          : 'Belum memenuhi minimal belanja Rp 1.000.000';
      });
    };

    var readShipping = function () {
      var c = $('input[name="courier"]:checked');
      shipping = c && !c.disabled ? parseInt(c.dataset.cost, 10) || 0 : 0;
    };

    // Ganti provinsi: ambil ongkir tiap kurir dari server
    var province = $('#province');
    var loadQuotes = function () {
      var hint = $('#courier-hint');
      if (!province.value) { hint.textContent = 'Pilih provinsi dulu untuk melihat ongkir.'; return; }
      fetch(form.dataset.quoteUrl + '?provinsi=' + encodeURIComponent(province.value))
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (quotes) {
          $$('#courier-list .choice').forEach(function (label) {
            var radio = $('input[type="radio"]', label);
            var q = quotes[radio.value];
            radio.disabled = !q;
            radio.dataset.cost = q ? q.cost : 0;
            $('[data-eta]', label).textContent = q ? q.eta : 'Belum tersedia untuk provinsi ini';
            $('[data-cost-label]', label).textContent = q ? rp(q.cost) : '';
            if (!q && radio.checked) radio.checked = false;
          });
          hint.textContent = 'Ongkir simulasi berdasarkan provinsi tujuan.';
          readShipping();
          render();
        })
        .catch(function () { hint.textContent = 'Gagal memuat ongkir. Coba pilih provinsi lagi.'; });
    };
    province.addEventListener('change', loadQuotes);
    $('#courier-list').addEventListener('change', function () { readShipping(); render(); });

    // Pilih alamat tersimpan: isi otomatis form
    var saved = $('#saved-address');
    if (saved) {
      saved.addEventListener('change', function () {
        var o = saved.options[saved.selectedIndex];
        if (!o || !o.value) return;
        $('#recipient').value = o.dataset.recipient;
        $('#phone').value = o.dataset.phone;
        $('#street').value = o.dataset.street;
        $('#city').value = o.dataset.city;
        $('#postal_code').value = o.dataset.postal;
        province.value = o.dataset.province;
        loadQuotes();
      });
    }

    // Voucher: cek ke server
    var vInput = $('#voucher'), vMsg = $('#voucher-msg'), vBtn = $('#voucher-apply');
    var applyVoucher = function () {
      var code = vInput.value.trim();
      if (!code) { discount = 0; vMsg.textContent = ''; render(); return; }
      vBtn.disabled = true;
      window.SL.postJSON(form.dataset.voucherUrl, { code: code })
        .then(function (res) {
          discount = res.ok ? res.discount : 0;
          vMsg.textContent = res.message || '';
          vMsg.className = 'small mt-1 mb-0 ' + (res.ok ? 'verified-text' : 'sale-text');
          render();
        })
        .catch(function () { vMsg.textContent = 'Voucher tidak dapat dicek sekarang.'; vMsg.className = 'small mt-1 mb-0 sale-text'; })
        .then(function () { vBtn.disabled = false; });
    };
    vBtn.addEventListener('click', applyVoucher);
    vInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); applyVoucher(); } });
    $$('input[name="payment"]').forEach(function (r) { r.addEventListener('change', render); });

    readShipping();
    render();
  }

  /* ---------- Halaman pembayaran: rapikan input kartu (tanpa menyimpan) ---------- */
  var num = $('#card_number'), exp = $('#card_exp'), cvv = $('#card_cvv');
  if (num) {
    num.addEventListener('input', function () {
      var d = num.value.replace(/\D/g, '').slice(0, 19);
      num.value = d.replace(/(.{4})/g, '$1 ').trim();
    });
  }
  if (exp) {
    exp.addEventListener('input', function () {
      var d = exp.value.replace(/\D/g, '').slice(0, 4);
      exp.value = d.length > 2 ? d.slice(0, 2) + '/' + d.slice(2) : d;
    });
  }
  if (cvv) cvv.addEventListener('input', function () { cvv.value = cvv.value.replace(/\D/g, '').slice(0, 4); });

  // Cegah klik ganda pada tombol bayar
  var payForm = $('#pay-form');
  if (payForm) {
    payForm.addEventListener('submit', function () {
      var b = $('button[type="submit"]', payForm);
      if (b) { b.disabled = true; b.textContent = 'Memproses...'; }
    });
  }
})();
