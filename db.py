"""Koneksi database, inisialisasi skema, dan data seed Second Luxe."""
import os
import re
import sqlite3
from datetime import datetime, timedelta

from flask import current_app, g
from werkzeug.security import generate_password_hash

FMT = "%Y-%m-%d %H:%M:%S"


def now_str(delta=None):
    """Waktu UTC sebagai teks; delta opsional (timedelta)."""
    t = datetime.utcnow() + (delta or timedelta())
    return t.strftime(FMT)


def get_db():
    """Satu koneksi per request, disimpan di flask.g."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def slugify(text):
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "item"


def init_db():
    """Buat ulang semua tabel lalu isi data seed."""
    db = get_db()
    schema = os.path.join(current_app.root_path, "schema.sql")
    with open(schema, encoding="utf-8") as f:
        db.executescript(f.read())
    seed_db(db)
    db.commit()


def init_app(app):
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def init_db_command():
        """Reset database + isi data seed: flask --app app init-db"""
        init_db()
        print("Database berhasil dibuat dan diisi data seed.")

    # Buat otomatis saat pertama kali dijalankan
    if not os.path.exists(app.config["DATABASE"]):
        with app.app_context():
            init_db()


# ---------------------------------------------------------------- DATA SEED
BRANDS = [
    ("Gucci", "Rumah mode Italia berdiri 1921, terkenal lewat pola GG dan detail kuda."),
    ("Dior", "Maison Prancis dengan siluet feminin dan kerajinan haute couture."),
    ("Louis Vuitton", "Pembuat koper perjalanan sejak 1854, ikon kanvas Monogram."),
    ("Chanel", "Klasik abadi Prancis: tweed, kulit quilted, dan rantai emas."),
    ("Hermes", "Atelier kulit Prancis, dikenal lewat tas jahit tangan dan syal sutra."),
    ("Prada", "Rumah mode Milan, terkenal dengan nilon dan kulit Saffiano."),
    ("Nike", "Merek olahraga dengan sneakers ikonik dan edisi terbatas."),
    ("Adidas", "Merek olahraga Jerman, tiga garis dan kolaborasi Yeezy."),
    ("Balenciaga", "Rumah mode dengan siluet berani dan sneakers berukuran besar."),
    ("Coach", "Merek kulit Amerika, klasik dan lebih terjangkau."),
]
CATEGORIES = ["Tas", "Pakaian", "Sepatu", "Aksesoris"]

# (nama, brand, kategori, gender, ukuran, warna, bahan, skor, catatan kondisi,
#  minus, kelengkapan, harga, harga baru, status, featured)
PRODUCTS = [
    ("GG Marmont Small Shoulder Bag", "Gucci", "Tas", "wanita", "Small", "Hitam", "Kulit matelasse", 9,
     "Kulit mulus, hardware emas cerah, interior bersih.", "Goresan halus di sudut bawah kiri.",
     "dustbag, box, nota", 18500000, 31000000, "available", 1),
    ("Neverfull MM Monogram", "Louis Vuitton", "Tas", "wanita", "MM", "Cokelat Monogram", "Kanvas Monogram, trim vachetta", 8,
     "Kanvas kokoh, vachetta mulai berpatina merata.", "Patina pada tali, noda tipis di dalam.",
     "dustbag, nota", 17500000, 25000000, "available", 1),
    ("Classic Flap Medium Caviar", "Chanel", "Tas", "wanita", "Medium", "Hitam", "Kulit caviar, hardware emas", 9,
     "Kulit caviar tegas, rantai tanpa lecet berarti.", "Lecet sangat tipis pada hardware CC.",
     "dustbag, box, kartu, nota", 45000000, 98000000, "available", 1),
    ("Book Tote Small Oblique", "Dior", "Tas", "wanita", "Small", "Biru Oblique", "Kanvas bordir", 9,
     "Bordir rapi, tanpa noda, bentuk tas terjaga.", "Tidak ada.",
     "dustbag, nota", 24000000, 38000000, "sold", 0),
    ("Evelyne PM Clemence", "Hermes", "Tas", "unisex", "PM", "Gold", "Kulit Clemence", 8,
     "Kulit lembut, jahitan utuh, stempel jelas.", "Bekas gesekan pada tali bahu.",
     "dustbag, nota", 29000000, 42000000, "available", 1),
    ("Re-Edition 2005 Nylon", "Prada", "Tas", "wanita", "Small", "Hitam", "Re-Nylon, logo segitiga", 9,
     "Nilon bersih, ritsleting lancar.", "Tidak ada.",
     "dustbag, kartu", 6800000, 22000000, "held", 0),
    ("Tabby Shoulder Bag 26", "Coach", "Tas", "wanita", "26", "Krem", "Kulit pebbled", 8,
     "Kulit pebbled terawat, kunci Signature mulus.", "Noda tinta kecil di lapisan dalam.",
     "dustbag", 3200000, 7500000, "available", 0),
    ("Hourglass XS Top Handle", "Balenciaga", "Tas", "wanita", "XS", "Hitam", "Kulit halus", 9,
     "Bentuk jam pasir terjaga, kilap kulit baik.", "Goresan tipis di dasar tas.",
     "dustbag, box, nota", 12500000, 28000000, "available", 1),
    ("GG Logo Hoodie", "Gucci", "Pakaian", "unisex", "M", "Abu Tua", "Katun jersey", 9,
     "Katun tebal, cetakan logo utuh, tanpa pilling.", "Tidak ada.",
     "tag, nota", 4800000, 14000000, "available", 0),
    ("Firebird Track Jacket", "Adidas", "Pakaian", "unisex", "M", "Biru Navy", "Poliester tricot", 8,
     "Warna segar, tiga garis utuh.", "Bekas lipatan di kerah.",
     "tag", 800000, 1800000, "available", 0),
    ("Tweed Jacket Collarless", "Chanel", "Pakaian", "wanita", "38", "Krem Multi", "Tweed wol", 9,
     "Tweed utuh, kancing logo lengkap, lapisan bersih.", "Tidak ada.",
     "hanger, nota", 22000000, 65000000, "available", 1),
    ("Re-Nylon Windbreaker", "Prada", "Pakaian", "pria", "L", "Hitam", "Re-Nylon", 9,
     "Nilon ringan, ritsleting lancar, logo utuh.", "Tidak ada.",
     "tag", 5500000, 17000000, "sold", 0),
    ("Logo Tee Oversized", "Gucci", "Pakaian", "unisex", "L", "Putih", "Katun", 9,
     "Cetakan jelas, warna putih masih cerah.", "Tidak ada.",
     "tag", 3400000, 8500000, "available", 0),
    ("Cities Hoodie Paris", "Balenciaga", "Pakaian", "unisex", "M", "Hitam Pudar", "Katun terry", 8,
     "Efek pudar memang bagian dari desain.", "Benang longgar di lengan kanan.",
     "tag", 7900000, 19000000, "available", 0),
    ("Air Jordan 1 Retro High OG", "Nike", "Sepatu", "pria", "EU 43", "Merah Hitam", "Kulit", 8,
     "Sol atas masih tebal, kulit tanpa retak.", "Kerutan di toebox, noda halus di midsole.",
     "box, nota", 3800000, 5000000, "available", 1),
    ("Yeezy Boost 350 V2", "Adidas", "Sepatu", "unisex", "EU 42", "Zebra", "Primeknit", 8,
     "Primeknit bersih, sol Boost lembut.", "Kekuningan tipis di sol tengah.",
     "box", 2600000, 4200000, "available", 0),
    ("Ace Sneakers Bee", "Gucci", "Sepatu", "wanita", "EU 38", "Putih", "Kulit", 9,
     "Kulit putih bersih, motif lebah utuh.", "Lecet kecil di tumit.",
     "dustbag, box", 5200000, 10500000, "available", 0),
    ("B23 High-Top Oblique", "Dior", "Sepatu", "pria", "EU 42", "Abu Oblique", "Kanvas Oblique", 9,
     "Kanvas rapi, sol hampir tak terpakai.", "Tidak ada.",
     "dustbag, box, nota", 9800000, 18000000, "available", 1),
    ("Triple S Sneaker", "Balenciaga", "Sepatu", "pria", "EU 41", "Putih Abu", "Mesh, kulit sintetis", 8,
     "Sol tiga lapis utuh, jahitan kencang.", "Noda di mesh sisi dalam.",
     "dustbag, box", 8500000, 14500000, "sold", 0),
    ("Carre 90 Silk Scarf", "Hermes", "Aksesoris", "wanita", "90 x 90 cm", "Biru Emas", "Sutra twill", 9,
     "Warna tajam, tepi jahit tangan utuh.", "Tidak ada.",
     "box", 4200000, 6800000, "available", 1),
    ("GG Supreme Belt", "Gucci", "Aksesoris", "unisex", "90 cm", "Hitam", "Kulit, kepala logam GG", 9,
     "Kulit lentur, kepala sabuk mengilap.", "Goresan tipis di gesper.",
     "dustbag, nota", 3900000, 7200000, "available", 0),
    ("Slender Wallet Monogram", "Louis Vuitton", "Aksesoris", "pria", "Slim", "Cokelat Monogram", "Kanvas Monogram", 8,
     "Kanvas kokoh, kompartemen kartu rapi.", "Tepi kulit mulai tipis.",
     "dustbag", 3100000, 5400000, "available", 0),
    ("CC Logo Sunglasses", "Chanel", "Aksesoris", "wanita", "One size", "Hitam Emas", "Asetat", 9,
     "Lensa bening, engsel kencang.", "Goresan mikro di lensa kiri.",
     "case, kain lap, kartu", 4900000, 9000000, "available", 0),
    ("Saffiano Card Holder", "Prada", "Aksesoris", "unisex", "Kecil", "Hitam", "Kulit Saffiano", 9,
     "Kulit Saffiano tahan gores, plat logo mulus.", "Tidak ada.",
     "dustbag", 1400000, 3200000, "available", 0),
]

REVIEWS = [
    (1, "Anindya P.", 5, "Aslinya meyakinkan, sertifikatnya jelas. Kondisi sama persis dengan deskripsi."),
    (1, "Rizky M.", 5, "Packing aman, tas datang dengan dustbag lengkap. Sangat puas."),
    (1, "Salsabila K.", 4, "Bagus, ada goresan kecil seperti yang ditulis. Penjual jujur."),
    (2, "Dewi L.", 5, "Neverfull impian dengan harga masuk akal. Terima kasih Second Luxe."),
    (4, "Maya S.", 5, "Bordirnya rapi, tidak ada yang dikurangi dari foto."),
    (12, "Bagas A.", 5, "Windbreaker ringan, ukuran sesuai tabel. Pengiriman cepat."),
    (19, "Fajar N.", 4, "Sneakers bersih, noda sedikit seperti dijelaskan."),
    (15, "Citra R.", 5, "Jordan-nya asli, boleh dicek ke toko resmi. Recommended."),
]

BANNERS = [
    ("Barang branded, riwayat jelas", "Setiap produk dicek tim autentikasi sebelum tayang.",
     "Lihat koleksi", "/katalog", "cream", 1),
    ("Chanel, Hermes, Dior: koleksi pilihan", "Tas ikonik dengan kondisi 8/10 ke atas.",
     "Belanja tas", "/katalog?kategori=tas", "sand", 2),
    ("Jual barang branded Anda", "Kirim foto, dapat estimasi harga dalam hitungan menit.",
     "Jual ke kami", "/jual", "ink", 3),
]

BLOG = [
    ("Cara Mengenali Tas Branded Asli", "cara-mengenali-tas-branded-asli", "Panduan",
     "Lima hal yang dicek tim kami sebelum sebuah tas lolos autentikasi.",
     "Autentikasi dimulai dari detail kecil: jahitan, hardware, dan nomor seri.\n\n"
     "Periksa kerapian jahitan. Rumah mode besar menjaga jumlah jahitan per sentimeter tetap konsisten.\n\n"
     "Periksa berat dan bunyi hardware. Logam asli terasa padat, dan resleting meluncur tanpa tersendat.\n\n"
     "Terakhir, cocokkan nomor seri dengan format produksi merek tersebut. Bila ragu, minta sertifikat dari penjual."),
    ("Merawat Tas Kulit Agar Awet", "merawat-tas-kulit-agar-awet", "Perawatan",
     "Kulit butuh perlakuan berbeda dari kanvas. Ini rutinitas sederhananya.",
     "Simpan tas di dustbag, bukan plastik, supaya kulit tetap bernapas.\n\n"
     "Isi tas dengan kertas bersih agar bentuknya terjaga, dan hindari paparan matahari langsung.\n\n"
     "Bersihkan dengan kain mikrofiber lembap. Untuk kulit halus, gunakan kondisioner sekali tiap tiga bulan."),
    ("Mengapa Preloved Branded Jadi Pilihan Cerdas", "mengapa-preloved-branded-pilihan-cerdas", "Gaya Hidup",
     "Nilai jual kembali tas klasik sering bertahan, bahkan naik.",
     "Banyak tas klasik tidak kehilangan nilai sebesar barang fashion biasa.\n\n"
     "Dengan membeli preloved, Anda mendapat desain yang sudah teruji waktu pada harga yang lebih ringan.\n\n"
     "Syaratnya satu: beli dari penjual yang transparan soal kondisi dan keaslian."),
    ("Panduan Ukuran Sepatu Sneakers Mewah", "panduan-ukuran-sepatu-sneakers-mewah", "Panduan",
     "Ukuran tiap merek berbeda. Cek tabel ini sebelum membeli.",
     "Nike cenderung pas, sementara Balenciaga dan Dior sering lebih besar setengah ukuran.\n\n"
     "Ukur panjang telapak kaki di sore hari, saat kaki paling mengembang.\n\n"
     "Bila di antara dua ukuran, pilih yang lebih besar dan gunakan insole tambahan."),
    ("Mengenal Sertifikat Keaslian Digital Kami", "mengenal-sertifikat-keaslian-digital", "Kepercayaan",
     "Setiap produk punya nomor sertifikat yang bisa dicek kapan saja.",
     "Sertifikat memuat nomor unik, detail produk, skor kondisi, dan tanggal verifikasi.\n\n"
     "Anda bisa membuka dan mencetaknya dari halaman produk, juga menyimpannya sebagai bukti kepemilikan.\n\n"
     "Jika Anda menjual kembali di masa depan, sertifikat ini membantu calon pembeli percaya."),
]

VOUCHERS = [
    ("WELCOME10", "Diskon 10% untuk pembeli baru", "percent", 10, 500000, 500000, 100, 365),
    ("LUXE50K", "Potongan Rp 50.000 min. belanja Rp 1 juta", "fixed", 50000, 1000000, None, 200, 365),
    ("HEMAT5", "Diskon 5% min. belanja Rp 5 juta", "percent", 5, 5000000, 1500000, 50, 180),
    ("GLAMOUR15", "Diskon 15% untuk belanja Rp 10 juta ke atas", "percent", 15, 10000000, 3000000, 20, 90),
]


def _description(p):
    """Deskripsi produk sederhana dari field (versi AI datang di Tahap 5)."""
    name, brand, cat, gender, size, color, material, score = p[:8]
    return (f"{brand} {name} warna {color} dengan bahan {material}. "
            f"Kondisi {score}/10. {p[8]} Minus: {p[9]}")


def seed_db(db):
    """Isi database dengan data demo yang lengkap."""
    # Pengguna demo
    db.execute("INSERT INTO users (name,email,password_hash,phone,role) VALUES (?,?,?,?,?)",
               ("Admin Second Luxe", "admin@secondluxe.com", generate_password_hash("admin123"),
                "081234567890", "admin"))
    db.execute("INSERT INTO users (name,email,password_hash,phone,role) VALUES (?,?,?,?,?)",
               ("Aulia Rahma", "user@secondluxe.com", generate_password_hash("user123"),
                "081298765432", "customer"))
    db.execute("""INSERT INTO addresses (user_id,label,recipient,phone,street,city,province,postal_code,is_default)
                  VALUES (2,'Rumah','Aulia Rahma','081298765432','Jl. Kartini No. 12',
                          'Bandar Lampung','Lampung','35214',1)""")

    # Brand dan kategori
    brand_id, cat_id = {}, {}
    for name, desc in BRANDS:
        cur = db.execute("INSERT INTO brands (name,slug,description) VALUES (?,?,?)",
                         (name, slugify(name), desc))
        brand_id[name] = cur.lastrowid
    for name in CATEGORIES:
        cur = db.execute("INSERT INTO categories (name,slug) VALUES (?,?)", (name, slugify(name)))
        cat_id[name] = cur.lastrowid

    # Produk + 3 foto placeholder per produk
    for i, p in enumerate(PRODUCTS, start=1):
        (name, brand, cat, gender, size, color, material, score, notes, flaws,
         completeness, price, original, status, featured) = p
        stock = 0 if status == "sold" else 1
        created = now_str(timedelta(days=-(3 + i * 2)))
        db.execute(
            """INSERT INTO products (name,slug,description,brand_id,category_id,gender,size,color,material,
               condition_score,condition_notes,flaws,completeness,price,original_price,stock,status,
               is_featured,certificate_no,created_at,views,sold_count)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, slugify(f"{brand} {name}"), _description(p), brand_id[brand], cat_id[cat], gender, size,
             color, material, score, notes, flaws, completeness, price, original, stock, status, featured,
             f"SL-2026-{i:04d}", created, 40 + (i * 37) % 260, 1 if status == "sold" else 0))
        for n in range(1, 4):
            db.execute("INSERT INTO product_images (product_id,url,is_primary,sort_order) VALUES (?,?,?,?)",
                       (i, f"/placeholder/{i}.svg?v={n}", 1 if n == 1 else 0, n))

    # Produk berstatus held: booking aktif oleh user demo
    held_id = next(i for i, p in enumerate(PRODUCTS, 1) if p[13] == "held")
    db.execute("INSERT INTO holds (product_id,user_id,expires_at,status) VALUES (?,?,?,'active')",
               (held_id, 2, now_str(timedelta(hours=20))))

    # Voucher
    for code, desc, typ, val, minp, maxd, quota, days in VOUCHERS:
        db.execute("""INSERT INTO vouchers (code,description,type,value,min_purchase,max_discount,quota,
                      valid_from,valid_until) VALUES (?,?,?,?,?,?,?,?,?)""",
                   (code, desc, typ, val, minp, maxd, quota,
                    now_str(timedelta(days=-30)), now_str(timedelta(days=days))))

    # Banner, blog, ulasan
    for title, sub, cta, url, tone, order in BANNERS:
        db.execute("INSERT INTO banners (title,subtitle,cta_text,cta_url,tone,sort_order) VALUES (?,?,?,?,?,?)",
                   (title, sub, cta, url, tone, order))
    for n, (title, slug, cat, excerpt, content) in enumerate(BLOG):
        db.execute("INSERT INTO blog_posts (title,slug,category,excerpt,content,created_at) VALUES (?,?,?,?,?,?)",
                   (title, slug, cat, excerpt, content, now_str(timedelta(days=-(5 + n * 6)))))
    for pid, who, rating, comment in REVIEWS:
        db.execute("INSERT INTO reviews (product_id,reviewer_name,rating,comment,created_at) VALUES (?,?,?,?,?)",
                   (pid, who, rating, comment, now_str(timedelta(days=-pid))))

    # Pesanan demo untuk produk yang sudah terjual (agar dashboard tidak kosong)
    sold = [(i, p) for i, p in enumerate(PRODUCTS, 1) if p[13] == "sold"]
    states = [("selesai", 9), ("dikirim", 3), ("diproses", 1)]
    for n, ((pid, p), (status, ago)) in enumerate(zip(sold, states), start=1):
        price, ship = p[11], 25000
        created = now_str(timedelta(days=-ago))
        cur = db.execute(
            """INSERT INTO orders (order_no,user_id,recipient,phone,street,city,province,postal_code,courier,
               shipping_cost,subtotal,discount,total,payment_method,payment_status,status,tracking_no,
               created_at,paid_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"SL-{created[:10].replace('-', '')}-{n:04d}", 2, "Aulia Rahma", "081298765432",
             "Jl. Kartini No. 12", "Bandar Lampung", "Lampung", "35214", "JNE", ship, price, 0, price + ship,
             "Transfer Bank", "paid", status, f"JNE00{n}2026{n}88" if status != "diproses" else None,
             created, created))
        oid = cur.lastrowid
        db.execute("INSERT INTO order_items (order_id,product_id,product_name,price) VALUES (?,?,?,?)",
                   (oid, pid, p[0], price))
        db.execute("INSERT INTO order_events (order_id,status,note,created_at) VALUES (?,?,?,?)",
                   (oid, "diproses", "Pembayaran diterima, pesanan diproses.", created))

    # Pengunjung simulasi untuk dashboard
    for d in range(30):
        for s in range(3 + d % 5):
            db.execute("INSERT INTO visits (session_id,path,created_at) VALUES (?,?,?)",
                       (f"seed-{d}-{s}", "/", now_str(timedelta(days=-d))))


def release_expired_holds():
    """Booking 24 jam yang lewat waktu otomatis dilepas (dicek saat request, tanpa scheduler)."""
    db = get_db()
    rows = db.execute("SELECT id, product_id FROM holds WHERE status='active' AND expires_at <= ?",
                      (now_str(),)).fetchall()
    for r in rows:
        db.execute("UPDATE holds SET status='expired' WHERE id=?", (r["id"],))
        db.execute("UPDATE products SET status='available' WHERE id=? AND status='held'", (r["product_id"],))
    if rows:
        db.commit()
