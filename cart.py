"""Blueprint wishlist dan keranjang.

Tamu memakai session; pengguna login memakai database. Saat login keduanya digabung.
Karena stok thrift hanya 1 unit, satu produk tidak boleh muncul dua kali di keranjang.
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session

import db as database
from shop import BASE_SELECT
from utils import redirect_back

bp = Blueprint("cart", __name__)


# ------------------------------------------------------------------ Helper
def cart_ids():
    """Daftar id produk di keranjang (urut tambah)."""
    if g.user:
        rows = database.get_db().execute(
            "SELECT product_id FROM cart_items WHERE user_id=? ORDER BY id", (g.user["id"],))
        return [r[0] for r in rows]
    return list(session.get("cart", []))


def wishlist_ids():
    if g.user:
        rows = database.get_db().execute(
            "SELECT product_id FROM wishlist WHERE user_id=? ORDER BY id DESC", (g.user["id"],))
        return [r[0] for r in rows]
    return list(session.get("wishlist", []))


def purchasable(p, user_id):
    """(bisa dibeli?, alasan). Produk 'held' hanya bisa dibeli oleh pemilik booking aktif."""
    if p["status"] == "sold" or p["stock"] <= 0:
        return False, "Produk ini sudah terjual."
    if p["status"] == "held":
        mine = None
        if user_id:
            mine = database.get_db().execute(
                "SELECT 1 FROM holds WHERE product_id=? AND user_id=? AND status='active'",
                (p["id"], user_id)).fetchone()
        if not mine:
            return False, "Produk ini sedang dibooking pembeli lain."
    return True, ""


def agreed_prices(user_id, ids):
    """{product_id: harga sepakat} dari tawaran 'accepted' yang booking-nya masih aktif."""
    if not user_id or not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = database.get_db().execute(
        f"""SELECT o.product_id, o.amount FROM offers o
            WHERE o.user_id=? AND o.status='accepted' AND o.product_id IN ({marks})
              AND EXISTS (SELECT 1 FROM holds h WHERE h.product_id=o.product_id AND h.user_id=o.user_id
                          AND h.status='active' AND h.expires_at > ?)
            ORDER BY o.id""", [user_id, *ids, database.now_str()]).fetchall()
    return {r[0]: r[1] for r in rows}


def load_cart():
    """Isi keranjang sebagai list {'p': produk, 'ok': bool, 'reason': str, 'agreed': bool}."""
    ids = cart_ids()
    if not ids:
        return []
    db = database.get_db()
    marks = ",".join("?" * len(ids))
    by_id = {r["id"]: r for r in db.execute(f"{BASE_SELECT} WHERE p.id IN ({marks})", ids)}
    uid = g.user["id"] if g.user else None
    agreed = agreed_prices(uid, ids)
    items = []
    for pid in ids:
        p = by_id.get(pid)
        if p is None:
            continue
        ok, reason = purchasable(p, uid)
        if pid in agreed:                    # harga hasil tawar menggantikan harga katalog
            p = dict(p, list_price=p["price"], price=agreed[pid])
        items.append({"p": p, "ok": ok, "reason": reason, "agreed": pid in agreed})
    return items


def merge_guest_data(user_id, guest_cart, guest_wish):
    """Gabungkan keranjang dan wishlist tamu ke akun saat login."""
    db = database.get_db()
    for pid in guest_cart:
        p = db.execute("SELECT id,status,stock FROM products WHERE id=?", (pid,)).fetchone()
        if p and purchasable(p, user_id)[0]:
            db.execute("INSERT OR IGNORE INTO cart_items (user_id,product_id) VALUES (?,?)", (user_id, pid))
    for pid in guest_wish:
        p = db.execute("SELECT id,price FROM products WHERE id=?", (pid,)).fetchone()
        if p:
            db.execute("INSERT OR IGNORE INTO wishlist (user_id,product_id,price_when_added) VALUES (?,?,?)",
                       (user_id, pid, p["price"]))
    db.commit()


# ---------------------------------------------------------------- Keranjang
@bp.route("/keranjang")
def view_cart():
    items = load_cart()
    subtotal = sum(i["p"]["price"] for i in items if i["ok"])
    blocked = sum(1 for i in items if not i["ok"])
    return render_template("shop/cart.html", items=items, subtotal=subtotal, blocked=blocked)


@bp.post("/keranjang/tambah/<int:pid>")
def cart_add(pid):
    db = database.get_db()
    p = db.execute("SELECT id,name,status,stock FROM products WHERE id=?", (pid,)).fetchone()
    if p is None:
        abort(404)
    uid = g.user["id"] if g.user else None
    ok, reason = purchasable(p, uid)
    if not ok:
        flash(reason, "error")
        return redirect_back("/katalog")

    go_checkout = request.form.get("next") == "checkout"
    if pid in cart_ids():
        flash("Produk ini sudah ada di keranjang (stok hanya 1 unit).", "info")
    else:
        if g.user:
            db.execute("INSERT OR IGNORE INTO cart_items (user_id,product_id) VALUES (?,?)", (uid, pid))
            db.commit()
        else:
            session["cart"] = session.get("cart", []) + [pid]
        flash(f"{p['name']} ditambahkan ke keranjang.", "success")
    if go_checkout:
        return redirect("/checkout")
    return redirect_back("/keranjang")


@bp.post("/keranjang/hapus/<int:pid>")
def cart_remove(pid):
    if g.user:
        db = database.get_db()
        db.execute("DELETE FROM cart_items WHERE user_id=? AND product_id=?", (g.user["id"], pid))
        db.commit()
    else:
        session["cart"] = [i for i in session.get("cart", []) if i != pid]
    flash("Produk dihapus dari keranjang.", "info")
    return redirect("/keranjang")


# ----------------------------------------------------------------- Wishlist
@bp.route("/wishlist")
def view_wishlist():
    ids = wishlist_ids()
    products, drops = [], {}
    if ids:
        db = database.get_db()
        marks = ",".join("?" * len(ids))
        by_id = {r["id"]: r for r in db.execute(f"{BASE_SELECT} WHERE p.id IN ({marks})", ids)}
        products = [by_id[i] for i in ids if i in by_id]
        if g.user:
            for r in db.execute("SELECT product_id, price_when_added FROM wishlist WHERE user_id=?", (g.user["id"],)):
                p = by_id.get(r["product_id"])
                if p and r["price_when_added"] and p["price"] < r["price_when_added"]:
                    drops[p["id"]] = r["price_when_added"]
    return render_template("shop/wishlist.html", products=products, drops=drops)


@bp.post("/wishlist/toggle/<int:pid>")
def wishlist_toggle(pid):
    db = database.get_db()
    p = db.execute("SELECT id,price FROM products WHERE id=?", (pid,)).fetchone()
    if p is None:
        abort(404)
    if g.user:
        exists = db.execute("SELECT 1 FROM wishlist WHERE user_id=? AND product_id=?",
                            (g.user["id"], pid)).fetchone()
        if exists:
            db.execute("DELETE FROM wishlist WHERE user_id=? AND product_id=?", (g.user["id"], pid))
        else:
            db.execute("INSERT INTO wishlist (user_id,product_id,price_when_added) VALUES (?,?,?)",
                       (g.user["id"], pid, p["price"]))
        db.commit()
        added = not exists
    else:
        wish = session.get("wishlist", [])
        added = pid not in wish
        session["wishlist"] = wish + [pid] if added else [i for i in wish if i != pid]
    flash("Disimpan ke wishlist." if added else "Dihapus dari wishlist.", "success" if added else "info")
    return redirect_back("/wishlist")
