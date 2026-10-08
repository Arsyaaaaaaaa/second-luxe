"""Blueprint interaksi pembeli: Tawar Harga, Booking 24 Jam, dan Jual ke Kami.

Fungsi pembantu `resolve_offer` dan `make_sell_offer` dipakai panel admin (Tahap 6).
Harga hasil tawaran yang diterima dihormati saat checkout lewat cart.agreed_prices.
"""
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request)

import db as database
import valuation
from shop import BASE_SELECT
from utils import clean, client_ip, login_required, rate_exceeded, rate_hit, rupiah

bp = Blueprint("engage", __name__)

HOLD_HOURS = 24
MAX_ACTIVE_HOLDS = 2          # satu pembeli maksimal menahan 2 produk sekaligus
MIN_OFFER_RATIO = 0.6         # tawaran minimal 60% dari harga
MAX_PENDING_OFFERS = 5
MAX_PHOTOS = 5
PHOTO_MAX_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------- Pembantu
def parse_amount(raw):
    """'12.500.000' -> 12500000 (None jika kosong atau tidak masuk akal)."""
    digits = re.sub(r"\D", "", raw or "")
    return int(digits) if digits and len(digits) <= 12 else None


def notify(db, user_id, kind, message, url=None):
    db.execute("INSERT INTO notifications (user_id,kind,message,url) VALUES (?,?,?,?)",
               (user_id, kind, message, url))


def notify_admins(db, kind, message, url):
    for r in db.execute("SELECT id FROM users WHERE role='admin' AND is_active=1").fetchall():
        notify(db, r["id"], kind, message, url)


