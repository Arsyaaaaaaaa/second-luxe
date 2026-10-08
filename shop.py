"""Blueprint toko: beranda, katalog, saran pencarian, dan halaman produk."""
import json
from urllib.parse import quote, urlencode

from flask import (Blueprint, abort, current_app, jsonify, render_template, request, session)
from werkzeug.datastructures import MultiDict

import db as database

bp = Blueprint("shop", __name__)

PER_PAGE = 12
SORTS = {
    "terbaru": "p.created_at DESC",
    "termurah": "p.price ASC",
    "termahal": "p.price DESC",
    "terpopuler": "(p.views + p.sold_count * 50) DESC",
}
SORT_LABELS = {"terbaru": "Terbaru", "termurah": "Harga terendah", "termahal": "Harga tertinggi",
               "terpopuler": "Terpopuler"}

# Query dasar produk + brand + kategori + foto utama
BASE_SELECT = """
SELECT p.*, b.name AS brand_name, b.slug AS brand_slug, c.name AS category_name, c.slug AS category_slug,
  (SELECT url FROM product_images i WHERE i.product_id = p.id
   ORDER BY i.is_primary DESC, i.sort_order LIMIT 1) AS image_url
FROM products p
JOIN brands b ON b.id = p.brand_id
JOIN categories c ON c.id = p.category_id
"""


