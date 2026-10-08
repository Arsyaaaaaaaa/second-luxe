"""Blueprint akun: profil, kata sandi, buku alamat, riwayat pesanan, notifikasi."""
import re

from flask import (Blueprint, abort, flash, g, redirect, render_template, request)
from werkzeug.security import check_password_hash, generate_password_hash

import db as database
import shipping
from checkout import add_event, order_parts
from utils import (clean, client_ip, login_required, rate_exceeded, rate_hit, rate_reset, rupiah,
                   valid_phone)

bp = Blueprint("account", __name__)
MAX_ADDRESSES = 10


@bp.app_context_processor
def unread_notifications():
    """Jumlah notifikasi belum dibaca (untuk navbar dan menu akun)."""
    if g.get("user") is None:
        return {"unread_notif": 0}
    n = database.get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
                                  (g.user["id"],)).fetchone()[0]
    return {"unread_notif": n}


def sync_notifications(db, user_id):
    """Buat notifikasi dari wishlist: harga turun, dan stok tinggal 1 unit (tanpa duplikat)."""
    rows = db.execute("""SELECT w.id AS wid, w.price_when_added, p.name, p.slug, p.price, p.status, p.stock,
                                b.name AS brand
                         FROM wishlist w JOIN products p ON p.id=w.product_id JOIN brands b ON b.id=p.brand_id
                         WHERE w.user_id=?""", (user_id,)).fetchall()
    for r in rows:
        if r["status"] == "sold":
            continue
        url = "/produk/" + r["slug"]
        title = f"{r['brand']} {r['name']}"
        if r["price_when_added"] and r["price"] < r["price_when_added"]:
            db.execute("INSERT INTO notifications (user_id,kind,message,url) VALUES (?,?,?,?)",
                       (user_id, "price_drop",
                        f"Harga {title} turun dari {rupiah(r['price_when_added'])} menjadi {rupiah(r['price'])}.", url))
            db.execute("UPDATE wishlist SET price_when_added=? WHERE id=?", (r["price"], r["wid"]))
        if r["status"] == "available" and r["stock"] <= 1:
            seen = db.execute("SELECT 1 FROM notifications WHERE user_id=? AND kind='low_stock' AND url=?",
                              (user_id, url)).fetchone()
            if not seen:
                db.execute("INSERT INTO notifications (user_id,kind,message,url) VALUES (?,?,?,?)",
                           (user_id, "low_stock",
                            f"{title} di wishlistmu tinggal 1 unit. Amankan sebelum diambil pembeli lain.", url))
    db.commit()


# --------------------------------------------------------------------- Profil
@bp.route("/akun", methods=["GET", "POST"])
@login_required
def profile():
    db = database.get_db()
    u = db.execute("SELECT id,name,email,phone,created_at FROM users WHERE id=?", (g.user["id"],)).fetchone()
    errors = {}
    form = {"name": u["name"], "phone": u["phone"] or ""}
    if request.method == "POST":
        form = {"name": clean(request.form.get("name"), 80), "phone": clean(request.form.get("phone"), 20)}
        if len(form["name"]) < 2:
            errors["name"] = "Nama minimal 2 karakter."
        phone = valid_phone(form["phone"]) if form["phone"] else None
        if form["phone"] and not phone:
            errors["phone"] = "Nomor HP tidak valid (contoh 081234567890)."
        if not errors:
            db.execute("UPDATE users SET name=?, phone=? WHERE id=?", (form["name"], phone, u["id"]))
            db.commit()
            flash("Profil diperbarui.", "success")
            return redirect("/akun")
        flash("Periksa kembali data yang ditandai.", "error")
    stats = {
        "orders": db.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (u["id"],)).fetchone()[0],
        "wishlist": db.execute("SELECT COUNT(*) FROM wishlist WHERE user_id=?", (u["id"],)).fetchone()[0],
    }
    return render_template("account/profile.html", u=u, form=form, errors=errors, stats=stats, active="profil")