def time_left(expires_at):
    """Sisa waktu booking sebagai teks: '5 jam 20 menit'."""
    try:
        t = datetime.strptime(expires_at, database.FMT)
    except (TypeError, ValueError):
        return ""
    secs = int((t - datetime.utcnow()).total_seconds())
    if secs <= 0:
        return "habis"
    h, m = divmod(secs // 60, 60)
    return f"{h} jam {m} menit" if h else f"{m} menit"


def grant_hold(db, pid, uid):
    """Tahan produk 24 jam. UPDATE atomik: hanya berhasil bila produk masih available."""
    cur = db.execute("UPDATE products SET status='held' WHERE id=? AND status='available' AND stock>0", (pid,))
    if cur.rowcount != 1:
        return None
    expires = database.now_str(timedelta(hours=HOLD_HOURS))
    db.execute("INSERT INTO holds (product_id,user_id,expires_at,status) VALUES (?,?,?,'active')",
               (pid, uid, expires))
    return expires


def ensure_hold(db, pid, uid):
    """Pakai booking aktif milik user (diperpanjang 24 jam), atau buat baru."""
    mine = db.execute("SELECT id FROM holds WHERE product_id=? AND user_id=? AND status='active'",
                      (pid, uid)).fetchone()
    if mine:
        expires = database.now_str(timedelta(hours=HOLD_HOURS))
        db.execute("UPDATE holds SET expires_at=? WHERE id=?", (expires, mine["id"]))
        return expires
    return grant_hold(db, pid, uid)


def release_hold(db, hold):
    """Batalkan booking: produk kembali available bila tidak ada booking aktif lain."""
    db.execute("UPDATE holds SET status='cancelled' WHERE id=?", (hold["id"],))
    db.execute("UPDATE products SET status='available' WHERE id=? AND status='held' "
               "AND NOT EXISTS (SELECT 1 FROM holds WHERE product_id=? AND status='active')",
               (hold["product_id"], hold["product_id"]))


def get_product(pid):
    p = database.get_db().execute(BASE_SELECT + " WHERE p.id=?", (pid,)).fetchone()
    if p is None:
        abort(404)
    return p


def login_to(next_url):
    flash("Silakan masuk terlebih dahulu.", "info")
    return redirect("/masuk?next=" + quote(next_url, safe=""))


# ---------------------------------------------------------------- Tawar harga
@bp.route("/tawar/<int:pid>", methods=["GET", "POST"])
def make_offer(pid):
    p = get_product(pid)
    if g.user is None:
        return login_to(f"/tawar/{pid}")
    db = database.get_db()
    uid = g.user["id"]
    if p["status"] != "available":
        flash("Produk ini sedang tidak bisa ditawar (sudah terjual atau sedang dibooking).", "error")
        return redirect("/produk/" + p["slug"])

    min_amount = -(-int(p["price"] * MIN_OFFER_RATIO) // 1000) * 1000      # dibulatkan ke atas ribuan
    form, error = {"amount": "", "message": ""}, None
    if request.method == "POST":
        form = {"amount": clean(request.form.get("amount"), 20), "message": clean(request.form.get("message"), 300)}
        amount = parse_amount(form["amount"])
        pending = db.execute("SELECT COUNT(*) FROM offers WHERE user_id=? AND status='pending'", (uid,)).fetchone()[0]
        same = db.execute("SELECT 1 FROM offers WHERE user_id=? AND product_id=? AND status IN ('pending','countered')",
                          (uid, pid)).fetchone()
        if rate_exceeded(f"offer:{uid}", 10, 3600):
            error = "Terlalu banyak tawaran dalam satu jam. Coba lagi nanti."
        elif amount is None:
            error = "Masukkan nominal tawaran dalam angka."
        elif amount >= p["price"]:
            error = f"Tawaran harus di bawah harga produk ({rupiah(p['price'])}). Gunakan Beli Sekarang bila setuju."
        elif amount < min_amount:
            error = f"Tawaran minimal {rupiah(min_amount)} (60% dari harga) agar bisa kami proses."
        elif same:
            error = "Kamu masih punya tawaran aktif untuk produk ini. Tunggu jawaban admin dulu."
        elif pending >= MAX_PENDING_OFFERS:
            error = f"Maksimal {MAX_PENDING_OFFERS} tawaran menunggu sekaligus."
        if error is None:
            rate_hit(f"offer:{uid}", 3600)
            db.execute("INSERT INTO offers (product_id,user_id,amount,message) VALUES (?,?,?,?)",
                       (pid, uid, amount, form["message"] or None))
            notify_admins(db, "offer", f"Tawaran baru {rupiah(amount)} untuk {p['brand_name']} {p['name']}.",
                          "/admin/tawaran")
            db.commit()
            flash("Tawaranmu terkirim. Admin menjawab maksimal 1x24 jam, cek statusnya di akun.", "success")
            return redirect("/akun/tawaran")
        flash(error, "error")

    history = db.execute("SELECT * FROM offers WHERE user_id=? AND product_id=? ORDER BY id DESC LIMIT 5",
                         (uid, pid)).fetchall()
    return render_template("offers/offer_form.html", p=p, form=form, min_amount=min_amount, history=history)


def resolve_offer(db, offer_id, action, counter_amount=None, note=""):
    """Dipanggil admin: action = 'accept' | 'reject' | 'counter'. Kembalikan (berhasil, pesan)."""
    o = db.execute("""SELECT o.*, p.name, p.price, p.status AS pstatus, p.slug, b.name AS brand
                      FROM offers o JOIN products p ON p.id=o.product_id JOIN brands b ON b.id=p.brand_id
                      WHERE o.id=?""", (offer_id,)).fetchone()
    if o is None or o["status"] != "pending":
        return False, "Tawaran tidak ditemukan atau sudah diproses."
    note = clean(note, 300) or None
    title, url = f"{o['brand']} {o['name']}", "/akun/tawaran"

    if action == "reject":
        db.execute("UPDATE offers SET status='rejected', admin_note=? WHERE id=?", (note, offer_id))
        notify(db, o["user_id"], "offer", f"Tawaran untuk {title} belum bisa kami terima.", url)
    elif action == "counter":
        if counter_amount is None or not o["amount"] < counter_amount < o["price"]:
            return False, "Harga balasan harus di atas tawaran dan di bawah harga produk."
        db.execute("UPDATE offers SET status='countered', counter_amount=?, admin_note=? WHERE id=?",
                   (counter_amount, note, offer_id))
        notify(db, o["user_id"], "offer", f"Admin membalas tawaranmu untuk {title}: {rupiah(counter_amount)}.", url)
    elif action == "accept":
        if not ensure_hold(db, o["product_id"], o["user_id"]):
            return False, "Produk tidak tersedia (sudah terjual atau dibooking pembeli lain)."
        db.execute("UPDATE offers SET status='accepted', admin_note=? WHERE id=?", (note, offer_id))
        notify(db, o["user_id"], "offer",
               f"Tawaranmu untuk {title} diterima! Produk ditahan {HOLD_HOURS} jam, segera checkout.", url)
    else:
        return False, "Aksi tidak dikenal."
    db.commit()
    return True, "Tawaran diproses."


@bp.route("/akun/tawaran")
@login_required
def my_offers():
    db = database.get_db()
    uid = g.user["id"]
    offers = db.execute("""
        SELECT o.*, p.name, p.slug, p.price, p.status AS pstatus, b.name AS brand,
          (SELECT url FROM product_images i WHERE i.product_id=p.id ORDER BY i.is_primary DESC, i.sort_order LIMIT 1) AS image_url,
          (SELECT expires_at FROM holds h WHERE h.product_id=p.id AND h.user_id=o.user_id AND h.status='active'
             AND h.expires_at > ? ORDER BY h.id DESC LIMIT 1) AS hold_until
        FROM offers o JOIN products p ON p.id=o.product_id JOIN brands b ON b.id=p.brand_id
        WHERE o.user_id=? ORDER BY o.id DESC""", (database.now_str(), uid)).fetchall()
    holds = db.execute("""
        SELECT h.*, p.name, p.slug, p.price, b.name AS brand,
          (SELECT url FROM product_images i WHERE i.product_id=p.id ORDER BY i.is_primary DESC, i.sort_order LIMIT 1) AS image_url
        FROM holds h JOIN products p ON p.id=h.product_id JOIN brands b ON b.id=p.brand_id
        WHERE h.user_id=? AND h.status='active' ORDER BY h.expires_at""", (uid,)).fetchall()
    holds = [dict(h, left=time_left(h["expires_at"])) for h in holds]
    return render_template("offers/my_offers.html", offers=offers, holds=holds, active="tawaran",
                           time_left=time_left)


def own_offer(db, oid):
    o = db.execute("SELECT * FROM offers WHERE id=? AND user_id=?", (oid, g.user["id"])).fetchone()
    if o is None:
        abort(404)
    return o


@bp.post("/akun/tawaran/<int:oid>/respon")
@login_required
def offer_respond(oid):
    """Pembeli menerima/menolak balasan admin, atau menarik tawaran yang masih menunggu."""
    db = database.get_db()
    o = own_offer(db, oid)
    action = request.form.get("aksi")
    if action == "tarik" and o["status"] == "pending":
        db.execute("UPDATE offers SET status='rejected', admin_note='Ditarik oleh pembeli' WHERE id=?", (oid,))
        flash("Tawaran ditarik.", "info")
    elif action == "tolak" and o["status"] == "countered":
        db.execute("UPDATE offers SET status='rejected' WHERE id=?", (oid,))
        flash("Balasan admin ditolak.", "info")
    elif action == "terima" and o["status"] == "countered":
        if ensure_hold(db, o["product_id"], g.user["id"]):
            # Harga kesepakatan = harga balasan admin
            db.execute("UPDATE offers SET status='accepted', amount=counter_amount WHERE id=?", (oid,))
            flash(f"Harga disepakati. Produk ditahan {HOLD_HOURS} jam, silakan checkout.", "success")
        else:
            flash("Maaf, produk sudah tidak tersedia.", "error")
    else:
        flash("Aksi tidak bisa dilakukan untuk tawaran ini.", "error")
    db.commit()
    return redirect("/akun/tawaran")


@bp.post("/akun/tawaran/<int:oid>/beli")
@login_required
def offer_buy(oid):
    """Masukkan produk dengan harga kesepakatan ke keranjang lalu ke checkout."""
    db = database.get_db()
    o = own_offer(db, oid)
    live = db.execute("SELECT 1 FROM holds WHERE product_id=? AND user_id=? AND status='active' AND expires_at > ?",
                      (o["product_id"], g.user["id"], database.now_str())).fetchone()
    if o["status"] != "accepted" or not live:
        flash("Tawaran ini sudah kedaluwarsa atau produk tidak lagi ditahan untukmu.", "error")
        return redirect("/akun/tawaran")
    db.execute("INSERT OR IGNORE INTO cart_items (user_id,product_id) VALUES (?,?)", (g.user["id"], o["product_id"]))
    db.commit()
    flash("Harga kesepakatan diterapkan di keranjang.", "success")
    return redirect("/checkout")


# ---------------------------------------------------------------- Booking 24 jam
@bp.post("/booking/<int:pid>")
def book(pid):
    p = get_product(pid)
    if g.user is None:
        return login_to("/produk/" + p["slug"])
    db = database.get_db()
    uid = g.user["id"]
    active = db.execute("SELECT COUNT(*) FROM holds WHERE user_id=? AND status='active'", (uid,)).fetchone()[0]
    if active >= MAX_ACTIVE_HOLDS:
        flash(f"Kamu sudah menahan {MAX_ACTIVE_HOLDS} produk. Selesaikan atau batalkan salah satu dulu.", "error")
    elif p["status"] != "available":
        flash("Produk ini sudah terjual atau sedang dibooking pembeli lain.", "error")
    else:
        expires = grant_hold(db, pid, uid)
        if expires is None:
            flash("Maaf, produk baru saja diambil pembeli lain.", "error")
        else:
            db.execute("INSERT OR IGNORE INTO cart_items (user_id,product_id) VALUES (?,?)", (uid, pid))
            db.commit()
            flash(f"Produk ditahan untukmu selama {HOLD_HOURS} jam dan sudah masuk keranjang. Selesaikan pembayaran sebelum waktunya habis.", "success")
    db.commit()
    return redirect("/produk/" + p["slug"])


@bp.post("/akun/booking/<int:hid>/batal")
@login_required
def cancel_hold(hid):
    db = database.get_db()
    h = db.execute("SELECT * FROM holds WHERE id=? AND user_id=? AND status='active'", (hid, g.user["id"])).fetchone()
    if h is None:
        abort(404)
    release_hold(db, h)
    db.commit()
    flash("Booking dibatalkan. Produk tersedia kembali untuk pembeli lain.", "info")
    return redirect("/akun/tawaran")


# ---------------------------------------------------------------- Jual ke Kami
def sniff_image(data):
    """Kenali tipe gambar dari byte awal (bukan dari nama file): jpg, png, atau webp."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def read_photos(files):
    """Validasi semua foto. Kembalikan ([(bytes, ext)], pesan_error)."""
    files = [f for f in files if f and f.filename]
    if not files:
        return [], "Unggah minimal 1 foto barang."
    if len(files) > MAX_PHOTOS:
        return [], f"Maksimal {MAX_PHOTOS} foto."
    out = []
    for f in files:
        data = f.read(PHOTO_MAX_BYTES + 1)
        if len(data) > PHOTO_MAX_BYTES:
            return [], "Ukuran tiap foto maksimal 5 MB."
        ext = sniff_image(data)
        if ext is None:
            return [], "Foto harus berformat JPG, PNG, atau WEBP yang valid."
        out.append((data, ext))
    return out, None


def store_photos(photos):
    """Simpan dengan nama acak (nama asli pengguna tidak dipakai sama sekali)."""
    folder = os.path.join(current_app.static_folder, "uploads", "sell")
    os.makedirs(folder, exist_ok=True)
    paths = []
    for data, ext in photos:
        name = secrets.token_hex(12) + ext
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)
        paths.append("/static/uploads/sell/" + name)
    return paths


def get_estimate(db, d):
    """Estimasi harga: pakai AI (ai.py, Tahap 5) bila ada, jika gagal pakai aturan."""
    args = dict(brand=d["brand"], item_name=d["item_name"], condition_key=d["condition"],
                expected_price=d["expected_price"], description=d["description"])
    try:
        from ai import estimate_sell_price      # ditambahkan pada Tahap 5
        result = estimate_sell_price(db, **args)
        if result and result.get("min") and result.get("max"):
            return result
    except Exception:                            # ImportError atau error API: lanjut ke aturan
        pass
    return valuation.rule_estimate(db, **args)


@bp.route("/jual", methods=["GET", "POST"])
def sell():
    db = database.get_db()
    brands = [r[0] for r in db.execute("SELECT name FROM brands ORDER BY name")]
    form, errors = {"brand": "", "item_name": "", "condition": "", "expected_price": "", "description": ""}, {}
    if request.method == "POST":
        if g.user is None:
            return login_to("/jual")
        r = request.form
        form = {"brand": clean(r.get("brand"), 40), "item_name": clean(r.get("item_name"), 100),
                "condition": r.get("condition", ""), "expected_price": clean(r.get("expected_price"), 20),
                "description": clean(r.get("description"), 1000)}
        price = parse_amount(form["expected_price"])
        if len(form["brand"]) < 2:
            errors["brand"] = "Isi nama brand."
        if len(form["item_name"]) < 3:
            errors["item_name"] = "Isi nama barang, misalnya Classic Flap Medium."
        if form["condition"] not in valuation.CONDITIONS:
            errors["condition"] = "Pilih kondisi barang."
        if price is None or not 100_000 <= price <= 500_000_000:
            errors["expected_price"] = "Harga harapan antara Rp 100.000 dan Rp 500.000.000."
        photos, photo_err = read_photos(request.files.getlist("photos"))
        if photo_err:
            errors["photos"] = photo_err
        if not errors and rate_exceeded(f"sell:{g.user['id']}", 5, 3600):
            errors["form"] = "Terlalu banyak pengajuan dalam satu jam. Coba lagi nanti."
        if not errors:
            rate_hit(f"sell:{g.user['id']}", 3600)
            paths = store_photos(photos)
            data = dict(form, expected_price=price)
            est = get_estimate(db, data)
            cur = db.execute(
                """INSERT INTO sell_requests (user_id,brand,item_name,condition_desc,expected_price,description,
                   photos,ai_min,ai_max,ai_notes) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (g.user["id"], form["brand"], form["item_name"], valuation.CONDITIONS[form["condition"]][0],
                 price, form["description"] or None, ",".join(paths), est["min"], est["max"],
                 clean(est.get("notes"), 1200)))
            notify_admins(db, "sell", f"Pengajuan Jual ke Kami baru: {form['brand']} {form['item_name']}.",
                          "/admin/jual")
            db.commit()
            flash("Pengajuan terkirim. Berikut estimasi awal; penawaran final menyusul setelah tim kami memeriksa.", "success")
            return redirect(f"/akun/jual/{cur.lastrowid}")
        flash(errors.get("form") or "Periksa kembali data yang ditandai.", "error")
    return render_template("sell/sell.html", form=form, errors=errors, brands=brands,
                           conditions=valuation.CONDITIONS, max_photos=MAX_PHOTOS)


@bp.route("/akun/jual")
@login_required
def my_sells():
    rows = database.get_db().execute("SELECT * FROM sell_requests WHERE user_id=? ORDER BY id DESC",
                                     (g.user["id"],)).fetchall()
    return render_template("sell/my_sells.html", rows=rows, active="jual")


def own_sell(db, sid):
    s = db.execute("SELECT * FROM sell_requests WHERE id=? AND user_id=?", (sid, g.user["id"])).fetchone()
    if s is None:
        abort(404)
    return s


@bp.route("/akun/jual/<int:sid>")
@login_required
def sell_detail(sid):
    s = own_sell(database.get_db(), sid)
    photos = [x for x in (s["photos"] or "").split(",") if x.startswith("/static/uploads/")]
    return render_template("sell/sell_detail.html", s=s, photos=photos, active="jual")


@bp.post("/akun/jual/<int:sid>/respon")
@login_required
def sell_respond(sid):
    db = database.get_db()
    s = own_sell(db, sid)
    action = request.form.get("aksi")
    if s["status"] != "offered" or action not in ("terima", "tolak"):
        flash("Aksi tidak bisa dilakukan untuk pengajuan ini.", "error")
        return redirect(f"/akun/jual/{sid}")
    new = "accepted" if action == "terima" else "rejected"
    db.execute("UPDATE sell_requests SET status=? WHERE id=?", (new, sid))
    notify_admins(db, "sell", f"Penawaran final {s['brand']} {s['item_name']} "
                  f"{'DITERIMA' if new == 'accepted' else 'ditolak'} penjual.", "/admin/jual")
    db.commit()
    flash("Terima kasih! Tim kami akan menghubungimu untuk pengiriman barang." if new == "accepted"
          else "Penawaran ditolak. Kamu bisa mengajukan barang lain kapan saja.", "success" if new == "accepted" else "info")
    return redirect(f"/akun/jual/{sid}")


def make_sell_offer(db, sid, final_offer, note=""):
    """Dipanggil admin: beri penawaran final. Kembalikan (berhasil, pesan)."""
    s = db.execute("SELECT * FROM sell_requests WHERE id=?", (sid,)).fetchone()
    if s is None or s["status"] not in ("pending", "offered"):
        return False, "Pengajuan tidak ditemukan atau sudah selesai."
    if not final_offer or final_offer < 50_000:
        return False, "Nominal penawaran tidak valid."
    db.execute("UPDATE sell_requests SET status='offered', final_offer=?, admin_note=? WHERE id=?",
               (final_offer, clean(note, 300) or None, sid))
    notify(db, s["user_id"], "sell",
           f"Penawaran final untuk {s['brand']} {s['item_name']}: {rupiah(final_offer)}.", f"/akun/jual/{sid}")
    db.commit()
    return True, "Penawaran dikirim."