def like_escape(text):
    """Escape karakter khusus LIKE agar input pengguna diperlakukan sebagai teks biasa."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def url_with(args, **changes):
    """Bangun query string dari args saat ini dengan perubahan. Nilai None = hapus kunci."""
    md = MultiDict(args)
    for k, v in changes.items():
        md.pop(k, None)
        if v is not None:
            vals = v if isinstance(v, (list, tuple)) else [v]
            for x in vals:
                md.add(k, x)
    qs = urlencode(list(md.items(multi=True)))
    return "?" + qs if qs else ""


@bp.app_context_processor
def helpers():
    return {"qs": lambda **kw: url_with(request.args, **kw)}


# ------------------------------------------------------------------ Beranda
@bp.route("/")
def index():
    db = database.get_db()
    latest = db.execute(BASE_SELECT + " WHERE p.status != 'sold' ORDER BY p.created_at DESC LIMIT 8").fetchall()
    shown = [p["id"] for p in latest]

    def pick(where, limit, order):
        marks = ",".join("?" * len(shown))
        rows = db.execute(f"{BASE_SELECT} WHERE {where} AND p.id NOT IN ({marks}) ORDER BY {order} LIMIT {limit}",
                          shown).fetchall()
        shown.extend(r["id"] for r in rows)
        return rows

    featured = pick("p.is_featured = 1 AND p.status != 'sold'", 4, "p.condition_score DESC, p.price DESC")
    popular = pick("p.status != 'sold'", 4, "p.views DESC")

    cats = db.execute("""
        SELECT c.name, c.slug,
          (SELECT COUNT(*) FROM products p WHERE p.category_id = c.id AND p.status != 'sold') AS total,
          (SELECT i.url FROM products p JOIN product_images i ON i.product_id = p.id AND i.is_primary = 1
           WHERE p.category_id = c.id AND p.status != 'sold' ORDER BY p.is_featured DESC, p.price DESC LIMIT 1) AS image_url
        FROM categories c ORDER BY c.id""").fetchall()
    banners = db.execute("SELECT * FROM banners WHERE is_active=1 ORDER BY sort_order").fetchall()
    # Dua foto untuk kolase hero: produk pilihan termahal
    hero_imgs = [r["image_url"] for r in db.execute(
        BASE_SELECT + " WHERE p.is_featured=1 AND p.status != 'sold' ORDER BY p.price DESC LIMIT 2")]
    return render_template("index.html", latest=latest, featured=featured, popular=popular, cats=cats,
                           banners=banners, hero_imgs=hero_imgs)


# ------------------------------------------------------------------ Katalog
def _read_filters():
    a = request.args
    f = {
        "q": a.get("q", "").strip()[:80],
        "kategori": a.get("kategori", "").strip()[:40],
        "brand": [x[:40] for x in a.getlist("brand")][:12],
        "ukuran": [x[:30] for x in a.getlist("ukuran")][:12],
        "warna": [x[:30] for x in a.getlist("warna")][:12],
        "gender": a.get("gender", "") if a.get("gender") in ("pria", "wanita") else "",
        "kondisi": a.get("kondisi", type=int),
        "harga_min": a.get("harga_min", type=int),
        "harga_max": a.get("harga_max", type=int),
        "urut": a.get("urut") if a.get("urut") in SORTS else "terbaru",
    }
    if f["kondisi"] is not None and not 1 <= f["kondisi"] <= 10:
        f["kondisi"] = None
    return f


def _where(f):
    """Susun klausa WHERE terparameter dari filter (tanpa penggabungan string input)."""
    clauses, params = [], []
    for word in f["q"].split()[:5]:
        like = "%" + like_escape(word) + "%"
        clauses.append("(p.name LIKE ? ESCAPE '\\' OR b.name LIKE ? ESCAPE '\\' OR p.color LIKE ? ESCAPE '\\' "
                       "OR p.material LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\')")
        params += [like] * 5
    if f["kategori"]:
        clauses.append("c.slug = ?"); params.append(f["kategori"])
    if f["brand"]:
        clauses.append("b.slug IN (%s)" % ",".join("?" * len(f["brand"]))); params += f["brand"]
    if f["ukuran"]:
        clauses.append("p.size IN (%s)" % ",".join("?" * len(f["ukuran"]))); params += f["ukuran"]
    if f["warna"]:
        clauses.append("(" + " OR ".join("p.color LIKE ? ESCAPE '\\'" for _ in f["warna"]) + ")")
        params += [like_escape(w) + "%" for w in f["warna"]]
    if f["gender"]:
        clauses.append("(p.gender = ? OR p.gender = 'unisex')"); params.append(f["gender"])
    if f["kondisi"]:
        clauses.append("p.condition_score >= ?"); params.append(f["kondisi"])
    if f["harga_min"] is not None:
        clauses.append("p.price >= ?"); params.append(f["harga_min"])
    if f["harga_max"] is not None:
        clauses.append("p.price <= ?"); params.append(f["harga_max"])
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _page_window(page, pages):
    """Daftar nomor halaman dengan None sebagai elipsis."""
    nums = sorted({1, pages, page - 1, page, page + 1} & set(range(1, pages + 1)))
    out, prev = [], 0
    for n in nums:
        if n - prev > 1:
            out.append(None)
        out.append(n)
        prev = n
    return out


@bp.route("/katalog")
def catalog():
    db = database.get_db()
    f = _read_filters()
    where, params = _where(f)
    join = " FROM products p JOIN brands b ON b.id=p.brand_id JOIN categories c ON c.id=p.category_id"
    total = db.execute("SELECT COUNT(*)" + join + where, params).fetchone()[0]
    pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    page = min(max(request.args.get("hal", 1, type=int), 1), pages)
    # Produk sold selalu di akhir daftar, urutan lain mengikuti pilihan
    order = f" ORDER BY (p.status = 'sold'), {SORTS[f['urut']]}, p.id"
    products = db.execute(BASE_SELECT + where + order + " LIMIT ? OFFSET ?",
                          params + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()

    # Opsi filter (dari semua produk)
    cats = db.execute("SELECT name, slug FROM categories ORDER BY id").fetchall()
    brands = db.execute("""SELECT b.name, b.slug, COUNT(p.id) AS n FROM brands b
                           JOIN products p ON p.brand_id = b.id GROUP BY b.id ORDER BY b.name""").fetchall()
    sizes = [r[0] for r in db.execute("SELECT DISTINCT size FROM products WHERE size IS NOT NULL ORDER BY size")]
    colors = sorted({(r[0] or "").split(" ")[0] for r in db.execute("SELECT DISTINCT color FROM products")} - {""})

    # Chip filter aktif: (label, url untuk menghapusnya)
    chips = []
    args = request.args
    name_of = {b["slug"]: b["name"] for b in brands}
    cat_name = {c["slug"]: c["name"] for c in cats}
    if f["q"]: chips.append((f"Cari: {f['q']}", url_with(args, q=None, hal=None)))
    if f["kategori"]: chips.append((cat_name.get(f["kategori"], f["kategori"]), url_with(args, kategori=None, hal=None)))
    for key, label in (("brand", None), ("ukuran", None), ("warna", None)):
        for v in f[key]:
            rest = [x for x in f[key] if x != v]
            chips.append((name_of.get(v, v) if key == "brand" else v, url_with(args, **{key: rest or None, "hal": None})))
    if f["gender"]: chips.append((f["gender"].capitalize(), url_with(args, gender=None, hal=None)))
    if f["kondisi"]: chips.append((f"Kondisi {f['kondisi']}/10 ke atas", url_with(args, kondisi=None, hal=None)))
    if f["harga_min"] is not None: chips.append((f"Min Rp {f['harga_min']:,}".replace(",", "."), url_with(args, harga_min=None, hal=None)))
    if f["harga_max"] is not None: chips.append((f"Maks Rp {f['harga_max']:,}".replace(",", "."), url_with(args, harga_max=None, hal=None)))

    heading = "Semua Produk"
    if f["kategori"] and f["kategori"] in cat_name:
        heading = cat_name[f["kategori"]]
    if f["q"]:
        heading = f"Hasil untuk \u201c{f['q']}\u201d"
    return render_template("catalog.html", products=products, total=total, page=page, pages=pages,
                           page_nums=_page_window(page, pages), f=f, cats=cats, brands=brands, sizes=sizes,
                           colors=colors, chips=chips, heading=heading, sorts=SORT_LABELS)


@bp.route("/api/cari-saran")
def search_suggest():
    """Saran pencarian otomatis (JSON): produk dan brand yang cocok."""
    q = request.args.get("q", "").strip()[:60]
    if len(q) < 2:
        return jsonify([])
    like = "%" + like_escape(q) + "%"
    db = database.get_db()
    out = []
    for b in db.execute("SELECT name, slug FROM brands WHERE name LIKE ? ESCAPE '\\' ORDER BY name LIMIT 2", (like,)):
        out.append({"label": b["name"], "type": "Brand", "url": "/katalog?brand=" + quote(b["slug"])})
    rows = db.execute("""SELECT p.name, p.slug, p.price, p.status, b.name AS brand FROM products p
                         JOIN brands b ON b.id = p.brand_id
                         WHERE p.name LIKE ? ESCAPE '\\' OR b.name LIKE ? ESCAPE '\\'
                         ORDER BY (p.status='sold'), p.views DESC LIMIT 6""", (like, like)).fetchall()
    for r in rows:
        out.append({"label": f"{r['brand']} {r['name']}", "type": "Sold out" if r["status"] == "sold" else
                    "Rp " + f"{r['price']:,}".replace(",", "."), "url": "/produk/" + r["slug"]})
    return jsonify(out)


# ------------------------------------------------------------ Halaman produk
@bp.route("/produk/<slug>")
def product(slug):
    db = database.get_db()
    p = db.execute(BASE_SELECT + " WHERE p.slug = ?", (slug,)).fetchone()
    if p is None:
        abort(404)

    # Hitung tayangan sekali per session; catat "baru dilihat"
    counted = session.get("viewed", [])
    if p["id"] not in counted:
        db.execute("UPDATE products SET views = views + 1 WHERE id = ?", (p["id"],))
        db.commit()
        session["viewed"] = (counted + [p["id"]])[-50:]
    recent_ids = [i for i in session.get("recent", []) if i != p["id"]]
    session["recent"] = ([p["id"]] + recent_ids)[:8]

    images = db.execute("SELECT url FROM product_images WHERE product_id=? ORDER BY is_primary DESC, sort_order",
                        (p["id"],)).fetchall()
    reviews = db.execute("""SELECT * FROM reviews WHERE product_id=? AND is_approved=1
                            ORDER BY created_at DESC""", (p["id"],)).fetchall()
    rating = round(sum(r["rating"] for r in reviews) / len(reviews), 1) if reviews else 0

    related = db.execute(BASE_SELECT + """ WHERE p.id != ? AND p.status != 'sold'
        ORDER BY (p.brand_id = ?) + (p.category_id = ?) DESC, p.views DESC LIMIT 4""",
                         (p["id"], p["brand_id"], p["category_id"])).fetchall()
    recent = []
    if recent_ids:
        marks = ",".join("?" * len(recent_ids))
        by_id = {r["id"]: r for r in db.execute(f"{BASE_SELECT} WHERE p.id IN ({marks})", recent_ids)}
        recent = [by_id[i] for i in recent_ids if i in by_id][:4]

    hold = None
    if p["status"] == "held":
        hold = db.execute("SELECT expires_at FROM holds WHERE product_id=? AND status='active' "
                          "ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()

    discount = round((1 - p["price"] / p["original_price"]) * 100) if p["original_price"] and p["original_price"] > p["price"] else 0
    wa_text = f"Halo Second Luxe, saya tertarik dengan {p['brand_name']} {p['name']} ({p['certificate_no']}). {request.base_url}"
    json_ld = {
        "@context": "https://schema.org", "@type": "Product", "name": f"{p['brand_name']} {p['name']}",
        "description": p["description"], "brand": {"@type": "Brand", "name": p["brand_name"]},
        "sku": p["certificate_no"], "itemCondition": "https://schema.org/UsedCondition",
        "offers": {"@type": "Offer", "priceCurrency": "IDR", "price": p["price"],
                   "availability": "https://schema.org/InStock" if p["status"] == "available" else "https://schema.org/OutOfStock"},
    }
    if reviews:
        json_ld["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": rating, "reviewCount": len(reviews)}
    return render_template(
        "product.html", p=p, images=images, reviews=reviews, rating=rating, related=related, recent=recent,
        hold=hold, discount=discount, completeness=[c.strip() for c in (p["completeness"] or "").split(",") if c.strip()],
        wa_link=f"https://wa.me/{current_app.config['WHATSAPP_NUMBER']}?text={quote(wa_text)}",
        json_ld=json_ld, review_summary=None)   # review_summary diisi AI pada Tahap 5
