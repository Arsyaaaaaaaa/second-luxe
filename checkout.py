"""Blueprint checkout: alamat, kurir, voucher, pembayaran simulasi, pelacakan.

Pembayaran hanya SIMULASI. Data kartu tidak pernah disimpan atau dicatat; hanya divalidasi
formatnya lalu dibuang. Produk berstatus sold lewat UPDATE atomik di dalam transaksi
(BEGIN IMMEDIATE) sehingga dua pembeli tidak bisa memiliki produk yang sama.
"""
import random
import re
from datetime import datetime

from flask import (Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   session)

import db as database
import shipping
from cart import load_cart
from utils import (EMAIL_RE, clean, client_ip, rate_exceeded, rate_hit, rupiah, send_email_simulated,
                   valid_phone)

bp = Blueprint("checkout", __name__)

PAYMENTS = {
    "transfer": {"label": "Transfer Bank", "months": 0, "card": False,
                 "desc": "Virtual account BCA, Mandiri, atau BNI"},
    "ewallet": {"label": "E-wallet", "months": 0, "card": False,
                "desc": "GoPay, OVO, DANA, atau ShopeePay"},
    "qris": {"label": "QRIS", "months": 0, "card": False,
             "desc": "Scan dengan aplikasi bank atau e-wallet apa pun"},
    "kartu": {"label": "Kartu Kredit", "months": 0, "card": True,
              "desc": "Visa atau Mastercard (simulasi)"},
    "cicilan3": {"label": "Cicilan 3 bulan", "months": 3, "card": True,
                 "desc": "Kartu kredit, bunga 0%, minimal belanja Rp 1.000.000"},
    "cicilan6": {"label": "Cicilan 6 bulan", "months": 6, "card": True,
                 "desc": "Kartu kredit, bunga 0%, minimal belanja Rp 1.000.000"},
}
MIN_INSTALLMENT = 1_000_000

# UPDATE atomik: hanya berhasil jika produk masih tersedia (atau di-hold oleh pembeli ini)
CLAIM_SQL = """
UPDATE products SET status='sold', stock=0, sold_count=sold_count+1
WHERE id=? AND stock>0 AND (
  status='available' OR (status='held' AND EXISTS (
    SELECT 1 FROM holds h WHERE h.product_id=products.id AND h.user_id=? AND h.status='active')))
"""


# ------------------------------------------------------------------ Voucher
def check_voucher(db, code, subtotal):
    """Validasi voucher. Kembalikan (baris voucher, potongan, pesan error)."""
    code = clean(code, 30).upper()
    if not code:
        return None, 0, None
    v = db.execute("SELECT * FROM vouchers WHERE code=?", (code,)).fetchone()
    if v is None or not v["is_active"]:
        return None, 0, "Kode voucher tidak ditemukan."
    now = database.now_str()
    if v["valid_from"] and now < v["valid_from"]:
        return None, 0, "Voucher ini belum berlaku."
    if v["valid_until"] and now > v["valid_until"]:
        return None, 0, "Voucher ini sudah kedaluwarsa."
    if v["used"] >= v["quota"]:
        return None, 0, "Kuota voucher ini sudah habis."
    if subtotal < v["min_purchase"]:
        return None, 0, f"Minimal belanja {rupiah(v['min_purchase'])} untuk voucher ini."
    if v["type"] == "percent":
        discount = subtotal * v["value"] // 100
        if v["max_discount"]:
            discount = min(discount, v["max_discount"])
    else:
        discount = v["value"]
    return v, min(discount, subtotal), None


# ------------------------------------------------------------ Akses pesanan
def can_access(order):
    """Pemilik akun, atau tamu yang membuat pesanan pada session ini."""
    if g.user and order["user_id"] == g.user["id"]:
        return True
    return order["order_no"] in session.get("guest_orders", [])


def get_order_or_404(order_no):
    order = database.get_db().execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if order is None or not can_access(order):
        abort(404)
    return order


