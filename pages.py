"""Blueprint halaman pendukung: kepercayaan, sertifikat, blog, FAQ, kontak, newsletter, SEO, error."""
from datetime import datetime
from xml.sax.saxutils import escape

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request)
from werkzeug.exceptions import BadRequest, Forbidden, NotFound, RequestEntityTooLarge

import db as database
from utils import EMAIL_RE, clean, client_ip, rate_exceeded, rate_hit, redirect_back, send_email_simulated

bp = Blueprint("pages", __name__)

BLOG_PER_PAGE = 6

# Pertanyaan FAQ dipakai di halaman dan di data terstruktur (schema.org FAQPage)
FAQ = [
    ("Bagaimana saya tahu barangnya asli?",
     "Setiap produk diperiksa tim autentikasi sebelum tayang: material, jahitan, hardware, nomor seri, dan "
     "kelengkapan. Hasilnya dicatat dalam sertifikat keaslian digital yang bisa dibuka dari halaman produk."),
    ("Apa itu skala kondisi 1 sampai 10?",
     "Skor internal kami. 9-10 seperti baru, 8 sangat baik, 6-7 baik dengan tanda pemakaian wajar, 4-5 ada minus "
     "yang terlihat. Minus selalu ditulis jujur di bagian Minus dan Cacat."),
    ("Mengapa stok setiap produk hanya satu?",
     "Semua barang kami preloved dan unik. Begitu terjual, produk tidak akan kembali. Gunakan Booking 24 Jam bila "
     "perlu waktu untuk memutuskan."),
    ("Bagaimana cara kerja Booking 24 Jam?",
     "Produk ditahan atas namamu selama 24 jam tanpa biaya dan masuk ke keranjang. Jika tidak dibayar dalam "
     "waktu itu, produk otomatis tersedia lagi untuk pembeli lain."),
    ("Bisakah saya menawar harga?",
     "Bisa. Tekan Tawar Harga di halaman produk (minimal 60% dari harga). Admin bisa menerima, menolak, atau "
     "membalas dengan harga lain. Jika diterima, produk ditahan 24 jam dengan harga kesepakatan."),
    ("Apa saja metode pembayarannya?",
     "Transfer bank, e-wallet, QRIS, kartu kredit, dan cicilan 0% 3 atau 6 bulan untuk belanja minimal Rp 1.000.000. "
     "Catatan: pada situs demo ini pembayaran hanya simulasi."),
    ("Berapa lama pengiriman?",
     "Kami mengirim lewat JNE, J&T, SiCepat, atau Gojek. Pesanan dikemas maksimal 1x24 jam setelah pembayaran, "
     "lalu estimasi tiba mengikuti kurir dan tujuan."),
    ("Bagaimana jika barang tidak sesuai?",
     "Kamu punya waktu 7 hari setelah barang diterima untuk mengajukan retur. Lihat halaman Kebijakan Retur dan "
     "Garansi Uang Kembali untuk syarat lengkapnya."),
    ("Bagaimana cara menjual barang ke Second Luxe?",
     "Buka menu Jual ke Kami, isi detail dan unggah foto. Kamu langsung mendapat estimasi harga awal, lalu tim "
     "kami memberi penawaran final setelah memeriksa data."),
    ("Apakah data kartu saya aman?",
     "Situs ini hanya simulasi pembayaran. Nomor kartu tidak pernah disimpan atau dicatat, hanya dicek formatnya "
     "lalu dibuang."),
]


# ---------------------------------------------------------------- Kepercayaan
@bp.route("/autentikasi")
def authentication():
    return render_template("pages/authentication.html")


@bp.route("/garansi")
def guarantee():
    return render_template("pages/guarantee.html")


@bp.route("/retur")
def returns():
    return render_template("pages/returns.html")


@bp.route("/cek-sertifikat")
def check_certificate():
    """Form 'cek nomor sertifikat' di halaman autentikasi."""
    no = clean(request.args.get("no"), 30).upper()
    if no and database.get_db().execute("SELECT 1 FROM products WHERE certificate_no=?", (no,)).fetchone():
        return redirect("/sertifikat/" + no)
    flash("Nomor sertifikat tidak ditemukan. Contoh format: SL-2026-0001.", "error")
    return redirect("/autentikasi#cek")


@bp.route("/sertifikat/<no>")
def certificate(no):
    """Halaman sertifikat keaslian yang bisa dicetak."""
    p = database.get_db().execute("""
        SELECT p.*, b.name AS brand_name, c.name AS category_name FROM products p
        JOIN brands b ON b.id=p.brand_id JOIN categories c ON c.id=p.category_id
        WHERE p.certificate_no=?""", (no.upper(),)).fetchone()
    if p is None:
        abort(404)
    items = [c.strip() for c in (p["completeness"] or "").split(",") if c.strip()]
    return render_template("pages/certificate.html", p=p, completeness=items)


