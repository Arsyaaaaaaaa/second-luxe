# Second Luxe — Tahap 6 Admin

Tahap 6 melanjutkan project Tahap 4. Tahap 5 (chatbot/AI) sengaja dilewati sesuai permintaan. Fitur Jual ke Kami tetap memakai fallback estimasi berbasis aturan yang sudah ada di Tahap 4.

## Menjalankan

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Salin `.env.example` menjadi `.env`, lalu isi minimal:

```env
SECRET_KEY=ganti-dengan-kunci-rahasia
WHATSAPP_NUMBER=6281234567890
FLASK_DEBUG=1
```

Jalankan:

```bash
python app.py
```

Buka `http://127.0.0.1:5000`.

## Akun demo admin

- Email: `admin@secondluxe.com`
- Password: `admin123`

Setelah login, pilih **Panel Admin** atau buka `/admin`.

## Fitur Tahap 6

- Dashboard statistik dan grafik penjualan 7 hari.
- CRUD produk, upload banyak foto, foto utama, hapus foto, status, stok, featured.
- Kelola pesanan dan nomor resi.
- Kelola pengguna, role, dan status aktif.
- Moderasi ulasan.
- Kelola Tawar Harga: terima, tolak, counter.
- Kelola Jual ke Kami: kirim penawaran final atau tolak.
- Kelola brand, kategori, voucher, banner, dan artikel blog.
- Laporan penjualan CSV.
- Panduan Admin.

## Foto produk

Foto yang di-upload admin disimpan otomatis di:

`static/uploads/products/`

Jika produk dibuat tanpa foto, sistem memakai placeholder lokal `/placeholder/<id>.svg` sehingga UI tetap terlihat.

## Catatan

Pembayaran tetap simulasi sesuai project UTS. Jangan memasukkan data kartu kredit asli. Database lokal tersimpan di `instance/secondluxe.sqlite`.