def order_parts(db, order_id):
    items = db.execute("""SELECT oi.*, p.slug,
        (SELECT url FROM product_images i WHERE i.product_id=p.id ORDER BY i.is_primary DESC, i.sort_order LIMIT 1) AS image_url
        FROM order_items oi JOIN products p ON p.id=oi.product_id WHERE oi.order_id=?""", (order_id,)).fetchall()
    events = db.execute("SELECT * FROM order_events WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    return items, events


def add_event(db, order_id, status, note):
    db.execute("INSERT INTO order_events (order_id,status,note) VALUES (?,?,?)", (order_id, status, note))


# ----------------------------------------------------------- Form checkout
def default_form():
    """Nilai awal form: alamat utama pengguna (jika login)."""
    f = {"email": "", "recipient": "", "phone": "", "street": "", "city": "", "province": "",
         "postal_code": "", "courier": "", "voucher": "", "payment": "transfer", "save_address": ""}
    if g.user:
        db = database.get_db()
        u = db.execute("SELECT name,email,phone FROM users WHERE id=?", (g.user["id"],)).fetchone()
        a = db.execute("SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC, id LIMIT 1",
                       (g.user["id"],)).fetchone()
        f.update(email=u["email"], recipient=u["name"], phone=u["phone"] or "")
        if a:
            f.update(recipient=a["recipient"], phone=a["phone"], street=a["street"], city=a["city"],
                     province=a["province"], postal_code=a["postal_code"])
    return f


def read_form():
    """Baca dan validasi form checkout. Kembalikan (data, errors)."""
    r = request.form
    d = {
        "email": clean(r.get("email"), 254).lower(),
        "recipient": clean(r.get("recipient"), 80),
        "phone": clean(r.get("phone"), 20),
        "street": clean(r.get("street"), 200),
        "city": clean(r.get("city"), 60),
        "province": clean(r.get("province"), 60),
        "postal_code": clean(r.get("postal_code"), 10),
        "courier": clean(r.get("courier"), 20),
        "voucher": clean(r.get("voucher"), 30).upper(),
        "payment": clean(r.get("payment"), 20),
        "save_address": "1" if r.get("save_address") else "",
    }
    e = {}
    if g.user:
        d["email"] = database.get_db().execute("SELECT email FROM users WHERE id=?", (g.user["id"],)).fetchone()[0]
    elif not EMAIL_RE.match(d["email"]):
        e["email"] = "Masukkan email yang valid untuk konfirmasi pesanan."
    if len(d["recipient"]) < 2:
        e["recipient"] = "Nama penerima minimal 2 karakter."
    phone = valid_phone(d["phone"])
    if not phone:
        e["phone"] = "Nomor HP tidak valid (contoh 081234567890)."
    else:
        d["phone"] = phone
    if len(d["street"]) < 5:
        e["street"] = "Alamat lengkap minimal 5 karakter."
    if len(d["city"]) < 2:
        e["city"] = "Isi kota atau kabupaten."
    if d["province"] not in shipping.PROVINCES:
        e["province"] = "Pilih provinsi."
    if not re.fullmatch(r"\d{5}", d["postal_code"]):
        e["postal_code"] = "Kode pos harus 5 angka."
    if d["payment"] not in PAYMENTS:
        e["payment"] = "Pilih metode pembayaran."
    if "province" not in e:
        if d["courier"] not in shipping.COURIERS:
            e["courier"] = "Pilih kurir pengiriman."
        elif shipping.quote(d["province"], d["courier"]) is None:
            e["courier"] = "Kurir ini tidak melayani provinsi tujuan."
    return d, e


def money_for(db, d, subtotal):
    """Hitung ongkir, voucher, dan total dari data form."""
    q = shipping.quote(d["province"], d["courier"]) if d["province"] else None
    ship = q["cost"] if q else 0
    voucher, discount, verr = check_voucher(db, d["voucher"], subtotal)
    return ship, voucher, discount, verr, subtotal - discount + ship


# -------------------------------------------------------------- Halaman checkout
@bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    items = load_cart()
    if not items:
        flash("Keranjang masih kosong. Pilih produk dulu.", "warning")
        return redirect("/keranjang")
    if any(not i["ok"] for i in items):
        flash("Ada produk di keranjang yang sudah tidak tersedia. Hapus dulu sebelum checkout.", "error")
        return redirect("/keranjang")

    db = database.get_db()
    subtotal = sum(i["p"]["price"] for i in items)
    errors = {}
    form = default_form()

    if request.method == "POST":
        form, errors = read_form()
        ship, voucher, discount, verr, total = money_for(db, form, subtotal)
        if verr:
            errors["voucher"] = verr
        pay = PAYMENTS.get(form["payment"])
        if pay and pay["months"] and subtotal - discount < MIN_INSTALLMENT:
            errors["payment"] = f"Cicilan hanya untuk total belanja minimal {rupiah(MIN_INSTALLMENT)}."
        if not errors:
            order_no, problem = create_order(db, items, form, voucher, discount, ship, total, pay)
            if problem:
                flash(problem, "error")
                return redirect("/keranjang")
            if form["save_address"] and g.user:
                save_address_from_order(db, g.user["id"], form)
            flash("Pesanan dibuat. Selesaikan pembayaran untuk mengamankan produk.", "success")
            return redirect(f"/bayar/{order_no}")
        flash("Periksa kembali data yang ditandai.", "error")

    quotes = shipping.all_quotes(form["province"]) if form["province"] else {}
    ship, voucher, discount, verr, total = money_for(db, form, subtotal)
    addresses = []
    if g.user:
        addresses = db.execute("SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC, id",
                               (g.user["id"],)).fetchall()
    return render_template(
        "shop/checkout.html", items=items, subtotal=subtotal, form=form, errors=errors,
        provinces=sorted(shipping.PROVINCES), couriers=shipping.COURIERS, quotes=quotes,
        payments=PAYMENTS, addresses=addresses, ship=ship, discount=discount, total=total,
        min_installment=MIN_INSTALLMENT)


def save_address_from_order(db, user_id, d):
    """Simpan alamat checkout ke buku alamat jika belum ada yang sama (maks. 10 alamat)."""
    same = db.execute("SELECT 1 FROM addresses WHERE user_id=? AND street=? AND postal_code=?",
                      (user_id, d["street"], d["postal_code"])).fetchone()
    count = db.execute("SELECT COUNT(*) FROM addresses WHERE user_id=?", (user_id,)).fetchone()[0]
    if same or count >= 10:
        return
    db.execute("""INSERT INTO addresses (user_id,label,recipient,phone,street,city,province,postal_code,is_default)
                  VALUES (?,?,?,?,?,?,?,?,?)""",
               (user_id, "Alamat checkout", d["recipient"], d["phone"], d["street"], d["city"],
                d["province"], d["postal_code"], 1 if count == 0 else 0))
    db.commit()


def create_order(db, items, d, voucher, discount, ship, total, pay):
    """Buat pesanan di dalam transaksi. Kembalikan (order_no, None) atau (None, teks error)."""
    uid = g.user["id"] if g.user else None
    ids = [i["p"]["id"] for i in items]
    db.commit()                          # tutup transaksi implisit sebelum BEGIN eksplisit
    db.execute("BEGIN IMMEDIATE")
    try:
        # Cek ulang ketersediaan di dalam transaksi
        for pid in ids:
            p = db.execute("SELECT name,status,stock FROM products WHERE id=?", (pid,)).fetchone()
            p = dict(p, id=pid)
            ok = p["stock"] > 0 and p["status"] != "sold"
            if ok and p["status"] == "held":
                ok = bool(uid and db.execute(
                    "SELECT 1 FROM holds WHERE product_id=? AND user_id=? AND status='active'",
                    (pid, uid)).fetchone())
            if not ok:
                db.rollback()
                return None, f"Maaf, {p['name']} baru saja tidak tersedia lagi."
        # Pakai satu kuota voucher (dikembalikan jika pesanan batal)
        if voucher is not None:
            cur = db.execute("UPDATE vouchers SET used=used+1 WHERE id=? AND used<quota", (voucher["id"],))
            if cur.rowcount != 1:
                db.rollback()
                return None, "Kuota voucher baru saja habis. Coba checkout lagi tanpa voucher."
        prefix = "SL-" + datetime.utcnow().strftime("%Y%m%d")
        seq = db.execute("SELECT COUNT(*) FROM orders WHERE order_no LIKE ?", (prefix + "-%",)).fetchone()[0] + 1
        order_no = f"{prefix}-{seq:04d}"
        subtotal = sum(i["p"]["price"] for i in items)
        cur = db.execute(
            """INSERT INTO orders (order_no,user_id,guest_email,recipient,phone,street,city,province,postal_code,
               courier,shipping_cost,subtotal,discount,voucher_code,total,payment_method,installment_months)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (order_no, uid, None if uid else d["email"], d["recipient"], d["phone"], d["street"], d["city"],
             d["province"], d["postal_code"], shipping.COURIERS[d["courier"]]["label"], ship, subtotal,
             discount, voucher["code"] if voucher else None, total, pay["label"], pay["months"]))
        oid = cur.lastrowid
        for i in items:
            db.execute("INSERT INTO order_items (order_id,product_id,product_name,price) VALUES (?,?,?,?)",
                       (oid, i["p"]["id"], f"{i['p']['brand_name']} {i['p']['name']}", i["p"]["price"]))
        add_event(db, oid, "menunggu_pembayaran", "Pesanan dibuat, menunggu pembayaran.")
        db.commit()
    except Exception:
        db.rollback()
        raise
    if not uid:
        session["guest_orders"] = (session.get("guest_orders", []) + [order_no])[-20:]
    return order_no, None


# ---------------------------------------------------------------- Pembatalan
def cancel_order(db, order, note):
    """Batalkan pesanan: kembalikan kuota voucher dan, jika sudah lunas, stok produk."""
    if order["status"] == "dibatalkan":
        return
    was_paid = order["payment_status"] == "paid"
    db.execute("UPDATE orders SET status='dibatalkan', payment_status=? WHERE id=?",
               ("paid" if was_paid else "failed", order["id"]))
    if order["voucher_code"]:
        db.execute("UPDATE vouchers SET used=MAX(used-1,0) WHERE code=?", (order["voucher_code"],))
    if was_paid:
        for it in db.execute("SELECT product_id FROM order_items WHERE order_id=?", (order["id"],)):
            db.execute("""UPDATE products SET status='available', stock=1, sold_count=MAX(sold_count-1,0)
                          WHERE id=? AND status='sold'""", (it["product_id"],))
    add_event(db, order["id"], "dibatalkan", note)
    db.commit()


# ----------------------------------------------------------------- Pembayaran
def luhn_ok(number):
    digits = [int(c) for c in number][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
    return total % 10 == 0


def validate_card(form):
    """Validasi format kartu (tanpa menyimpan apa pun). Kembalikan dict error."""
    e = {}
    number = re.sub(r"[\s\-]", "", form.get("card_number", ""))
    if not (re.fullmatch(r"\d{13,19}", number) and luhn_ok(number)):
        e["card_number"] = "Nomor kartu tidak valid. Untuk demo pakai 4242 4242 4242 4242."
    if len(clean(form.get("card_name"), 60)) < 2:
        e["card_name"] = "Isi nama pada kartu."
    m = re.fullmatch(r"(\d{2})\s*/\s*(\d{2})", form.get("card_exp", "").strip())
    now = datetime.utcnow()
    if not m or not 1 <= int(m.group(1)) <= 12 or (2000 + int(m.group(2)), int(m.group(1))) < (now.year, now.month):
        e["card_exp"] = "Masa berlaku tidak valid (format BB/TT)."
    if not re.fullmatch(r"\d{3,4}", form.get("card_cvv", "").strip()):
        e["card_cvv"] = "CVV 3 atau 4 angka."
    return e


def pay_meta(order):
    """Info tampilan metode bayar: nomor VA, cicilan per bulan."""
    key = next((k for k, v in PAYMENTS.items() if v["label"] == order["payment_method"]), "transfer")
    digits = re.sub(r"\D", "", order["order_no"])
    return {"key": key, "info": PAYMENTS[key], "va": "8077" + digits.ljust(12, "0")[:12],
            "monthly": order["total"] // order["installment_months"] if order["installment_months"] else 0}


def settle_payment(db, order):
    """Tandai lunas secara atomik. Kembalikan (True, None) atau (False, daftar produk yang kalah rebutan)."""
    uid = g.user["id"] if g.user else None
    items = db.execute("SELECT product_id, product_name FROM order_items WHERE order_id=?", (order["id"],)).fetchall()
    database.release_expired_holds()
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    try:
        fresh = db.execute("SELECT status,payment_status FROM orders WHERE id=?", (order["id"],)).fetchone()
        if fresh["payment_status"] != "pending" or fresh["status"] != "menunggu_pembayaran":
            db.rollback()
            return False, None
        lost = []
        for it in items:
            if db.execute(CLAIM_SQL, (it["product_id"], uid)).rowcount != 1:
                lost.append(it["product_name"])
        if lost:
            db.rollback()
            return False, lost
        db.execute("UPDATE orders SET payment_status='paid', status='diproses', paid_at=? WHERE id=?",
                   (database.now_str(), order["id"]))
        add_event(db, order["id"], "diproses", f"Pembayaran via {order['payment_method']} diterima, pesanan diproses.")
        ids = [it["product_id"] for it in items]
        marks = ",".join("?" * len(ids))
        db.execute(f"UPDATE holds SET status='converted' WHERE status='active' AND product_id IN ({marks})", ids)
        db.execute(f"DELETE FROM cart_items WHERE product_id IN ({marks})", ids)   # produk sold hilang dari semua keranjang
        db.commit()
    except Exception:
        db.rollback()
        raise
    # Bersihkan keranjang tamu
    session["cart"] = [i for i in session.get("cart", []) if i not in ids]
    return True, None


def after_paid(db, order):
    """Email konfirmasi (simulasi) dan notifikasi akun."""
    items, _ = order_parts(db, order["id"])
    email = order["guest_email"]
    if order["user_id"]:
        email = db.execute("SELECT email FROM users WHERE id=?", (order["user_id"],)).fetchone()[0]
        db.execute("INSERT INTO notifications (user_id,kind,message,url) VALUES (?,?,?,?)",
                   (order["user_id"], "order", f"Pembayaran pesanan {order['order_no']} diterima.",
                    f"/akun/pesanan/{order['order_no']}"))
        db.commit()
    lines = "\n".join(f"- {i['product_name']}: {rupiah(i['price'])}" for i in items)
    body = (f"Halo {order['recipient']},\n\nTerima kasih, pembayaran pesanan {order['order_no']} sudah kami terima.\n\n"
            f"{lines}\n\nOngkir ({order['courier']}): {rupiah(order['shipping_cost'])}\n"
            f"Diskon: {rupiah(order['discount'])}\nTotal: {rupiah(order['total'])}\n\n"
            f"Dikirim ke: {order['street']}, {order['city']}, {order['province']} {order['postal_code']}\n"
            f"Lacak pesanan: /lacak\n\nSecond Luxe")
    if email:
        send_email_simulated(email, f"Konfirmasi pesanan {order['order_no']}", body)


@bp.route("/bayar/<order_no>", methods=["GET", "POST"])
def pay(order_no):
    db = database.get_db()
    order = get_order_or_404(order_no)
    if order["payment_status"] == "paid":
        return redirect(f"/pesanan/sukses/{order_no}")
    pending = order["status"] == "menunggu_pembayaran"
    errors = {}
    if request.method == "POST" and pending:
        action = request.form.get("aksi")
        if action == "batal":
            cancel_order(db, order, "Pesanan dibatalkan oleh pembeli.")
            flash("Pesanan dibatalkan.", "info")
            return redirect("/keranjang")
        meta = pay_meta(order)
        if meta["info"]["card"]:
            errors = validate_card(request.form)     # data kartu hanya divalidasi, tidak disimpan
        if not errors:
            ok, lost = settle_payment(db, order)
            if ok:
                after_paid(db, order)
                flash("Pembayaran berhasil (simulasi). Terima kasih!", "success")
                return redirect(f"/pesanan/sukses/{order_no}")
            if lost:
                fresh = db.execute("SELECT * FROM orders WHERE id=?", (order["id"],)).fetchone()
                cancel_order(db, fresh, "Produk sudah dibeli pembeli lain sebelum pembayaran selesai.")
                flash("Maaf, " + ", ".join(lost) + " sudah dibeli pembeli lain. Pesanan dibatalkan "
                      "dan tidak ada biaya yang ditagihkan.", "error")
                return redirect("/katalog")
            flash("Status pesanan sudah berubah. Muat ulang halaman.", "warning")
            return redirect(f"/bayar/{order_no}")
        flash("Periksa kembali data kartu.", "error")
        order = db.execute("SELECT * FROM orders WHERE id=?", (order["id"],)).fetchone()
    items, events = order_parts(db, order["id"])
    return render_template("shop/pay.html", order=order, items=items, meta=pay_meta(order),
                           errors=errors, pending=pending)


@bp.route("/pesanan/sukses/<order_no>")
def success(order_no):
    order = get_order_or_404(order_no)
    if order["payment_status"] != "paid":
        return redirect(f"/bayar/{order_no}")
    items, events = order_parts(database.get_db(), order["id"])
    return render_template("shop/success.html", order=order, items=items, guest=not g.user)


@bp.route("/qris/<order_no>.svg")
def qris(order_no):
    """QR dummy (bukan QRIS asli): pola acak yang selalu sama untuk satu nomor pesanan."""
    order = get_order_or_404(order_no)
    rnd = random.Random(order["order_no"])
    n, cell = 25, 8
    grid = [[rnd.random() < 0.48 for _ in range(n)] for _ in range(n)]

    def finder(x, y):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                gx, gy = x + dx, y + dy
                if 0 <= gx < n and 0 <= gy < n:
                    on = 0 <= dx <= 6 and 0 <= dy <= 6 and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
                    grid[gy][gx] = on
    finder(0, 0)
    finder(n - 7, 0)
    finder(0, n - 7)
    rects = "".join(f'<rect x="{x * cell}" y="{y * cell}" width="{cell}" height="{cell}"/>'
                    for y in range(n) for x in range(n) if grid[y][x])
    size = n * cell
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" role="img" '
           f'aria-label="QR dummy untuk simulasi"><rect width="{size}" height="{size}" fill="#fff"/>'
           f'<g fill="#1A1A1A">{rects}</g></svg>')
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


# ----------------------------------------------------------------- Pelacakan
@bp.route("/lacak")
def track():
    """Cari pesanan memakai nomor pesanan + email (tanpa perlu login)."""
    no = clean(request.args.get("no"), 30).upper()
    email = clean(request.args.get("email"), 254).lower()
    if g.user and not email:
        email = database.get_db().execute("SELECT email FROM users WHERE id=?", (g.user["id"],)).fetchone()[0]
    order = items = events = None
    if no:
        key = f"track:{client_ip()}"
        if rate_exceeded(key, 15):
            flash("Terlalu banyak pencarian. Coba lagi beberapa menit lagi.", "error")
        else:
            rate_hit(key)
            db = database.get_db()
            row = db.execute("""SELECT o.*, u.email AS user_email FROM orders o
                                LEFT JOIN users u ON u.id=o.user_id WHERE o.order_no=?""", (no,)).fetchone()
            owner_email = ((row["guest_email"] or row["user_email"] or "") if row else "").lower()
            if row and email and email == owner_email:
                order = row
                items, events = order_parts(db, row["id"])
            else:
                flash("Pesanan tidak ditemukan atau email tidak cocok.", "error")
    return render_template("shop/track.html", no=no, email=email, order=order, items=items, events=events)


# ------------------------------------------------------------------ API JSON
@bp.route("/api/ongkir")
def api_shipping():
    prov = clean(request.args.get("provinsi"), 60)
    return jsonify({code: q for code, q in shipping.all_quotes(prov).items()})


@bp.post("/api/voucher")
def api_voucher():
    if rate_exceeded(f"voucher:{client_ip()}", 30):
        return jsonify({"ok": False, "message": "Terlalu banyak percobaan. Coba lagi nanti."}), 429
    rate_hit(f"voucher:{client_ip()}")
    data = request.get_json(silent=True) or {}
    items = load_cart()
    subtotal = sum(i["p"]["price"] for i in items if i["ok"])
    v, discount, err = check_voucher(database.get_db(), str(data.get("code", "")), subtotal)
    if err or v is None:
        return jsonify({"ok": False, "message": err or "Masukkan kode voucher.", "discount": 0})
    return jsonify({"ok": True, "message": f"Voucher {v['code']} dipakai: hemat {rupiah(discount)}.",
                    "discount": discount})