# ---------------------------------------------------------------- Blog
@bp.route("/blog")
def blog():
    db = database.get_db()
    cat = clean(request.args.get("kategori"), 40)
    where, params = " WHERE is_published=1", []
    if cat:
        where += " AND category=?"
        params.append(cat)
    total = db.execute("SELECT COUNT(*) FROM blog_posts" + where, params).fetchone()[0]
    pages = max((total + BLOG_PER_PAGE - 1) // BLOG_PER_PAGE, 1)
    page = min(max(request.args.get("hal", 1, type=int), 1), pages)
    posts = db.execute("SELECT id,title,slug,excerpt,category,created_at,length(content) AS n FROM blog_posts" + where +
                       " ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [BLOG_PER_PAGE, (page - 1) * BLOG_PER_PAGE]).fetchall()
    cats = [r[0] for r in db.execute("SELECT DISTINCT category FROM blog_posts WHERE is_published=1 AND category IS NOT NULL ORDER BY category")]
    return render_template("pages/blog_list.html", posts=posts, cats=cats, cat=cat, page=page, pages=pages)


@bp.route("/blog/<slug>")
def blog_post(slug):
    db = database.get_db()
    post = db.execute("SELECT * FROM blog_posts WHERE slug=? AND is_published=1", (slug,)).fetchone()
    if post is None:
        abort(404)
    # Isi disimpan sebagai teks biasa: pisah per paragraf, di-escape otomatis oleh Jinja (tanpa |safe)
    paragraphs = [x.strip() for x in post["content"].split("\n\n") if x.strip()]
    others = db.execute("SELECT title,slug,excerpt,category FROM blog_posts WHERE is_published=1 AND id!=? "
                        "ORDER BY created_at DESC LIMIT 3", (post["id"],)).fetchall()
    json_ld = {"@context": "https://schema.org", "@type": "BlogPosting", "headline": post["title"],
               "description": post["excerpt"], "datePublished": post["created_at"][:10],
               "author": {"@type": "Organization", "name": "Second Luxe"}}
    return render_template("pages/blog_post.html", post=post, paragraphs=paragraphs, others=others,
                           minutes=max(1, round(len(post["content"].split()) / 200)), json_ld=json_ld)


# ---------------------------------------------------------------- Tentang, FAQ, Kontak
@bp.route("/tentang")
def about():
    return render_template("pages/about.html")


@bp.route("/faq")
def faq():
    json_ld = {"@context": "https://schema.org", "@type": "FAQPage",
               "mainEntity": [{"@type": "Question", "name": q,
                               "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ]}
    return render_template("pages/faq.html", faq=FAQ, json_ld=json_ld)


CONTACT_TOPICS = ["Pertanyaan produk", "Pesanan dan pengiriman", "Retur atau garansi", "Kerja sama", "Lainnya"]


@bp.route("/kontak", methods=["GET", "POST"])
def contact():
    form, errors = {"name": "", "email": "", "topic": CONTACT_TOPICS[0], "message": ""}, {}
    if request.method == "POST":
        form = {"name": clean(request.form.get("name"), 80), "email": clean(request.form.get("email"), 254),
                "topic": request.form.get("topic", ""), "message": clean(request.form.get("message"), 1500)}
        if len(form["name"]) < 2:
            errors["name"] = "Nama minimal 2 karakter."
        if not EMAIL_RE.match(form["email"]):
            errors["email"] = "Alamat email tidak valid."
        if form["topic"] not in CONTACT_TOPICS:
            errors["topic"] = "Pilih topik."
        if len(form["message"]) < 10:
            errors["message"] = "Pesan minimal 10 karakter."
        if not errors and rate_exceeded(f"contact:{client_ip()}", 5, 3600):
            flash("Terlalu banyak pesan dalam satu jam. Coba lagi nanti atau hubungi kami lewat WhatsApp.", "error")
        elif not errors:
            rate_hit(f"contact:{client_ip()}", 3600)
            # Simulasi: pesan masuk dicatat ke instance/emails.log (admin dan pengirim)
            send_email_simulated("admin@secondluxe.com", f"[Kontak] {form['topic']} dari {form['name']}",
                                 f"Dari: {form['name']} <{form['email']}>\n\n{form['message']}")
            send_email_simulated(form["email"], "Pesanmu sudah kami terima",
                                 f"Halo {form['name']},\n\nTerima kasih sudah menghubungi Second Luxe. "
                                 "Tim kami membalas dalam 1x24 jam kerja.\n\nSecond Luxe")
            flash("Pesan terkirim. Kami membalas dalam 1x24 jam kerja.", "success")
            return redirect("/kontak")
        else:
            flash("Periksa kembali data yang ditandai.", "error")
    return render_template("pages/contact.html", form=form, errors=errors, topics=CONTACT_TOPICS)


# ---------------------------------------------------------------- Newsletter
@bp.post("/newsletter")
def newsletter():
    email = clean(request.form.get("email"), 120).lower()
    if not EMAIL_RE.match(email):
        flash("Alamat email tidak valid.", "error")
    elif rate_exceeded(f"newsletter:{client_ip()}", 10, 3600):
        flash("Terlalu banyak percobaan. Coba lagi nanti.", "error")
    else:
        rate_hit(f"newsletter:{client_ip()}", 3600)
        db = database.get_db()
        cur = db.execute("INSERT OR IGNORE INTO newsletter (email) VALUES (?)", (email,))
        db.commit()
        if cur.rowcount:
            send_email_simulated(email, "Selamat datang di Second Luxe",
                                 "Terima kasih sudah berlangganan. Kamu akan jadi yang pertama tahu koleksi baru.")
            flash("Terima kasih! Kamu sudah berlangganan newsletter kami.", "success")
        else:
            flash("Email ini sudah terdaftar di newsletter kami.", "info")
    return redirect_back("/")


# ---------------------------------------------------------------- SEO
@bp.route("/robots.txt")
def robots():
    base = request.host_url.rstrip("/")
    body = ("User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /akun\nDisallow: /checkout\n"
            "Disallow: /bayar\nDisallow: /keranjang\nDisallow: /api/\n\n" f"Sitemap: {base}/sitemap.xml\n")
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    db = database.get_db()
    base = request.host_url.rstrip("/")
    urls = [(p, None) for p in ("/", "/katalog", "/autentikasi", "/garansi", "/retur", "/jual", "/blog",
                                "/tentang", "/faq", "/kontak")]
    urls += [(f"/produk/{r['slug']}", r["created_at"][:10]) for r in db.execute("SELECT slug,created_at FROM products")]
    urls += [(f"/blog/{r['slug']}", r["created_at"][:10]) for r in
             db.execute("SELECT slug,created_at FROM blog_posts WHERE is_published=1")]
    items = "".join(f"<url><loc>{escape(base + path)}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>"
                    for path, d in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'
    return Response(xml, mimetype="application/xml")


# ---------------------------------------------------------------- Halaman error
ERRORS = {
    400: ("Permintaan tidak valid", "Ada yang kurang tepat pada permintaanmu. Muat ulang halaman lalu coba lagi."),
    403: ("Akses ditolak", "Halaman ini hanya untuk admin."),
    404: ("Halaman tidak ditemukan", "Halaman yang kamu cari tidak ada atau sudah dipindahkan."),
    413: ("File terlalu besar", "Total ukuran unggahan melebihi 8 MB. Kecilkan foto lalu coba lagi."),
    429: ("Terlalu banyak permintaan", "Tunggu beberapa saat sebelum mencoba lagi."),
    500: ("Terjadi kesalahan", "Maaf, ada gangguan di sisi kami. Coba lagi sebentar lagi."),
}
DEFAULT_DESC = {400: BadRequest.description, 403: Forbidden.description, 404: NotFound.description,
                413: RequestEntityTooLarge.description}


def error_page(code, message=None):
    title, text = ERRORS[code]
    try:
        html = render_template("error.html", code=code, title=title, message=message or text)
    except Exception:       # jika template/DB ikut bermasalah, tetap tampilkan halaman sederhana
        html = (f"<!doctype html><meta charset='utf-8'><title>{code}</title>"
                f"<h1>{code} {escape(title)}</h1><p>{escape(message or text)}</p><a href='/'>Kembali ke beranda</a>")
    return html, code


@bp.app_errorhandler(400)
def err400(e):
    desc = getattr(e, "description", None)
    # Pesan buatan sendiri (Bahasa Indonesia) ditampilkan; pesan bawaan Werkzeug diganti teks umum
    return error_page(400, None if desc == DEFAULT_DESC[400] else desc)


@bp.app_errorhandler(403)
def err403(e):
    return error_page(403)


@bp.app_errorhandler(404)
def err404(e):
    return error_page(404)


@bp.app_errorhandler(413)
def err413(e):
    return error_page(413)


@bp.app_errorhandler(500)
def err500(e):
    current_app.logger.exception("Kesalahan server tidak tertangani")    # detail hanya di log server
    return error_page(500)