@bp.post("/akun/sandi")
@login_required
def change_password():
    from auth import password_error
    db = database.get_db()
    key = f"pwchange:{g.user['id']}"
    if rate_exceeded(key, 5):
        flash("Terlalu banyak percobaan. Coba lagi sekitar 10 menit lagi.", "error")
        return redirect("/akun")
    cur_pw = (request.form.get("current") or "")[:128]
    new_pw = (request.form.get("new") or "")[:128]
    confirm = (request.form.get("confirm") or "")[:128]
    row = db.execute("SELECT password_hash FROM users WHERE id=?", (g.user["id"],)).fetchone()
    err = None
    if not check_password_hash(row["password_hash"], cur_pw):
        err = "Kata sandi saat ini salah."
        rate_hit(key)
    else:
        err = password_error(new_pw) or (None if new_pw == confirm else "Konfirmasi kata sandi tidak sama.")
    if err:
        flash(err, "error")
    else:
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_pw), g.user["id"]))
        db.commit()
        rate_reset(key)
        flash("Kata sandi berhasil diganti.", "success")
    return redirect("/akun")


# --------------------------------------------------------------------- Alamat
def read_address():
    r = request.form
    d = {"label": clean(r.get("label"), 30) or "Rumah", "recipient": clean(r.get("recipient"), 80),
         "phone": clean(r.get("phone"), 20), "street": clean(r.get("street"), 200),
         "city": clean(r.get("city"), 60), "province": clean(r.get("province"), 60),
         "postal_code": clean(r.get("postal_code"), 10), "is_default": 1 if r.get("is_default") else 0}
    e = {}
    if len(d["recipient"]) < 2:
        e["recipient"] = "Nama penerima minimal 2 karakter."
    phone = valid_phone(d["phone"])
    if phone:
        d["phone"] = phone
    else:
        e["phone"] = "Nomor HP tidak valid (contoh 081234567890)."
    if len(d["street"]) < 5:
        e["street"] = "Alamat lengkap minimal 5 karakter."
    if len(d["city"]) < 2:
        e["city"] = "Isi kota atau kabupaten."
    if d["province"] not in shipping.PROVINCES:
        e["province"] = "Pilih provinsi."
    if not re.fullmatch(r"\d{5}", d["postal_code"]):
        e["postal_code"] = "Kode pos harus 5 angka."
    return d, e


def own_address(db, aid):
    a = db.execute("SELECT * FROM addresses WHERE id=? AND user_id=?", (aid, g.user["id"])).fetchone()
    if a is None:
        abort(404)
    return a


@bp.route("/akun/alamat")
@login_required
def addresses():
    rows = database.get_db().execute("SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC, id",
                                     (g.user["id"],)).fetchall()
    return render_template("account/addresses.html", rows=rows, active="alamat", max_addresses=MAX_ADDRESSES)


@bp.route("/akun/alamat/baru", methods=["GET", "POST"])
@bp.route("/akun/alamat/<int:aid>/edit", methods=["GET", "POST"])
@login_required
def address_form(aid=None):
    db = database.get_db()
    existing = own_address(db, aid) if aid else None
    count = db.execute("SELECT COUNT(*) FROM addresses WHERE user_id=?", (g.user["id"],)).fetchone()[0]
    if not existing and count >= MAX_ADDRESSES:
        flash(f"Maksimal {MAX_ADDRESSES} alamat. Hapus salah satu dulu.", "warning")
        return redirect("/akun/alamat")
    form = dict(existing) if existing else {"label": "Rumah", "recipient": g.user["name"], "phone": "", "street": "",
                                            "city": "", "province": "", "postal_code": "",
                                            "is_default": 1 if count == 0 else 0}
    errors = {}
    if request.method == "POST":
        form, errors = read_address()
        if not errors:
            if form["is_default"] or count == 0:
                db.execute("UPDATE addresses SET is_default=0 WHERE user_id=?", (g.user["id"],))
                form["is_default"] = 1
            if existing:
                db.execute("""UPDATE addresses SET label=?,recipient=?,phone=?,street=?,city=?,province=?,
                              postal_code=?,is_default=? WHERE id=? AND user_id=?""",
                           (form["label"], form["recipient"], form["phone"], form["street"], form["city"],
                            form["province"], form["postal_code"], form["is_default"], aid, g.user["id"]))
            else:
                db.execute("""INSERT INTO addresses (user_id,label,recipient,phone,street,city,province,postal_code,is_default)
                              VALUES (?,?,?,?,?,?,?,?,?)""",
                           (g.user["id"], form["label"], form["recipient"], form["phone"], form["street"],
                            form["city"], form["province"], form["postal_code"], form["is_default"]))
            db.commit()
            flash("Alamat disimpan.", "success")
            return redirect("/akun/alamat")
        flash("Periksa kembali data yang ditandai.", "error")
    return render_template("account/address_form.html", form=form, errors=errors, editing=bool(existing),
                           provinces=sorted(shipping.PROVINCES), aid=aid, active="alamat")


@bp.post("/akun/alamat/<int:aid>/hapus")
@login_required
def address_delete(aid):
    db = database.get_db()
    a = own_address(db, aid)
    db.execute("DELETE FROM addresses WHERE id=?", (aid,))
    if a["is_default"]:                                   # alamat lain naik jadi utama
        db.execute("""UPDATE addresses SET is_default=1 WHERE id=(
                      SELECT id FROM addresses WHERE user_id=? ORDER BY id LIMIT 1)""", (g.user["id"],))
    db.commit()
    flash("Alamat dihapus.", "info")
    return redirect("/akun/alamat")


@bp.post("/akun/alamat/<int:aid>/utama")
@login_required
def address_default(aid):
    db = database.get_db()
    own_address(db, aid)
    db.execute("UPDATE addresses SET is_default=0 WHERE user_id=?", (g.user["id"],))
    db.execute("UPDATE addresses SET is_default=1 WHERE id=?", (aid,))
    db.commit()
    flash("Alamat utama diganti.", "success")
    return redirect("/akun/alamat")


# -------------------------------------------------------------------- Pesanan
@bp.route("/akun/pesanan")
@login_required
def orders():
    rows = database.get_db().execute("""
        SELECT o.*, (SELECT GROUP_CONCAT(product_name, ', ') FROM order_items WHERE order_id=o.id) AS names,
               (SELECT COUNT(*) FROM order_items WHERE order_id=o.id) AS n_items
        FROM orders o WHERE o.user_id=? ORDER BY o.id DESC""", (g.user["id"],)).fetchall()
    return render_template("account/orders.html", rows=rows, active="pesanan")


@bp.route("/akun/pesanan/<order_no>")
@login_required
def order_detail(order_no):
    db = database.get_db()
    order = db.execute("SELECT * FROM orders WHERE order_no=? AND user_id=?", (order_no, g.user["id"])).fetchone()
    if order is None:
        abort(404)
    items, events = order_parts(db, order["id"])
    return render_template("account/order.html", order=order, items=items, events=events, active="pesanan")


@bp.post("/akun/pesanan/<order_no>/selesai")
@login_required
def order_received(order_no):
    """Pembeli mengonfirmasi barang diterima: dikirim -> selesai."""
    db = database.get_db()
    order = db.execute("SELECT * FROM orders WHERE order_no=? AND user_id=?", (order_no, g.user["id"])).fetchone()
    if order is None:
        abort(404)
    if order["status"] != "dikirim":
        flash("Pesanan hanya bisa diselesaikan setelah berstatus dikirim.", "warning")
    else:
        db.execute("UPDATE orders SET status='selesai' WHERE id=?", (order["id"],))
        add_event(db, order["id"], "selesai", "Pembeli mengonfirmasi pesanan diterima.")
        db.commit()
        flash("Terima kasih! Pesanan ditandai selesai.", "success")
    return redirect(f"/akun/pesanan/{order_no}")


# ---------------------------------------------------------------- Notifikasi
@bp.route("/akun/notifikasi")
@login_required
def notifications():
    db = database.get_db()
    sync_notifications(db, g.user["id"])
    rows = db.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50",
                      (g.user["id"],)).fetchall()
    html = render_template("account/notifications.html", rows=rows, active="notifikasi")
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0", (g.user["id"],))
    db.commit()
    return html
